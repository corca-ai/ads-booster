"""Single capability registry used to freeze planner-visible tool snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from ads_booster.contracts.agent_run import CapabilitySnapshot

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import datetime

    from ads_booster.contracts.tool_capability import ToolDescriptor
    from ads_booster.agent.core.ports import ToolAdapter


class ToolDescriptorFactory(Protocol):
    """Build the current descriptor for one stable capability version."""

    def __call__(self, *, now: datetime) -> ToolDescriptor: ...


@dataclass(frozen=True, slots=True)
class ToolRegistration:
    """Bind one stable capability version to execution and live description."""

    capability_id: str
    version: str
    adapter: ToolAdapter | None = field(repr=False)
    descriptor_factory: ToolDescriptorFactory = field(repr=False)

    @property
    def key(self) -> tuple[str, str]:
        return self.capability_id, self.version

    def descriptor(self, *, now: datetime) -> ToolDescriptor:
        descriptor = self.descriptor_factory(now=now)
        if (descriptor.capability_id, descriptor.version) != self.key:
            raise ValueError("tool_registration_descriptor_identity_mismatch")
        return descriptor


class ToolRegistrationCatalog(Protocol):
    """Return a complete, immutable registration bundle for installation."""

    def registrations(self) -> tuple[ToolRegistration, ...]: ...


class ToolCatalogProvider(Protocol):
    """Return the currently installed tool catalog for one decision boundary."""

    def descriptors(self, *, now: datetime) -> tuple[ToolDescriptor, ...]: ...


@dataclass(frozen=True, slots=True)
class CapabilityPolicy:
    allowed_capability_ids: tuple[str, ...] = ()
    denied_capability_ids: tuple[str, ...] = ()

    def permits(self, capability_id: str) -> bool:
        return capability_id not in self.denied_capability_ids and (
            not self.allowed_capability_ids or capability_id in self.allowed_capability_ids
        )


class ToolRegistry:
    def __init__(
        self,
        descriptors: Iterable[ToolDescriptor],
        *,
        provider: ToolCatalogProvider | None = None,
        registrations: tuple[ToolRegistration, ...] | None = None,
    ) -> None:
        ordered = _validated_descriptors(descriptors)
        self._descriptors: tuple[ToolDescriptor, ...] = ordered
        self._provider: ToolCatalogProvider | None = provider
        self._registrations: tuple[ToolRegistration, ...] | None = registrations
        self._adapters: Mapping[str, ToolAdapter] = MappingProxyType(
            _registration_adapters(registrations or ())
        )

    @classmethod
    def from_registrations(
        cls,
        registrations: Iterable[ToolRegistration],
        *,
        now: datetime,
    ) -> ToolRegistry:
        """Validate a complete registration bundle before publishing projections."""
        ordered = _validated_registrations(registrations)
        descriptors = tuple(item.descriptor(now=now) for item in ordered)
        return cls(descriptors, registrations=ordered)

    @classmethod
    def from_catalog(
        cls,
        catalog: ToolRegistrationCatalog,
        *,
        now: datetime,
    ) -> ToolRegistry:
        return cls.from_registrations(catalog.registrations(), now=now)

    def with_registrations(
        self,
        registrations: Iterable[ToolRegistration],
        *,
        now: datetime,
    ) -> ToolRegistry:
        """Return an additive candidate without mutating the installed registry."""
        if self._registrations is None:
            raise ValueError("tool_registry_not_registration_backed")
        return self.from_registrations((*self._registrations, *registrations), now=now)

    def _current(self, *, now: datetime) -> tuple[ToolDescriptor, ...]:
        if self._registrations is not None:
            return _validated_descriptors(
                item.descriptor(now=now) for item in self._registrations
            )
        if self._provider is None:
            return self._descriptors
        return _validated_descriptors(self._provider.descriptors(now=now))

    def require_current_dispatch(
        self,
        frozen: ToolDescriptor,
        *,
        policy: CapabilityPolicy,
        now: datetime,
    ) -> ToolDescriptor:
        """Fail closed if a frozen plan target has since been revoked or changed."""
        current = next(
            (
                item
                for item in self._current(now=now)
                if item.capability_id == frozen.capability_id and item.version == frozen.version
            ),
            None,
        )
        if (
            current is None
            or current.execution_identity_sha256 != frozen.execution_identity_sha256
            or not current.enabled
            or not current.readiness.ready
            or current.readiness.observed_at > now
            or (now - current.readiness.observed_at).total_seconds()
            > current.readiness.max_age_seconds
            or not policy.permits(current.capability_id)
        ):
            raise ValueError("tool_dispatch_no_longer_available")
        return current

    @property
    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return self._descriptors

    @property
    def adapters(self) -> Mapping[str, ToolAdapter]:
        return self._adapters

    def current_descriptors(self, *, now: datetime) -> tuple[ToolDescriptor, ...]:
        """Expose the same live catalog used to freeze a planning snapshot."""
        return self._current(now=now)

    def snapshot_for_plan(  # noqa: PLR0913 - all selection inputs are explicit snapshot lineage.
        self,
        *,
        snapshot_id: str,
        run_id: str,
        remaining_tool_calls: int,
        remaining_cost_units: int,
        policy: CapabilityPolicy,
        now: datetime,
    ) -> CapabilitySnapshot:
        selected = tuple(
            descriptor
            for descriptor in self._current(now=now)
            if remaining_tool_calls > 0
            and descriptor.enabled
            and descriptor.readiness.ready
            and descriptor.readiness.observed_at <= now
            and (now - descriptor.readiness.observed_at).total_seconds()
            <= descriptor.readiness.max_age_seconds
            and descriptor.cost.worst_case_units <= remaining_cost_units
            and policy.permits(descriptor.capability_id)
        )
        return CapabilitySnapshot(
            schema_version="trace.capability-snapshot.v1",
            snapshot_id=snapshot_id,
            run_id=run_id,
            descriptors=selected,
            created_at=now,
        )


def _validated_registrations(
    registrations: Iterable[ToolRegistration],
) -> tuple[ToolRegistration, ...]:
    ordered = tuple(sorted(registrations, key=lambda item: item.key))
    keys = tuple(item.key for item in ordered)
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate_tool_registration")
    if any(item.adapter is None for item in ordered):
        raise ValueError("tool_registration_adapter_missing")
    for capability_id in {item.capability_id for item in ordered}:
        adapters = [item.adapter for item in ordered if item.capability_id == capability_id]
        if any(adapter is not adapters[0] for adapter in adapters[1:]):
            raise ValueError("tool_registration_executor_conflict")
    return ordered


def _registration_adapters(
    registrations: tuple[ToolRegistration, ...],
) -> dict[str, ToolAdapter]:
    adapters: dict[str, ToolAdapter] = {}
    for registration in registrations:
        adapter = registration.adapter
        if adapter is None:
            raise ValueError("tool_registration_adapter_missing")
        adapters[registration.capability_id] = adapter
    return adapters


def _validated_descriptors(
    descriptors: Iterable[ToolDescriptor],
) -> tuple[ToolDescriptor, ...]:
    ordered = tuple(sorted(descriptors, key=lambda item: (item.capability_id, item.version)))
    keys = tuple((item.capability_id, item.version) for item in ordered)
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate_tool_descriptor")
    enabled_ids = tuple(item.capability_id for item in ordered if item.enabled)
    if len(enabled_ids) != len(set(enabled_ids)):
        raise ValueError("multiple_enabled_tool_versions")
    if any(item.enabled and item.idempotency.key_scope == "adapter_defined" for item in ordered):
        raise ValueError("adapter_defined_idempotency_not_supported")
    return ordered


__all__ = [
    "CapabilityPolicy",
    "ToolCatalogProvider",
    "ToolDescriptorFactory",
    "ToolRegistration",
    "ToolRegistrationCatalog",
    "ToolRegistry",
]
