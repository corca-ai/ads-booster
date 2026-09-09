from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.change_validation import ChangeValidationError
from ads_booster.knowledge.change_write_preparation import prepare_memory_write
from ads_booster.knowledge.contracts import (
    AppliesTo,
    AuthorityClass,
    AuthorityRef,
    ConstraintBinding,
    ConstraintCompatibility,
    OperationReceipt,
    OperationStatus,
    SourceDisposition,
    UsageRole,
)
from ads_booster.knowledge.memory import MemorySnapshot
from ads_booster.knowledge.repository import (
    CatalogCommit,
    IndexOutboxItem,
    MembershipRole,
    RepositoryConflictError,
    SourceAdmissionChange,
    SqliteKnowledgeRepository,
)
from ads_booster.knowledge.tool_contracts import MemoryApplyInput, ToolResultStatus
from ads_booster.knowledge.tools import ToolHost
from tests.knowledge.personal_publication_fixtures import (
    NOW,
    ingest_personal_event,
    personal_actor,
    personal_context,
    user_payload,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_manual_user_publication_hides_original_reference_and_preserves_provenance(
    tmp_path: Path,
) -> None:
    # Given an owner's canonical message already admitted to shared reference search.
    repo = SqliteKnowledgeRepository(tmp_path / "knowledge")
    owner = personal_actor()
    event, receipt = ingest_personal_event(repo, owner)
    admission = SourceAdmissionChange(
        operation_id="operation.reference",
        payload_sha256=contract_sha256({"source": receipt.source_id}),
        workspace_id=owner.workspace_id,
        source_id=receipt.source_id,
        expected_admission_revision=0,
        disposition=SourceDisposition.REFERENCE,
        index_item=IndexOutboxItem(
            item_id="index.reference",
            workspace_id=owner.workspace_id,
            entity_kind="source",
            entity_id=receipt.source_id,
            revision_id=receipt.source_revision_id,
            extraction_version="extractor.v1",
            admission_revision=1,
        ),
        occurred_at=NOW,
    )
    _ = repo.change_source_admission(admission)
    payload = user_payload(owner, event)
    # When the canonical memory_apply tool publishes the personal preference.
    result = ToolHost(repo).execute(
        "memory_apply",
        MemoryApplyInput(
            schema="knowledge.tool.memory-apply.v1",
            operation_id=payload.operation.operation_id,
            changes=(payload,),
        ).model_dump(mode="json", by_alias=True),
        personal_context(owner),
    )
    # Then only the personal owner receives the preference, with exact message identity retained.
    assert result.status is ToolResultStatus.APPLIED, result
    stored = repo.read_memory(owner, payload.document.document_id)
    assert stored is not None
    assert stored.entries[0].source_refs == payload.entries[0].source_refs
    assert repo.resolve_evidence(owner, stored.entries[0].source_refs[0]) == event
    with repo.connection() as connection:
        visibility = TypeAdapter(tuple[str]).validate_python(
            connection.execute(
                "SELECT visibility FROM sources WHERE source_id=?", (receipt.source_id,)
            ).fetchone()
        )[0]
    assert visibility == "hidden"
    with pytest.raises(RepositoryConflictError, match="personal_source_not_shareable"):
        _ = repo.change_source_admission(admission)


def test_manual_user_publication_rejects_non_owner_evidence(tmp_path: Path) -> None:
    # Given a real channel message authored by somebody else.
    repo = SqliteKnowledgeRepository(tmp_path / "knowledge")
    owner = personal_actor()
    repo.register_actor(owner, MembershipRole.ADMIN)
    event, _ = ingest_personal_event(repo, personal_actor("U2"))
    payload = user_payload(owner, event)
    # When memory_apply tries to treat that evidence as the owner's personal preference.
    result = ToolHost(repo).execute(
        "memory_apply",
        MemoryApplyInput(
            schema="knowledge.tool.memory-apply.v1",
            operation_id=payload.operation.operation_id,
            changes=(payload,),
        ).model_dump(mode="json", by_alias=True),
        personal_context(owner),
    )
    # Then generic publication rejects it and no USER head is created.
    assert result.status is ToolResultStatus.REJECTED, result
    assert repo.read_memory(owner, payload.document.document_id) is None


@pytest.mark.parametrize("constraint_attachment", [False, True])
def test_user_preferences_cannot_publish_constraints(
    tmp_path: Path, constraint_attachment: bool
) -> None:
    # Given an owner-authored preference with forged instruction authority.
    repo = SqliteKnowledgeRepository(tmp_path / "knowledge")
    owner = personal_actor()
    event, _ = ingest_personal_event(repo, owner)
    payload = user_payload(owner, event)
    authority = AuthorityRef(
        event_id=event.message_id,
        authority_class=AuthorityClass.RUNTIME_POLICY,
        actor_ref=owner.actor_id,
        workspace_id=owner.workspace_id,
        scope=event.scope,
        policy_epoch=owner.policy_epoch,
    )
    if constraint_attachment:
        binding = ConstraintBinding(
            constraint_id="constraint.personal",
            workspace_id=owner.workspace_id,
            entry_id=payload.entries[0].entry_id,
            revision_id=payload.revision.revision_id,
            applies_to=AppliesTo(),
            authority_ref=authority,
            authority_class=authority.authority_class,
            compatibility=ConstraintCompatibility.COMPATIBLE,
        )
        payload = payload.model_copy(update={"constraints": (binding,)})
    else:
        entry = payload.entries[0].model_copy(
            update={"usage_role": UsageRole.CONSTRAINT, "authority_ref": authority}
        )
        payload = payload.model_copy(update={"entries": (entry,)})
    # When the manual publication attempts to make the preference a governing constraint.
    result = ToolHost(repo).execute(
        "memory_apply",
        MemoryApplyInput(
            schema="knowledge.tool.memory-apply.v1",
            operation_id=payload.operation.operation_id,
            changes=(payload,),
        ).model_dump(mode="json", by_alias=True),
        personal_context(owner),
    )
    # Then no personal constraint enters canonical memory or the constraint catalog.
    expected = ToolResultStatus.CONFLICT if constraint_attachment else ToolResultStatus.REJECTED
    assert result.status is expected, result
    assert result.error_code == (
        "user_memory_constraints_forbidden"
        if constraint_attachment
        else "user_memory_reference_only"
    )
    assert result.retryable is False
    assert repo.read_memory(owner, payload.document.document_id) is None


def test_direct_catalog_commit_cannot_bypass_personal_authorship(tmp_path: Path) -> None:
    # Given another user's real channel message and an otherwise valid owned USER write.
    repo = SqliteKnowledgeRepository(tmp_path / "knowledge")
    owner = personal_actor()
    repo.register_actor(owner, MembershipRole.ADMIN)
    event, _ = ingest_personal_event(repo, personal_actor("U2"))
    payload = user_payload(owner, event)
    snapshot = MemorySnapshot(
        payload.document, payload.revision, payload.entries, payload.body.encode()
    )
    operation_id = payload.operation.operation_id
    write = prepare_memory_write(repo, operation_id, snapshot, ())
    receipt = OperationReceipt(
        schema="knowledge.operation-receipt.v1",
        operation_id=operation_id,
        status=OperationStatus.APPLIED,
        resulting_revision_ids=(payload.revision.revision_id,),
        retryable=False,
        occurred_at=NOW,
    )
    # When a lower-level writer bypasses ChangePublisher and commits directly.
    with pytest.raises(ChangeValidationError, match="user_memory_owner_evidence_required"):
        _ = repo.commit_catalog(
            CatalogCommit(
                operation_id=operation_id,
                actor=owner,
                payload_sha256=contract_sha256(payload),
                receipt=receipt,
                memory_writes=(write,),
            )
        )
    # Then no USER head is created by the unchecked owner assertion.
    assert repo.read_memory(owner, payload.document.document_id) is None


def test_wiki_dependency_lookup_does_not_read_foreign_user_event_with_matching_id(
    tmp_path: Path,
) -> None:
    # Given a foreign USER preference whose event ID collides with a changed Wiki claim ID.
    repo = SqliteKnowledgeRepository(tmp_path / "knowledge")
    owner = personal_actor("U2")
    event, _ = ingest_personal_event(repo, owner, message_id="claim.updated")
    payload = user_payload(owner, event)
    result = ToolHost(repo).execute(
        "memory_apply",
        MemoryApplyInput(
            schema="knowledge.tool.memory-apply.v1",
            operation_id=payload.operation.operation_id,
            changes=(payload,),
        ).model_dump(mode="json", by_alias=True),
        personal_context(owner),
    )
    assert result.status is ToolResultStatus.APPLIED, result
    editor = personal_actor()
    repo.register_actor(editor, MembershipRole.ADMIN)
    # When channel Wiki publication resolves derived Wiki-memory dependencies.
    dependents = repo.memory_dependents(editor, ("claim.updated",))
    # Then canonical event dependencies are not mistaken for foreign Wiki summaries.
    assert dependents == ()
