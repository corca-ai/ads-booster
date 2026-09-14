from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.task_completion import (
    CompletionAssessment,
    ObligationAssessment,
    SemanticAssessmentRequest,
    SemanticObligation,
)

if TYPE_CHECKING:
    from ads_booster.contracts.agent_run import AgentRun
    from ads_booster.contracts.task_completion import (
        CompletionCandidate,
        CompletionEvidenceSummary,
        SemanticAssessmentResult,
    )
    from ads_booster.contracts.task_progress import TaskCheckpoint, TaskObligation, TaskSpec


@dataclass(frozen=True, slots=True)
class CompletionContext:
    run: AgentRun
    checkpoint: TaskCheckpoint


@dataclass(frozen=True, slots=True)
class CompletionAttempt:
    task: TaskSpec
    candidate: CompletionCandidate
    context: CompletionContext

    def binding_error(self) -> str | None:
        task, candidate, checkpoint = self.task, self.candidate, self.context.checkpoint
        if (
            (candidate.task_id, candidate.task_revision) != (task.task_id, task.task_revision)
            or (checkpoint.task_id, checkpoint.task_revision) != (task.task_id, task.task_revision)
            or checkpoint.spec_sha256 != contract_sha256(task)
            or checkpoint.candidate != candidate
            or candidate.answer_sha256 != contract_sha256({"answer": candidate.answer})
        ):
            return "completion_binding_invalid"
        if not 1 <= checkpoint.assessment_calls <= checkpoint.policy.max_assessments:
            return "completion_reservation_invalid"
        if checkpoint.decision_calls < checkpoint.assessment_calls:
            return "completion_reservation_invalid"
        return None

    def finish(
        self,
        reason: str,
        obligations: tuple[ObligationAssessment, ...] = (),
        disposition: Literal["satisfied", "continue", "waiting", "blocked"] = "blocked",
    ) -> CompletionAssessment:
        return CompletionAssessment(
            assessment_id=f"assessment-{contract_sha256(self.candidate)[:24]}-{self.context.checkpoint.assessment_calls}",
            task_id=self.task.task_id,
            task_revision=self.task.task_revision,
            task_spec_sha256=contract_sha256(self.task),
            candidate_sha256=contract_sha256(self.candidate),
            evidence_sha256s=self.candidate.evidence_sha256s,
            obligations=obligations
            or tuple(
                ObligationAssessment(
                    obligation_id=item.obligation_id,
                    status="unverifiable",
                    mechanism="host_binding",
                    reason=reason,
                )
                for item in self.task.obligations
            ),
            disposition=disposition,
            reason=reason,
        )

    def request(
        self,
        evidence: tuple[CompletionEvidenceSummary, ...],
        obligations: tuple[TaskObligation, ...] | None = None,
    ) -> SemanticAssessmentRequest:
        selected = self.task.obligations if obligations is None else obligations
        return SemanticAssessmentRequest(
            task_spec_sha256=contract_sha256(self.task),
            original_objective=self.task.original_objective,
            admitted_instructions=self.task.admitted_instructions,
            original_criteria=self.task.original_criteria,
            objective=self.task.objective,
            constraints=self.task.constraints,
            candidate=self.candidate,
            prior_result=self.task.prior_result,
            obligations=tuple(
                SemanticObligation(
                    obligation_id=item.obligation_id,
                    kind=item.kind,
                    description=item.description,
                    required=item.required,
                )
                for item in selected
            ),
            evidence=evidence,
            evidence_sha256s=self.candidate.evidence_sha256s,
        )

    def resolve(
        self, semantic: SemanticAssessmentResult, evidence: tuple[CompletionEvidenceSummary, ...]
    ) -> CompletionAssessment:
        ids = tuple(item.obligation_id for item in semantic.obligations)
        if (
            len(set(ids)) != len(ids)
            or set(ids) != {item.obligation_id for item in self.task.obligations}
            or any(
                not set(item.evidence_sha256s).issubset(self.candidate.evidence_sha256s)
                for item in semantic.obligations
            )
            or not self._supersessions_valid(semantic)
        ):
            return self.finish("verification_unavailable")
        assessments = self._host_judgments(semantic, evidence)
        required = {item.obligation_id for item in self.task.obligations if item.required}
        satisfied = (
            semantic.requested_deliverables_supported
            and not semantic.uncovered_requirements
            and set(semantic.required_evidence_kinds).issubset(
                item.kind for item in evidence if item.verified
            )
            and all(
                item.status in {"satisfied", "superseded"}
                for item in assessments
                if item.obligation_id in required
            )
        )
        if satisfied:
            return self.finish("completion_satisfied", assessments, "satisfied")
        if any(
            item.status == "unverifiable" for item in assessments if item.obligation_id in required
        ):
            return self.finish("completion_unverifiable", assessments)
        checkpoint = self.context.checkpoint
        disposition = (
            "blocked"
            if checkpoint.assessment_calls == checkpoint.policy.max_assessments
            else "continue"
        )
        missing_kinds = set(semantic.required_evidence_kinds) - {
            item.kind for item in evidence if item.verified
        }
        remaining = (
            *semantic.uncovered_requirements,
            *(
                f"Missing required {kind} owner proof from admitted instructions"
                for kind in sorted(missing_kinds)
            ),
        )
        return self.finish("completion_unsatisfied", assessments, disposition).model_copy(
            update={"uncovered_requirements": remaining[:128]}
        )

    def _host_judgments(
        self, semantic: SemanticAssessmentResult, evidence: tuple[CompletionEvidenceSummary, ...]
    ) -> tuple[ObligationAssessment, ...]:
        judged = {item.obligation_id: item for item in semantic.obligations}
        admitted = {item.evidence_sha256: item for item in evidence}
        assessments: list[ObligationAssessment] = []
        for obligation in self.task.obligations:
            judgment = judged[obligation.obligation_id]
            if (
                judgment.status != "superseded"
                and obligation.kind != "response"
                and not any(
                    admitted[digest].verified and admitted[digest].kind == obligation.kind
                    for digest in judgment.evidence_sha256s
                )
            ):
                judgment = ObligationAssessment(
                    obligation_id=obligation.obligation_id,
                    status="unverifiable"
                    if any(item.kind == obligation.kind for item in evidence)
                    else "unsatisfied",
                    mechanism="host_owner_proof",
                    reason=f"Missing verified {obligation.kind} proof: {obligation.description}",
                )
            assessments.append(judgment)
        return tuple(assessments)

    def _supersessions_valid(self, semantic: SemanticAssessmentResult) -> bool:
        order = {
            item.source_event_id: index
            for index, item in enumerate(self.task.admitted_instructions)
        }
        obligations = {item.obligation_id: item for item in self.task.obligations}
        for judgment in semantic.obligations:
            if judgment.status == "superseded":
                target = judgment.superseded_by_event_id
                sources = obligations[judgment.obligation_id].source_refs
                if (
                    self.task.task_revision == 1
                    or target not in order
                    or any(source not in order for source in sources)
                    or any(order[target] <= order[source] for source in sources)
                ):
                    return False
        return True
