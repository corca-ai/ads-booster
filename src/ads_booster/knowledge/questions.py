from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Never, override

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.adoption_contracts import ExplicitAdoptionReceipt
from ads_booster.knowledge.contract_types import (
    GrantCapability,
    InstructionAuthority,
    Provenance,
)
from ads_booster.knowledge.governance_contracts import BrandTarget
from ads_booster.knowledge.grant_policy import authorize_brand_voice_edit
from ads_booster.knowledge.tool_contracts import (
    KnowledgeQuestionInput,
    ProposalTargetKind,
    QuestionRecord,
    QuestionStatus,
    TrustedInvocationContext,
    TrustedQuestionAnswer,
)

if TYPE_CHECKING:
    from ads_booster.knowledge.repository_tool_state import RepositoryToolState
    from ads_booster.transport.json_types import JsonObject


@dataclass(slots=True)
class QuestionError(Exception):
    code: str
    question_id: str

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.question_id}"


@dataclass(frozen=True, slots=True)
class QuestionWriteResult:
    question: QuestionRecord
    replayed: bool


@dataclass(frozen=True, slots=True)
class QuestionAnswerResult:
    question: QuestionRecord
    adoption_receipt: ExplicitAdoptionReceipt | None
    replayed: bool


@dataclass(frozen=True, slots=True)
class KnowledgeQuestions:
    state: RepositoryToolState

    def ask(
        self,
        request: KnowledgeQuestionInput,
        context: TrustedInvocationContext,
    ) -> QuestionWriteResult:
        if any(
            not self.state.reference_exists(context.actor, reference_id)
            for reference_id in request.evidence_ids
        ):
            _fail("question_evidence_not_found", request.question_id)
        proposal = request.pending_proposal
        if proposal is not None:
            target = self.state.correction_target(context.actor, proposal.target_id)
            if (
                target.target_kind is not proposal.target_kind
                or target.target_id != proposal.target_id
                or target.expected_revision_id != proposal.expected_revision_id
                or target.brand_id != proposal.brand_id
                or proposal.brand_id != context.brand_id
            ):
                _fail("question_proposal_binding_mismatch", request.question_id)
        question = QuestionRecord(
            question_id=request.question_id,
            workspace_id=context.actor.workspace_id,
            actor_ref=context.actor.actor_id,
            problem=request.problem,
            evidence_ids=request.evidence_ids,
            checks_tried=request.checks_tried,
            recommendation=request.recommendation,
            pending_proposal=request.pending_proposal,
            status=QuestionStatus.PENDING,
            created_at=context.invoked_at,
        )
        stored, replayed = self.state.put_question(context.actor, question)
        return QuestionWriteResult(question=stored, replayed=replayed)

    def answer(
        self,
        answer: TrustedQuestionAnswer,
        context: TrustedInvocationContext,
    ) -> QuestionAnswerResult:
        current = self.state.question(context.actor, answer.question_id)
        if current is None:
            _fail("question_not_found", answer.question_id)
        self._require_canonical_answer(answer, context)
        answered = current.model_copy(
            update={
                "status": QuestionStatus.ANSWERED,
                "answer_event_id": answer.authenticated_event.event_id,
                "answer": answer.answer,
                "answered_at": answer.answered_at,
            }
        )
        if current.status is QuestionStatus.ANSWERED:
            stored, receipt, replayed = self.state.answer_question(
                context.actor,
                answered,
                None,
            )
            return QuestionAnswerResult(stored, receipt, replayed)
        receipt = self._adoption_receipt(current, answer, context)
        stored, persisted_receipt, replayed = self.state.answer_question(
            context.actor,
            answered,
            receipt,
        )
        return QuestionAnswerResult(stored, persisted_receipt, replayed)

    def _require_canonical_answer(
        self,
        answer: TrustedQuestionAnswer,
        context: TrustedInvocationContext,
    ) -> None:
        trusted = answer.authenticated_event
        canonical = self.state.canonical_event(context.actor, trusted.event_id)
        occurred_at = canonical.edited_at or canonical.created_at
        if (
            trusted.actor_ref != context.actor.actor_id
            or trusted.workspace_id != context.actor.workspace_id
            or trusted.scope != canonical.scope
            or trusted.policy_epoch != context.capability_epoch
            or trusted.occurred_at != occurred_at
            or trusted.instruction_authority is not InstructionAuthority.AUTHORIZED_USER
            or trusted.provenance is not Provenance.HUMAN_DIRECT
        ):
            _fail("authenticated_answer_mismatch", answer.question_id)

    def _adoption_receipt(
        self,
        question: QuestionRecord,
        answer: TrustedQuestionAnswer,
        context: TrustedInvocationContext,
    ) -> ExplicitAdoptionReceipt | None:
        proposal = question.pending_proposal
        if proposal is None or proposal.target_kind is not ProposalTargetKind.SOUL:
            if answer.explicitly_adopts:
                _fail("adoption_proposal_missing", question.question_id)
            return None
        if not answer.explicitly_adopts:
            return None
        if proposal.brand_id is None:
            _fail("adoption_brand_missing", question.question_id)
        target = self.state.correction_target(context.actor, proposal.target_id)
        if (
            target.target_kind is not ProposalTargetKind.SOUL
            or target.brand_id != proposal.brand_id
            or target.expected_revision_id != proposal.expected_revision_id
        ):
            _fail("adoption_proposal_stale", question.question_id)
        _ = authorize_brand_voice_edit(
            actor=context.actor,
            target=BrandTarget(scope=target.scope, brand_id=proposal.brand_id),
            at=answer.answered_at,
        )
        if GrantCapability.BRAND_VOICE_EDIT not in answer.authenticated_event.capabilities:
            _fail("adoption_event_capability_missing", question.question_id)
        receipt_payload: JsonObject = {
            "question": question.question_id,
            "event": answer.authenticated_event.event_id,
            "proposal": proposal.proposal_id,
        }
        receipt_id = f"adoption.{contract_sha256(receipt_payload)[:32]}"
        return ExplicitAdoptionReceipt(
            receipt_id=receipt_id,
            proposal_id=proposal.proposal_id,
            question_id=question.question_id,
            workspace_id=context.actor.workspace_id,
            actor_ref=context.actor.actor_id,
            brand_id=proposal.brand_id,
            expected_revision_id=proposal.expected_revision_id,
            authenticated_event_id=answer.authenticated_event.event_id,
            answered_at=answer.answered_at,
        )


def _fail(code: str, question_id: str) -> Never:
    raise QuestionError(code, question_id)


__all__ = [
    "KnowledgeQuestions",
    "QuestionAnswerResult",
    "QuestionError",
    "QuestionWriteResult",
]
