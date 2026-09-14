from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ads_booster.agent.service.completion_evidence import BoundCompletionEvidence
    from ads_booster.contracts.agent_run import AgentRun
    from ads_booster.contracts.creative_work import CreativeScope
    from ads_booster.contracts.tool_capability import EffectClass
    from ads_booster.creative.creative_assets import SqliteCreativeAssetRepository


@dataclass(frozen=True, slots=True)
class CompletionArtifactOwners:
    image_root: Path | None = None
    assets: SqliteCreativeAssetRepository | None = None
    scope_for_run: Callable[[AgentRun], CreativeScope] | None = None


@dataclass(frozen=True, slots=True)
class ProofIdentity:
    capability_id: str
    owner: str
    executor_id: str
    effect_class: EffectClass


class CompletionProofVerifier(Protocol):
    def verify(
        self, run: AgentRun, bound: BoundCompletionEvidence, owners: CompletionArtifactOwners
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class ProofRegistration:
    identity: ProofIdentity
    verifier: CompletionProofVerifier
    installation_id: str | None = None


@dataclass(frozen=True, slots=True)
class CompletionProofRegistry:
    registrations: tuple[ProofRegistration, ...]

    def __post_init__(self) -> None:
        """Reject ambiguous verifier ownership before publishing the registry."""
        identities = tuple(item.identity for item in self.registrations)
        if len(set(identities)) != len(identities):
            message = "duplicate_completion_proof_identity"
            raise ValueError(message)

    def verify(
        self, run: AgentRun, bound: BoundCompletionEvidence, owners: CompletionArtifactOwners
    ) -> bool:
        identity = ProofIdentity(
            bound.descriptor.capability_id,
            bound.descriptor.owner,
            bound.receipt.executor_id,
            bound.descriptor.effect_class,
        )
        registration = next(
            (item for item in self.registrations if item.identity == identity), None
        )
        if (
            registration is None
            or bound.receipt.disposition != "succeeded"
            or (
                registration.installation_id is not None
                and registration.installation_id != bound.descriptor.installation_id
            )
        ):
            return False
        return registration.verifier.verify(run, bound, owners)
