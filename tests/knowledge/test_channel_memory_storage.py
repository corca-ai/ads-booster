from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from ads_booster.contracts.knowledge_context import ValidationStage
from ads_booster.knowledge.change_publication import ChangeGroup, ChangePublisher, MemoryPublication
from ads_booster.knowledge.change_validation import ChangeValidationError
from ads_booster.knowledge.contracts import (
    AccessScope,
    MemoryDocument,
    MemoryKind,
    MemoryOperation,
    MemoryOperationKind,
    MemoryRevision,
)
from ads_booster.knowledge.errors import KnowledgePolicyError
from ads_booster.knowledge.memory import MemorySnapshot, ValidationCatalog, validate_memory_snapshot
from ads_booster.knowledge.repository import MembershipRole, SqliteKnowledgeRepository
from ads_booster.knowledge.repository_context import context_receipt_is_current, memory_document_ids
from tests.knowledge.change_test_fixtures import NOW, WORKSPACE_SCOPE, actor, memory_document
from tests.knowledge.test_transfer_validation_replay import accepted_transfer

if TYPE_CHECKING:
    from pathlib import Path


def test_channel_document_retains_explicit_owner() -> None:
    scope = AccessScope.model_validate(
        {
            "kind": "channel",
            "workspace_id": WORKSPACE_SCOPE.workspace_id,
            "channel_id": "C1",
        }
    )
    document = MemoryDocument(
        document_id="channel.core",
        workspace_id=scope.workspace_id,
        kind=MemoryKind.CORE,
        timezone="UTC",
        head_revision_id="r1",
        scope=scope,
    )
    restored = MemoryDocument.model_validate_json(document.model_dump_json())
    assert restored.owned_scope == scope


def test_legacy_document_serialization_keeps_original_shape() -> None:
    document = memory_document()
    assert "scope" not in document.model_dump()
    assert document.owned_scope == WORKSPACE_SCOPE


def test_document_rejects_foreign_workspace_owner() -> None:
    scope = AccessScope.model_validate(
        {
            "kind": "channel",
            "workspace_id": "foreign",
            "channel_id": "C1",
        }
    )
    with pytest.raises(ValidationError, match="memory_scope_workspace_mismatch"):
        _ = MemoryDocument(
            document_id="channel.core",
            workspace_id=WORKSPACE_SCOPE.workspace_id,
            kind=MemoryKind.CORE,
            timezone="UTC",
            head_revision_id="r1",
            scope=scope,
        )


def test_channel_documents_have_independent_heads_and_immutable_ownership(tmp_path: Path) -> None:

    repository = SqliteKnowledgeRepository(tmp_path)
    scopes = tuple(
        AccessScope.model_validate(
            {
                "kind": "channel",
                "workspace_id": WORKSPACE_SCOPE.workspace_id,
                "channel_id": channel,
            }
        )
        for channel in ("C1", "C2")
    )
    actors = tuple(
        actor().model_copy(
            update={
                "conversation_scope": scope,
                "grants": tuple(
                    grant.model_copy(
                        update={
                            "scope": scope,
                            "grant_id": f"{scope.channel_id}.{grant.grant_id}",
                        }
                    )
                    for grant in actor().grants
                ),
            }
        )
        for scope in scopes
    )
    for principal in actors:
        repository.register_actor(principal, MembershipRole.EDITOR)
    snapshots: list[MemorySnapshot] = []
    for index, principal in enumerate(actors):
        document = MemoryDocument(
            document_id=f"core.{index}",
            workspace_id=principal.workspace_id,
            kind=MemoryKind.CORE,
            timezone="UTC",
            head_revision_id=f"r{index}",
            scope=principal.conversation_scope,
        )
        revision = MemoryRevision(
            document_id=document.document_id,
            revision_id=document.head_revision_id,
            previous_revision_id=None,
            body_sha256=sha256(b"").hexdigest(),
            created_at=NOW,
        )
        snapshot = MemorySnapshot(document, revision, (), b"")
        snapshots.append(snapshot)
        _ = ChangePublisher(repository).publish(
            actor=principal,
            group=ChangeGroup(
                operation_id=f"create.{index}",
                memory_operations=(
                    MemoryOperation(
                        operation_id=f"create.{index}",
                        kind=MemoryOperationKind.ADD,
                        document_id=document.document_id,
                        entry_id="empty",
                        expected_revision_id="none",
                        reason="Initialize channel memory.",
                        evidence_refs=("init",),
                    ),
                ),
            ),
            pages=None,
            memories=(MemoryPublication(snapshot),),
            at=NOW,
        )
    reopened = SqliteKnowledgeRepository(tmp_path)
    assert memory_document_ids(reopened, actors[0]) == ("core.0",)
    assert memory_document_ids(reopened, actors[1]) == ("core.1",)
    assert reopened.find_memory_document_id(actors[1], MemoryKind.CORE, None, None) == "core.1"
    with pytest.raises(KnowledgePolicyError):
        _ = reopened.read_memory(actors[0], "core.1")
    with pytest.raises(ChangeValidationError):
        validate_memory_snapshot(
            snapshot=replace(
                snapshots[0],
                document=snapshots[0].document.model_copy(
                    update={"scope": scopes[1]},
                ),
            ),
            actor=actors[0],
            catalog=ValidationCatalog((), (), ()),
            at=NOW,
        )


def test_prepared_workspace_receipt_is_not_current_for_channel_actor(tmp_path: Path) -> None:

    adapter, request, _, _ = accepted_transfer(tmp_path, ValidationStage.PRE_DISPATCH)
    transfer = adapter.repository.context_transfer(request.transfer_id)
    binding = adapter.ingress.binding_for_run("run.1")
    assert transfer is not None
    assert binding is not None
    principal = binding.actor
    assert context_receipt_is_current(adapter.repository, principal, transfer.receipt)
    scope = AccessScope.model_validate(
        {
            "kind": "channel",
            "workspace_id": principal.workspace_id,
            "channel_id": "C1",
        }
    )
    channel_actor = principal.model_copy(
        update={
            "conversation_scope": scope,
            "grants": tuple(
                grant.model_copy(update={"scope": scope}) for grant in principal.grants
            ),
        }
    )
    assert not context_receipt_is_current(adapter.repository, channel_actor, transfer.receipt)
