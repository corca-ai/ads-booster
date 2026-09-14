# pyright: reportUnnecessaryComparison=false, reportUnreachable=false
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from ads_booster.agent.core.ports import IdentifiedSemanticAssessor
from ads_booster.agent.service.completion_assessment import CompletionAttempt, CompletionContext
from ads_booster.agent.service.deterministic_completion import assess_deterministic_obligations
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.task_completion import (
    CompletionAssessment,
    ObligationAssessment,
    SemanticAssessmentResult,
)
from ads_booster.execution_control import ExecutionCancelledError

_INVALID_EVIDENCE: Final = "completion_evidence_invalid"
_OWNER_UNAVAILABLE: Final = "completion_proof_owner_unavailable"
__all__ = ["CompletionContext", "CompletionProofReader", "TaskCompletionService"]

if TYPE_CHECKING:
    from ads_booster.agent.core.ports import SemanticAssessor
    from ads_booster.agent.service.sqlite_repository import SqliteAgentRunRepository
    from ads_booster.contracts.agent_run import AgentRecord, AgentRun
    from ads_booster.contracts.task_completion import (
        CompletionCandidate,
        CompletionEvidenceSummary,
        SemanticAssessmentRequest,
    )
    from ads_booster.contracts.task_progress import TaskSpec


class CompletionProofReader(Protocol):
    def summarize(self, run: AgentRun, record: AgentRecord) -> CompletionEvidenceSummary: ...


class CompletionProofError(ValueError):
    def __init__(self, code: str) -> None:
        """Carry a bounded host reason without exposing external payloads."""
        self.code: str = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class TaskCompletionService:
    repository: SqliteAgentRunRepository
    assessor: SemanticAssessor | None
    proof_reader: CompletionProofReader | None = None

    def assess(  # noqa: PLR0911 - each gate has a distinct fail-closed outcome.
        self, task: TaskSpec, candidate: CompletionCandidate, context: CompletionContext
    ) -> CompletionAssessment:
        attempt = CompletionAttempt(task, candidate, context)
        reason = attempt.binding_error()
        if reason or self.repository.get(context.run.tenant_id, context.run.run_id) != context.run:
            return attempt.finish(reason or "completion_binding_invalid")
        try:
            evidence = self._evidence(candidate, context.run)
            deterministic = assess_deterministic_obligations(task.obligations, candidate)
            failed_ids = {
                item.obligation_id
                for item in deterministic.assessments
                if item.status == "unsatisfied"
            }
            semantic_obligations = tuple(
                item
                for item in task.obligations
                if item.verification is None
                or (task.task_revision > 1 and item.obligation_id in failed_ids)
            )
            if deterministic.uncovered_requirements and not semantic_obligations:
                deferred = tuple(
                    ObligationAssessment(
                        obligation_id=item.obligation_id,
                        status="unsatisfied",
                        mechanism="semantic_deferred",
                        reason="A required deterministic check failed before semantic assessment",
                    )
                    for item in deterministic.semantic_obligations
                )
                result = SemanticAssessmentResult(
                    request_sha256=contract_sha256({"deterministic": task.model_dump(mode="json")}),
                    candidate_sha256=contract_sha256(candidate),
                    obligations=(*deterministic.assessments, *deferred),
                    uncovered_requirements=deterministic.uncovered_requirements,
                    requested_deliverables_supported=False,
                )
                return attempt.resolve(result, evidence)
            if not semantic_obligations:
                result = SemanticAssessmentResult(
                    request_sha256=contract_sha256({"deterministic": task.model_dump(mode="json")}),
                    candidate_sha256=contract_sha256(candidate),
                    obligations=deterministic.assessments,
                    requested_deliverables_supported=True,
                )
                return attempt.resolve(result, evidence)
            if self.assessor is None:
                return attempt.finish("verification_unavailable")
            request = attempt.request(evidence, semantic_obligations)
            assessor_identity = _assessor_identity(self.assessor)
            cache_sha256 = (
                None
                if assessor_identity is None
                else _assessment_cache_sha256(request, assessor_identity)
            )
            cached = (
                None
                if cache_sha256 is None or assessor_identity is None
                else self._cached_semantic(context.run, cache_sha256, assessor_identity)
            )
            semantic = cached or self.assessor.assess(request)
            refreshed = self._evidence(candidate, context.run)
            merged = _merge_deterministic(task, deterministic.assessments, semantic)
        except ExecutionCancelledError:
            raise
        except CompletionProofError as error:
            return attempt.finish(error.code)
        except OSError, ValueError, RuntimeError:
            return attempt.finish("verification_unavailable")
        semantic_ids = tuple(item.obligation_id for item in semantic.obligations)
        if (
            semantic.request_sha256 != contract_sha256(request)
            or semantic.candidate_sha256 != contract_sha256(candidate)
            or refreshed != evidence
            or len(set(semantic_ids)) != len(semantic_ids)
            or set(semantic_ids) != {item.obligation_id for item in semantic_obligations}
        ):
            return attempt.finish("verification_unavailable")
        resolved = attempt.resolve(merged, evidence)
        if resolved.reason == "verification_unavailable":
            return resolved
        return resolved.model_copy(
            update={
                "assessment_cache_sha256": cache_sha256,
                "assessor_identity": assessor_identity,
                "semantic_result": semantic,
                "cache_hit": cached is not None,
            }
        )

    def _evidence(
        self, candidate: CompletionCandidate, run: AgentRun
    ) -> tuple[CompletionEvidenceSummary, ...]:
        records = {
            item.payload_sha256: item
            for item in self.repository.records(run.tenant_id, run.run_id)
            if item.payload_schema_version == "trace.tool-output-evidence.v1"
        }
        if not set(candidate.evidence_sha256s).issubset(records):
            raise CompletionProofError(_INVALID_EVIDENCE)
        evidence: list[CompletionEvidenceSummary] = []
        for digest in candidate.evidence_sha256s:
            if self.proof_reader is None:
                raise CompletionProofError(_OWNER_UNAVAILABLE)
            try:
                summary = self.proof_reader.summarize(run, records[digest])
            except ExecutionCancelledError:
                raise
            except (OSError, ValueError, RuntimeError) as error:
                raise CompletionProofError(_INVALID_EVIDENCE) from error
            if summary.evidence_sha256 != digest:
                raise CompletionProofError(_INVALID_EVIDENCE)
            evidence.append(summary)
        return tuple(evidence)

    def _cached_semantic(
        self, run: AgentRun, cache_sha256: str, assessor_identity: str
    ) -> SemanticAssessmentResult | None:
        for record in reversed(self.repository.records(run.tenant_id, run.run_id)):
            if record.payload_schema_version != "trace.task-completion.v1":
                continue
            cached = CompletionAssessment.model_validate(record.payload)
            if (
                cached.assessment_cache_sha256 == cache_sha256
                and cached.assessor_identity == assessor_identity
                and cached.semantic_result is not None
            ):
                return cached.semantic_result
        return None


def _assessor_identity(assessor: SemanticAssessor) -> str | None:
    match assessor:
        case IdentifiedSemanticAssessor():
            return assessor.assessment_identity
        case _:
            return None


def _assessment_cache_sha256(request: SemanticAssessmentRequest, assessor_identity: str) -> str:
    return contract_sha256(
        {
            "request_sha256": contract_sha256(request),
            "assessor_identity": assessor_identity,
            "result_schema": "trace.semantic-assessment-result.v1",
        }
    )


def _merge_deterministic(
    task: TaskSpec,
    deterministic: tuple[ObligationAssessment, ...],
    semantic: SemanticAssessmentResult,
) -> SemanticAssessmentResult:
    deterministic_by_id = {item.obligation_id: item for item in deterministic}
    semantic_by_id = {item.obligation_id: item for item in semantic.obligations}
    obligations: list[ObligationAssessment] = []
    uncovered = list(semantic.uncovered_requirements)
    for obligation in task.obligations:
        host = deterministic_by_id.get(obligation.obligation_id)
        judged = semantic_by_id.get(obligation.obligation_id)
        if host is None:
            if judged is not None:
                obligations.append(judged)
            continue
        selected = judged if judged is not None and judged.status == "superseded" else host
        obligations.append(selected)
        if obligation.required and selected.status == "unsatisfied":
            uncovered.append(obligation.description)
    return semantic.model_copy(
        update={
            "obligations": tuple(obligations),
            "uncovered_requirements": tuple(dict.fromkeys(uncovered)),
            "requested_deliverables_supported": semantic.requested_deliverables_supported
            and not uncovered,
        }
    )
