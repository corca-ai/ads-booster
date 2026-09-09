from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Never, override

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.adoption_contracts import ExplicitAdoptionReceipt
from ads_booster.knowledge.contract_types import (
    GrantCapability,
    InstructionAuthority,
    Provenance,
    ScopeKind,
)
from ads_booster.knowledge.governance_contracts import BrandTarget
from ads_booster.knowledge.grant_policy import authorize_brand_voice_edit, authorize_write
from ads_booster.knowledge.repository_tool_state import ToolStateError
from ads_booster.knowledge.tool_contracts import (
    KnowledgeQuestionInput,
    PendingProposal,
    ProposalTargetKind,
    QuestionRecord,
    QuestionStatus,
    TrustedInvocationContext,
    TrustedQuestionAnswer,
)

if TYPE_CHECKING:
    from ads_booster.knowledge.legacy_memory import LegacyMemoryGuard
    from ads_booster.knowledge.repository_tool_state import RepositoryToolState
    from ads_booster.knowledge.source_contracts import ConversationEvent
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
    legacy_memory: LegacyMemoryGuard | None = None

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
        if proposal is not None and not self._proposal_is_current(
            proposal,
            context,
            allow_new=bool(request.legacy_memory_conflicts),
        ):
            _fail("question_proposal_binding_mismatch", request.question_id)
        source = self._source_event(request, context)
        question = QuestionRecord(
            question_id=request.question_id,
            workspace_id=context.actor.workspace_id,
            actor_ref=context.actor.actor_id,
            problem=request.problem,
            evidence_ids=request.evidence_ids,
            checks_tried=request.checks_tried,
            recommendation=request.recommendation,
            pending_proposal=request.pending_proposal,
            source_event_id=None if source is None else source.message_id,
            source_event_revision=None if source is None else source.revision,
            source_conversation_id=None if source is None else source.conversation_id,
            source_scope=None if source is None else source.scope,
            legacy_memory_receipt=request.legacy_memory_receipt,
            legacy_memory_conflicts=request.legacy_memory_conflicts,
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
        canonical = self._require_canonical_answer(answer, context)
        self._require_answer_binding(current, answer, canonical, context)
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

    def pending_for_conversation(
        self,
        conversation_id: str,
        context: TrustedInvocationContext,
    ) -> tuple[QuestionRecord, ...]:
        return tuple(
            question
            for question in self.state.pending_questions_for_conversation(
                context.actor, conversation_id
            )
            if self._legacy_memory_is_current(question, context)
        )

    def _source_event(
        self,
        request: KnowledgeQuestionInput,
        context: TrustedInvocationContext,
    ) -> ConversationEvent | None:
        if request.source_event_id is None:
            return None
        if request.source_event_id not in request.evidence_ids:
            _fail("question_source_evidence_missing", request.question_id)
        source = self.state.canonical_event(context.actor, request.source_event_id)
        if not self.state.event_is_bound_to_context(context, source):
            _fail("question_source_not_bound", request.question_id)
        return source

    def _require_canonical_answer(
        self,
        answer: TrustedQuestionAnswer,
        context: TrustedInvocationContext,
    ) -> ConversationEvent:
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
        return canonical

    def _require_answer_binding(
        self,
        question: QuestionRecord,
        answer: TrustedQuestionAnswer,
        canonical: ConversationEvent,
        context: TrustedInvocationContext,
    ) -> None:
        if question.source_scope is None:
            return
        if not self.state.question_source_is_current(context.actor, question):
            _fail("question_source_stale", question.question_id)
        if not self._legacy_memory_is_current(question, context):
            _fail("question_legacy_memory_stale", question.question_id)
        if question.source_scope.kind is ScopeKind.MEMBER:
            if question.source_conversation_id != canonical.conversation_id:
                _fail("question_answer_thread_mismatch", question.question_id)
            return
        if (
            question.source_conversation_id != canonical.conversation_id
            or canonical.scope.kind is not ScopeKind.WORKSPACE
            or GrantCapability.WRITE not in answer.authenticated_event.capabilities
        ):
            _fail("question_answer_thread_mismatch", question.question_id)
        if not self.state.is_active_workspace_member(context.actor):
            _fail("question_answer_member_inactive", question.question_id)
        _ = authorize_write(
            actor=context.actor,
            target_scope=question.source_scope,
            at=answer.answered_at,
        )
        proposal = question.pending_proposal
        if proposal is None:
            return
        if not self._proposal_is_current(
            proposal,
            context,
            allow_new=bool(question.legacy_memory_conflicts),
        ):
            _fail("question_proposal_stale", question.question_id)

    def _proposal_is_current(
        self,
        proposal: PendingProposal,
        context: TrustedInvocationContext,
        *,
        allow_new: bool,
    ) -> bool:
        try:
            target = self.state.correction_target(context.actor, proposal.target_id)
        except ToolStateError:
            return (
                allow_new and proposal.expected_revision_id == "none" and proposal.brand_id is None
            )
        return (
            target.target_kind is proposal.target_kind
            and target.target_id == proposal.target_id
            and target.expected_revision_id == proposal.expected_revision_id
            and target.brand_id == proposal.brand_id
            and proposal.brand_id == context.brand_id
        )

    def _legacy_memory_is_current(
        self,
        question: QuestionRecord,
        context: TrustedInvocationContext,
    ) -> bool:
        receipt = question.legacy_memory_receipt
        conflicts = question.legacy_memory_conflicts
        if receipt is None:
            return not conflicts
        return (
            bool(conflicts)
            and self.legacy_memory is not None
            and self.legacy_memory.references_are_current(
                context.actor,
                context.run_id,
                receipt,
                conflicts,
                now=context.invoked_at,
            )
        )

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
