"""Server-owned guard for approved legacy SQLite memory used by learning writes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, override

from ads_booster.contracts.agent_memory import (
    LegacyMemoryAssessment,
    LegacyMemoryAssessmentKind,
    MemoryAccess,
    MemoryReference,
    MemoryScope,
    MemorySelection,
    MemorySelectionReceipt,
)
from ads_booster.knowledge.contract_types import ScopeKind

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.knowledge.scope_contracts import ActorContext
    from ads_booster.knowledge.tool_contracts import TrustedInvocationContext


class LegacyMemoryReader(Protocol):
    """Narrow read contract implemented by the existing SQLite memory owner."""

    def select(  # noqa: PLR0913 - mirrors the bounded legacy owner contract.
        self,
        access: MemoryAccess,
        *,
        query: str,
        run_id: str,
        now: datetime,
        limit: int = 6,
        max_chars: int = 6000,
    ) -> MemorySelection: ...

    def latest_selection(
        self,
        access: MemoryAccess,
        *,
        run_id: str,
        now: datetime,
    ) -> MemorySelection | None: ...

    def references_are_current(
        self,
        access: MemoryAccess,
        receipt: MemorySelectionReceipt,
        references: tuple[MemoryReference, ...],
        *,
        now: datetime,
    ) -> bool: ...


@dataclass(slots=True)
class LegacyMemoryGuardError(Exception):
    code: str

    @override
    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class LegacyMemoryGuard:
    reader: LegacyMemoryReader

    def select(
        self,
        actor: ActorContext,
        *,
        run_id: str,
        query: str,
        now: datetime,
    ) -> MemorySelection:
        return self.reader.select(
            self.access(actor, run_id=run_id),
            query=query[:8000],
            run_id=run_id,
            now=now,
        )

    def latest(
        self,
        actor: ActorContext,
        *,
        run_id: str,
        now: datetime,
    ) -> MemorySelection | None:
        return self.reader.latest_selection(
            self.access(actor, run_id=run_id), run_id=run_id, now=now
        )

    def assess(
        self,
        context: TrustedInvocationContext,
        assessments: tuple[LegacyMemoryAssessment, ...],
    ) -> tuple[MemoryReference, ...]:
        selection = context.legacy_memory_selection
        if selection is None:
            if assessments:
                code = "legacy_memory_selection_missing"
                raise LegacyMemoryGuardError(code)
            return ()
        receipt = selection.receipt
        expected_access = self.access(context.actor, run_id=context.run_id)
        if (
            receipt.run_id != context.run_id
            or receipt.scope != expected_access.scope
            or receipt.actor_id != context.actor.actor_id
        ):
            code = "legacy_memory_selection_context_mismatch"
            raise LegacyMemoryGuardError(code)
        fingerprint = receipt.selection_sha256
        if fingerprint is None or fingerprint != receipt.canonical_sha256():
            code = "legacy_memory_selection_stale"
            raise LegacyMemoryGuardError(code)
        if not self.references_are_current(
            context.actor,
            context.run_id,
            receipt,
            receipt.selected,
            now=context.invoked_at,
        ):
            code = "legacy_memory_selection_stale"
            raise LegacyMemoryGuardError(code)
        expected = {item.note_id: item for item in receipt.selected}
        provided = {item.reference.note_id: item for item in assessments}
        if len(provided) != len(assessments) or set(provided) != set(expected):
            code = "legacy_memory_assessment_incomplete"
            raise LegacyMemoryGuardError(code)
        for note_id, assessment in provided.items():
            if (
                assessment.selection_sha256 != fingerprint
                or assessment.reference != expected[note_id]
            ):
                code = "legacy_memory_assessment_mismatch"
                raise LegacyMemoryGuardError(code)
        return tuple(
            item.reference
            for item in assessments
            if item.assessment is LegacyMemoryAssessmentKind.CONFLICT
        )

    def references_are_current(
        self,
        actor: ActorContext,
        run_id: str,
        receipt: MemorySelectionReceipt,
        references: tuple[MemoryReference, ...],
        *,
        now: datetime,
    ) -> bool:
        if receipt.run_id != run_id:
            return False
        return self.reader.references_are_current(
            self.access(actor, run_id=receipt.run_id), receipt, references, now=now
        )

    @staticmethod
    def access(actor: ActorContext, *, run_id: str) -> MemoryAccess:
        private = actor.conversation_scope.kind is ScopeKind.MEMBER
        return MemoryAccess(
            scope=MemoryScope(
                workspace_id=actor.workspace_id,
                channel_id=actor.conversation_scope.channel_id,
                product_id="trace",
                work_id=run_id,
                member_id=actor.member_id if private else "",
                session_id=actor.session_id if private else "",
            ),
            actor_id=actor.actor_id,
            private=private,
        )


__all__ = ["LegacyMemoryGuard", "LegacyMemoryGuardError", "LegacyMemoryReader"]
