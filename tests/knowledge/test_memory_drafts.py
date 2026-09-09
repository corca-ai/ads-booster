"""Server-normalized CORE memory drafts remain bounded and source-linked."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING

from ads_booster.knowledge.contracts import (
    AccessScope,
    MemoryDocument,
    MemoryEntryKind,
    MemoryKind,
    MemoryOperation,
    MemoryOperationKind,
    MemoryRevision,
    ScopeKind,
)
from ads_booster.knowledge.curation import learning_target_is_consumed
from ads_booster.knowledge.governance_contracts import AppliesTo
from ads_booster.knowledge.learning_contracts import LearningReviewRequest
from ads_booster.knowledge.operation_enums import LearningPurpose
from ads_booster.knowledge.repository import MembershipRole
from ads_booster.knowledge.tool_contracts import (
    CoreMemoryDraft,
    KnowledgeToolName,
    MemoryApplyInput,
    MemoryRevisionPayload,
    ToolResultStatus,
)
from ads_booster.knowledge.tools import ToolHost
from tests.knowledge.change_test_fixtures import NOW, memory_entry
from tests.knowledge.test_curation_inputs import curation_input as fixture_curation_input

if TYPE_CHECKING:
    from tests.knowledge.test_curation_inputs import CurationInput

curation_input = fixture_curation_input


def _draft(*, text: str, expected_revision_id: str = "none") -> CoreMemoryDraft:
    return CoreMemoryDraft(
        document_id="memory.core",
        entry_id="entry.learning.core",
        expected_revision_id=expected_revision_id,
        text=text,
        kind=MemoryEntryKind.FACT,
        reason="Store the source-linked reusable campaign lesson.",
    )


def test_model_core_draft_creates_strict_source_linked_memory(
    curation_input: CurationInput,
) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    host = ToolHost(repository)
    context = processor.build_curation_work(job).trusted_context

    # When
    result = host.execute(
        "memory_apply",
        MemoryApplyInput(
            schema="knowledge.tool.memory-apply.v1",
            operation_id="operation.memory-draft.create",
            drafts=(_draft(text="Keep Korean price evidence current before campaign reuse."),),
        ).model_dump(mode="json", by_alias=True),
        context,
    )

    # Then
    stored = repository.read_memory(processor.actor, "memory.core")
    assert result.status is ToolResultStatus.APPLIED
    assert stored is not None
    assert stored.document.kind is MemoryKind.CORE
    assert stored.entries[0].text == "Keep Korean price evidence current before campaign reuse."
    assert stored.entries[0].source_refs[0].evidence_id == event.message_id
    assert stored.revision.body_sha256 == sha256(stored.body).hexdigest()


def test_model_core_draft_updates_existing_head_with_cas(curation_input: CurationInput) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    host = ToolHost(repository)
    context = processor.build_curation_work(job).trusted_context
    created = host.execute(
        "memory_apply",
        MemoryApplyInput(
            schema="knowledge.tool.memory-apply.v1",
            operation_id="operation.memory-draft.create",
            drafts=(_draft(text="Use the first source-linked lesson."),),
        ).model_dump(mode="json", by_alias=True),
        context,
    )
    assert created.status is ToolResultStatus.APPLIED
    original = repository.read_memory(processor.actor, "memory.core")
    assert original is not None

    # When
    result = host.execute(
        "memory_apply",
        MemoryApplyInput(
            schema="knowledge.tool.memory-apply.v1",
            operation_id="operation.memory-draft.update",
            drafts=(
                _draft(
                    text="Use the corrected source-linked lesson.",
                    expected_revision_id=original.revision.revision_id,
                ),
            ),
        ).model_dump(mode="json", by_alias=True),
        context,
    )

    # Then
    stored = repository.read_memory(processor.actor, "memory.core")
    assert result.status is ToolResultStatus.APPLIED
    assert stored is not None
    assert stored.revision.previous_revision_id == original.revision.revision_id
    assert stored.entries[0].text == "Use the corrected source-linked lesson."


def test_complete_memory_payload_with_forged_digest_is_rejected(
    curation_input: CurationInput,
) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    context = processor.build_curation_work(job).trusted_context
    document = MemoryDocument(
        document_id="memory.core",
        workspace_id=context.actor.workspace_id,
        kind=MemoryKind.CORE,
        timezone="UTC",
        head_revision_id="memory.core.forged",
    )
    entry = memory_entry(document=document, kind=MemoryEntryKind.FACT)
    revision = MemoryRevision(
        document_id=document.document_id,
        revision_id=document.head_revision_id,
        previous_revision_id=None,
        body_sha256="0" * 64,
        entry_ids=(entry.entry_id,),
        created_at=NOW,
    )

    # When
    result = ToolHost(repository).execute(
        "memory_apply",
        MemoryApplyInput(
            schema="knowledge.tool.memory-apply.v1",
            operation_id="operation.memory-draft.forged",
            changes=(
                MemoryRevisionPayload(
                    operation=MemoryOperation(
                        operation_id="operation.memory-draft.forged",
                        kind=MemoryOperationKind.ADD,
                        document_id=document.document_id,
                        entry_id=entry.entry_id,
                        expected_revision_id="none",
                        reason="Strict payloads must retain their supplied digest.",
                        evidence_refs=(entry.source_refs[0].evidence_id,),
                    ),
                    document=document,
                    revision=revision,
                    entries=(entry,),
                    body="# forged\n",
                ),
            ),
        ).model_dump(mode="json", by_alias=True),
        context,
    )

    # Then
    assert result.status is ToolResultStatus.REJECTED
    assert repository.read_memory(processor.actor, document.document_id) is None


def test_complete_memory_payload_stays_compatible(curation_input: CurationInput) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    context = processor.build_curation_work(job).trusted_context
    authenticated = processor.build_curation_work(job).request.authenticated_user_event
    assert authenticated is not None
    document = MemoryDocument(
        document_id="memory.core",
        workspace_id=context.actor.workspace_id,
        kind=MemoryKind.CORE,
        timezone="UTC",
        head_revision_id="memory.core.strict",
    )
    entry = memory_entry(document=document, kind=MemoryEntryKind.FACT).model_copy(
        update={"source_refs": (authenticated.evidence_ref,)}
    )
    body = b"# strict\n"
    revision = MemoryRevision(
        document_id=document.document_id,
        revision_id=document.head_revision_id,
        previous_revision_id=None,
        body_sha256=sha256(body).hexdigest(),
        entry_ids=(entry.entry_id,),
        created_at=NOW,
    )

    # When
    request = MemoryApplyInput(
        schema="knowledge.tool.memory-apply.v1",
        operation_id="operation.memory-draft.strict",
        changes=(
            MemoryRevisionPayload(
                operation=MemoryOperation(
                    operation_id="operation.memory-draft.strict",
                    kind=MemoryOperationKind.ADD,
                    document_id=document.document_id,
                    entry_id=entry.entry_id,
                    expected_revision_id="none",
                    reason="Strict complete payloads remain compatible.",
                    evidence_refs=(authenticated.evidence_ref.evidence_id,),
                ),
                document=document,
                revision=revision,
                entries=(entry,),
                body=body.decode(),
            ),
        ),
    )
    result = ToolHost(repository).execute(
        "memory_apply",
        request.model_dump(mode="json", by_alias=True),
        context,
    )

    # Then
    stored = repository.read_memory(processor.actor, document.document_id)
    assert request.target_ids == (document.document_id,)
    assert result.status is ToolResultStatus.APPLIED
    assert stored is not None
    assert stored.revision == revision
    assert stored.body == body


def test_core_memory_draft_rejects_private_team_and_soul_paths(
    curation_input: CurationInput,
) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    host = ToolHost(repository)
    context = processor.build_curation_work(job).trusted_context
    private_scope = AccessScope(
        kind=ScopeKind.MEMBER,
        workspace_id=context.actor.workspace_id,
        member_id="member.private",
        session_id="session.private",
    )
    private_actor = context.actor.model_copy(
        update={
            "actor_id": "actor.private",
            "member_id": private_scope.member_id,
            "session_id": private_scope.session_id,
            "conversation_scope": private_scope,
            "grants": tuple(
                grant.model_copy(update={"scope": private_scope}) for grant in context.actor.grants
            ),
        }
    )
    repository.register_actor(private_actor, MembershipRole.EDITOR)
    private_context = context.model_copy(
        update={"actor": private_actor, "source_capabilities": (), "source_fetch_event": None}
    )

    # When
    private_result = host.execute(
        "memory_apply",
        MemoryApplyInput(
            schema="knowledge.tool.memory-apply.v1",
            operation_id="operation.memory-draft.private",
            drafts=(_draft(text="Private text must not become shared CORE memory."),),
        ).model_dump(mode="json", by_alias=True),
        private_context,
    )
    team_result = host.execute(
        "memory_apply",
        {
            "schema": "knowledge.tool.memory-apply.v1",
            "operation_id": "operation.memory-draft.team",
            "drafts": [
                {
                    "document_id": "memory.team",
                    "entry_id": "entry.learning.team",
                    "text": "TEAM must not use the CORE draft path.",
                    "reason": "Model draft target must stay canonical CORE.",
                }
            ],
        },
        context,
    )
    soul_result = host.execute(
        "memory_apply",
        {
            "schema": "knowledge.tool.memory-apply.v1",
            "operation_id": "operation.memory-draft.soul",
            "drafts": [
                {
                    "document_id": "memory.soul",
                    "entry_id": "entry.learning.soul",
                    "text": "SOUL must not use the CORE draft path.",
                    "reason": "Model draft target must stay canonical CORE.",
                }
            ],
        },
        context,
    )

    # Then
    assert private_result.status is ToolResultStatus.REJECTED
    assert private_result.error_code == "core_memory_draft_private_forbidden"
    assert team_result.status is ToolResultStatus.REJECTED
    assert team_result.error_code == "tool_input_invalid"
    assert soul_result.status is ToolResultStatus.REJECTED
    assert soul_result.error_code == "tool_input_invalid"
    assert repository.read_memory(processor.actor, "memory.core") is None


def test_learning_fence_consumes_core_draft_before_tool_host(
    curation_input: CurationInput,
) -> None:
    # Given
    _, processor, job, _, _ = curation_input
    work = processor.build_curation_work(job)
    request = work.request.model_copy(
        update={
            "learning_purpose": LearningPurpose.CONVERSATIONAL_FEEDBACK,
            "learning_review": LearningReviewRequest(
                schema="knowledge.learning-review-request.v1",
                round_id="round.memory-draft",
                purpose=LearningPurpose.CONVERSATIONAL_FEEDBACK,
                applicability=AppliesTo(),
                consumed_target_ids=("memory.core",),
            ),
        }
    )
    draft = MemoryApplyInput(
        schema="knowledge.tool.memory-apply.v1",
        operation_id="operation.memory-draft.consumed",
        drafts=(_draft(text="Do not reapply this consumed CORE target."),),
    )

    # When
    blocked = learning_target_is_consumed(
        request,
        KnowledgeToolName.MEMORY_APPLY,
        draft.model_dump(mode="json", by_alias=True),
    )

    # Then
    assert draft.target_ids == ("memory.core",)
    assert blocked
