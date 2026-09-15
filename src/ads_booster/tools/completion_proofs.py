from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, assert_never

from ads_booster.agent.service.completion_evidence import CompletionEvidenceReader
from ads_booster.contracts.task_completion import CompletionEvidenceSummary
from ads_booster.contracts.tool_capability import EffectClass
from ads_booster.tools.completion_registry import (
    CompletionArtifactOwners,
    CompletionProofRegistry,
    ProofIdentity,
    ProofRegistration,
)
from ads_booster.tools.completion_summary import evidence_description
from ads_booster.tools.completion_verifiers import configured_proof_registry

__all__ = [
    "CanonicalCompletionProofs",
    "CompletionArtifactOwners",
    "CompletionProofRegistry",
    "ProofIdentity",
    "ProofRegistration",
    "configured_proof_registry",
]

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.agent.service.sqlite_repository import SqliteAgentRunRepository
    from ads_booster.contracts.agent_run import AgentRecord, AgentRun
    from ads_booster.contracts.tool_handoff import ToolInputHandoff


@dataclass(frozen=True, slots=True)
class CanonicalCompletionProofs:
    repository: SqliteAgentRunRepository
    owners: CompletionArtifactOwners = field(default_factory=CompletionArtifactOwners)
    registry: CompletionProofRegistry = field(default_factory=configured_proof_registry)

    def input_handoff(
        self, run: AgentRun, record: AgentRecord, now: datetime
    ) -> ToolInputHandoff | None:
        bound = CompletionEvidenceReader(self.repository).read(run, record)
        return self.registry.input_handoff(run, bound, self.owners, now)

    def summarize(self, run: AgentRun, record: AgentRecord) -> CompletionEvidenceSummary:
        bound = CompletionEvidenceReader(self.repository).read(run, record)
        kind = _kind(bound.descriptor.effect_class)
        match bound.descriptor.effect_class:
            case EffectClass.OBSERVE:
                verified = bound.receipt.disposition in {"succeeded", "no_effect"}
            case (
                EffectClass.LOCAL_ARTIFACT | EffectClass.CONTROL_PLANE_WRITE | EffectClass.EXTERNAL
            ):
                verified = self.registry.verify(run, bound, self.owners)
            case _:
                assert_never(bound.descriptor.effect_class)
        return CompletionEvidenceSummary(
            evidence_sha256=record.payload_sha256,
            kind=kind,
            description=evidence_description(bound),
            verified=verified,
        )


def _kind(effect_class: EffectClass) -> Literal["response", "artifact", "effect"]:
    match effect_class:
        case EffectClass.OBSERVE:
            return "response"
        case EffectClass.LOCAL_ARTIFACT:
            return "artifact"
        case EffectClass.CONTROL_PLANE_WRITE | EffectClass.EXTERNAL:
            return "effect"
        case _:
            assert_never(effect_class)
