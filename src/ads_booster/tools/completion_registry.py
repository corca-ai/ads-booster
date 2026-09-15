from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from ads_booster.contracts.agent_run import contract_sha256

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from pathlib import Path

    from ads_booster.agent.service.completion_evidence import BoundCompletionEvidence
    from ads_booster.agent.service.schedule_repository import ScheduleRepository
    from ads_booster.contracts.agent_run import AgentRun
    from ads_booster.contracts.creative_work import CreativeScope
    from ads_booster.contracts.tool_capability import EffectClass
    from ads_booster.contracts.tool_handoff import ToolInputHandoff
    from ads_booster.creative.creative_assets import SqliteCreativeAssetRepository
    from ads_booster.threads.accounts import ThreadsAccountRepository
    from ads_booster.threads.drafts import ThreadsDraftRepository
    from ads_booster.threads.publications import ThreadsPublicationRepository


@dataclass(frozen=True, slots=True)
class CompletionArtifactOwners:
    database_path: Path | None = None
    image_root: Path | None = None
    assets: SqliteCreativeAssetRepository | None = None
    scope_for_run: Callable[[AgentRun], CreativeScope] | None = None
    slack_channel_id: str | None = None
    notion_parent_page_id: str | None = None
    threads_publications: ThreadsPublicationRepository | None = None
    threads_accounts: ThreadsAccountRepository | None = None
    threads_drafts: ThreadsDraftRepository | None = None
    schedules: ScheduleRepository | None = None


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
    input_handoff: (
        Callable[
            [AgentRun, BoundCompletionEvidence, CompletionArtifactOwners, datetime],
            ToolInputHandoff | None,
        ]
        | None
    ) = None


@dataclass(frozen=True, slots=True)
class CompletionProofRegistry:
    registrations: tuple[ProofRegistration, ...]

    def input_handoff(
        self,
        run: AgentRun,
        bound: BoundCompletionEvidence,
        owners: CompletionArtifactOwners,
        now: datetime,
    ) -> ToolInputHandoff | None:
        identity = ProofIdentity(
            bound.descriptor.capability_id,
            bound.descriptor.owner,
            bound.receipt.executor_id,
            bound.descriptor.effect_class,
        )
        registration = next(
            (item for item in self.registrations if item.identity == identity), None
        )
        if registration is None or registration.input_handoff is None:
            return None
        if not self.verify(run, bound, owners):
            return None
        return registration.input_handoff(run, bound, owners, now)

    def __post_init__(self) -> None:
        """Reject ambiguous verifier ownership before publishing the registry."""
        identities = tuple(item.identity for item in self.registrations)
        if len(set(identities)) != len(identities):
            message = "duplicate_completion_proof_identity"
            raise ValueError(message)

    def verify(
        self, run: AgentRun, bound: BoundCompletionEvidence, owners: CompletionArtifactOwners
    ) -> bool:
        # Registry callers may be lower-level than CompletionEvidenceReader.  Keep
        # the receipt bound to the exact invocation before dispatching to an owner
        # verifier; the reader additionally validates approval and descriptor lineage.
        if bound.receipt.invocation_sha256 != contract_sha256(bound.invocation):
            return False
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
