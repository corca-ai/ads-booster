from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Never, override

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.contract_types import AuthorityClass
from ads_booster.knowledge.evidence_contracts import AuthorityRef
from ads_booster.knowledge.governance_contracts import TaskOverlay
from ads_booster.knowledge.grant_policy import authorize_write
from ads_booster.knowledge.operation_enums import CorrectionScope, CorrectionStatus
from ads_booster.knowledge.tool_contracts import (
    CorrectionData,
    KnowledgeQuestionInput,
    MemoryCorrectInput,
    PendingProposal,
    TrustedInvocationContext,
)

if TYPE_CHECKING:
    from ads_booster.knowledge.questions import KnowledgeQuestions
    from ads_booster.knowledge.repository_tool_state import CorrectionTarget, RepositoryToolState


@dataclass(slots=True)
class CorrectionError(Exception):
    code: str
    target: str

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.target}"


@dataclass(frozen=True, slots=True)
class KnowledgeCorrections:
    state: RepositoryToolState
    questions: KnowledgeQuestions

    def correct(
        self,
        request: MemoryCorrectInput,
        context: TrustedInvocationContext,
    ) -> CorrectionData:
        event = self.state.canonical_event(context.actor, request.authenticated_event_ref)
        match request.scope:
            case CorrectionScope.TASK_ONLY:
                return self._task_only(request, context, event.message_id)
            case CorrectionScope.TEAM:
                return self._team(request, context, event.message_id)

    def _task_only(
        self,
        request: MemoryCorrectInput,
        context: TrustedInvocationContext,
        event_id: str,
    ) -> CorrectionData:
        if request.task_id is None or context.task_id != request.task_id:
            _fail("task_scope_mismatch", request.operation_id)
        binding = self.state.task_binding(context.actor, request.task_id)
        if binding is None:
            _fail("task_scope_mismatch", request.operation_id)
        if (
            binding.state.value != "active"
            or binding.actor_ref != context.actor.actor_id
            or binding.workspace_id != context.actor.workspace_id
            or binding.member_id != context.actor.member_id
            or binding.session_id != context.actor.session_id
            or binding.capability_epoch != context.capability_epoch
            or binding.brand_id != context.brand_id
            or request.brand_id != context.brand_id
        ):
            _fail("task_scope_mismatch", request.operation_id)
        authority = AuthorityRef(
            event_id=event_id,
            authority_class=AuthorityClass.AUTHORIZED_TASK_INSTRUCTION,
            actor_ref=context.actor.actor_id,
            workspace_id=context.actor.workspace_id,
            scope=context.actor.conversation_scope,
            policy_epoch=context.capability_epoch,
        )
        identity = contract_sha256({"operation": request.operation_id, "event": event_id})
        overlay = TaskOverlay(
            overlay_id=f"overlay.{identity[:32]}",
            task_id=request.task_id,
            workspace_id=context.actor.workspace_id,
            actor_ref=context.actor.actor_id,
            capability_epoch=context.capability_epoch,
            brand_id=context.brand_id,
            instruction=request.correction_text,
            authority_ref=authority,
            reason=f"Task-only correction for {', '.join(request.target_ids)}.",
            created_at=context.invoked_at,
        )
        canonical_event = self.state.canonical_event(context.actor, event_id)
        _ = self.state.mark_event_source_use_only(
            context.actor,
            canonical_event,
            request.operation_id,
            context.invoked_at,
        )
        stored, replayed = self.state.put_task_overlay(context.actor, overlay)
        return CorrectionData(
            correction_id=request.operation_id,
            status=CorrectionStatus.TASK_ONLY,
            overlay=stored,
            replayed=replayed,
        )

    def _team(
        self,
        request: MemoryCorrectInput,
        context: TrustedInvocationContext,
        event_id: str,
    ) -> CorrectionData:
        targets = tuple(
            self.state.correction_target(context.actor, target_id)
            for target_id in request.target_ids
        )
        owner = targets[0]
        if any(self._owner_key(item) != self._owner_key(owner) for item in targets[1:]):
            _fail("correction_target_ambiguous", request.operation_id)
        _ = authorize_write(actor=context.actor, target_scope=owner.scope, at=context.invoked_at)
        if request.brand_id != owner.brand_id:
            _fail("correction_brand_mismatch", request.operation_id)
        identity = contract_sha256({"operation": request.operation_id, "event": event_id})
        question_id = f"question.{identity[:32]}"
        proposal = PendingProposal(
            proposal_id=f"proposal.{request.operation_id}",
            target_kind=owner.target_kind,
            target_id=owner.target_id,
            expected_revision_id=owner.expected_revision_id,
            brand_id=owner.brand_id,
        )
        result = self.questions.ask(
            KnowledgeQuestionInput(
                schema="knowledge.tool.question.v1",
                question_id=question_id,
                problem=request.correction_text,
                evidence_ids=(event_id, *request.target_ids),
                checks_tried=("Resolved the canonical owner and current expected head.",),
                recommendation="Review and explicitly approve the guarded owner change.",
                pending_proposal=proposal,
            ),
            context,
        )
        return CorrectionData(
            correction_id=request.operation_id,
            status=CorrectionStatus.PENDING,
            question_id=result.question.question_id,
        )

    @staticmethod
    def _owner_key(target: CorrectionTarget) -> tuple[str, str, str, str | None]:
        return (
            target.target_kind.value,
            target.target_id,
            target.expected_revision_id,
            target.brand_id,
        )


def _fail(code: str, target: str) -> Never:
    raise CorrectionError(code, target)


__all__ = ["CorrectionError", "KnowledgeCorrections"]
