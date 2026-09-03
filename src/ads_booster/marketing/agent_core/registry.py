"""Single capability registry used to freeze planner-visible tool snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from ads_booster.contracts.agent_run import CapabilitySnapshot

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from ads_booster.contracts.tool_capability import ToolDescriptor


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
    ) -> None:
        ordered = tuple(sorted(descriptors, key=lambda item: (item.capability_id, item.version)))
        keys = tuple((item.capability_id, item.version) for item in ordered)
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate_tool_descriptor")
        enabled_ids = tuple(item.capability_id for item in ordered if item.enabled)
        if len(enabled_ids) != len(set(enabled_ids)):
            raise ValueError("multiple_enabled_tool_versions")
        if any(
            item.enabled and item.idempotency.key_scope == "adapter_defined" for item in ordered
        ):
            raise ValueError("adapter_defined_idempotency_not_supported")
        self._descriptors: tuple[ToolDescriptor, ...] = ordered
        self._provider: ToolCatalogProvider | None = provider

    def _current(self, *, now: datetime) -> tuple[ToolDescriptor, ...]:
        if self._provider is None:
            return self._descriptors
        current = tuple(
            sorted(
                self._provider.descriptors(now=now),
                key=lambda item: (item.capability_id, item.version),
            )
        )
        keys = tuple((item.capability_id, item.version) for item in current)
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate_tool_descriptor")
        return current

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


__all__ = ["CapabilityPolicy", "ToolCatalogProvider", "ToolRegistry"]
