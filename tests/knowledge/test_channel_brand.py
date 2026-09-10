from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter, ValidationError

from ads_booster.contracts.agent_run import CapabilitySnapshot, contract_sha256
from ads_booster.contracts.knowledge_preparation import (
    BrandUnresolvedPreparation,
    PreparedKnowledgeContext,
    RequiredContextPreparationError,
)
from ads_booster.contracts.knowledge_selection import KnowledgeActionKind
from ads_booster.knowledge.context_selection import KnowledgeContextAssembler
from ads_booster.knowledge.contracts import (
    AccessScope,
    ActorContext,
    Brand,
    BrandState,
    ScopeKind,
    TaskBinding,
    TaskBindingState,
)
from ads_booster.knowledge.repository import (
    MembershipRole,
    RepositoryConflictError,
    SqliteKnowledgeRepository,
)
from ads_booster.knowledge.repository_identity import scope_key
from ads_booster.knowledge.retrieval import KnowledgeRetriever
from tests.knowledge.brand_test_fixtures import brand_registration
from tests.knowledge.change_test_fixtures import NOW, actor

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.knowledge.context_selection import ContextBuildResult
    from ads_booster.knowledge.repository import BrandRegistration
    from ads_booster.transport.json_types import JsonObject


def _actor(channel_id: str | None) -> ActorContext:
    base = actor()
    if channel_id is None:
        return base
    scope = AccessScope(
        kind=ScopeKind.CHANNEL, workspace_id=base.workspace_id, channel_id=channel_id
    )
    return base.model_copy(
        update={
            "session_id": f"session.{channel_id}",
            "conversation_scope": scope,
            "grants": tuple(
                grant.model_copy(
                    update={"scope": scope, "grant_id": f"{grant.grant_id}.{channel_id}"}
                )
                for grant in base.grants
            ),
        }
    )


def _prepare(
    repository: SqliteKnowledgeRepository, editor: ActorContext, brand_id: str | None
) -> ContextBuildResult:
    task = TaskBinding(
        task_id="task.brand." + (brand_id or "unresolved"),
        workspace_id=editor.workspace_id,
        actor_ref=editor.actor_id,
        member_id=editor.member_id,
        session_id=editor.session_id,
        action_kind=KnowledgeActionKind.CONTENT_WRITE,
        brand_id=brand_id,
        brand_catalog_revision=None if brand_id is None else 1,
        capability_epoch=editor.policy_epoch,
        state=TaskBindingState.ACTIVE,
        opened_at=NOW,
    )
    snapshot = CapabilitySnapshot(
        schema_version="trace.capability-snapshot.v1",
        snapshot_id="snapshot.brand",
        run_id="run.brand",
        descriptors=(),
        created_at=NOW,
    )
    return KnowledgeContextAssembler(repository, KnowledgeRetriever(repository)).prepare(
        editor, task, query="Shared Name", tool_catalog=(), capability_snapshot=snapshot, now=NOW
    )


def test_legacy_brand_serialization_preserves_hash() -> None:
    # Given a persisted workspace brand from before channel ownership.
    payload: JsonObject = {
        "brand_id": "brand.legacy",
        "workspace_id": "workspace.alpha",
        "name": "Legacy",
        "revision": 1,
        "state": "active",
    }
    brand = Brand.model_validate(payload)
    # When read through the new ownership projection, then old canonical payload stays unchanged.
    assert brand.owned_scope == _actor(None).conversation_scope
    assert brand.model_dump(mode="json") == payload
    assert contract_sha256(brand) == contract_sha256(payload)


@pytest.mark.parametrize(
    "scope",
    [
        AccessScope(kind=ScopeKind.CHANNEL, workspace_id="foreign", channel_id="CA"),
        AccessScope(
            kind=ScopeKind.MEMBER,
            workspace_id="workspace.alpha",
            member_id="member",
            session_id="session",
        ),
    ],
)
def test_brand_rejects_foreign_or_private_scope(scope: AccessScope) -> None:
    # Given an invalid owner, when creating the shared brand, then its contract rejects it.
    with pytest.raises(ValidationError):
        _ = Brand(
            brand_id="brand.invalid",
            workspace_id="workspace.alpha",
            name="Shared Name",
            revision=1,
            state=BrandState.ACTIVE,
            scope=scope,
        )


def test_identical_brand_names_and_soul_heads_are_channel_local(tmp_path: Path) -> None:
    # Given three independent same-named brands in workspace legacy and channel A/B scopes.
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editors = (_actor(None), _actor("CA"), _actor("CB"))
    registrations: list[BrandRegistration] = []
    for editor, suffix in zip(editors, ("legacy", "a", "b"), strict=True):
        repository.register_actor(editor, MembershipRole.ADMIN)
        registration = brand_registration(repository, editor, "brand." + suffix, suffix)
        receipt = repository.register_brand(registration)
        assert repository.register_brand(registration) == receipt
        registrations.append(registration)

    # When each channel requests brand discovery and branded content context.
    for editor, own in zip(editors, registrations, strict=True):
        granted = editor.model_copy(
            update={"grants": tuple(grant for identity in editors for grant in identity.grants)}
        )
        catalog = _prepare(repository, granted, None)
        prepared = _prepare(repository, granted, own.brand.brand_id)
        # Then no same-named brand or SOUL head from another owner is exposed.
        assert isinstance(catalog, BrandUnresolvedPreparation)
        assert catalog.candidate_brand_refs == (own.brand.brand_id,)
        assert isinstance(prepared, PreparedKnowledgeContext)
        assert prepared.receipt.soul_revision_id == own.revision.revision_id
        for other in registrations:
            if other.brand.brand_id == own.brand.brand_id:
                continue
            assert repository.brand(granted, other.brand.brand_id) is None
            assert isinstance(
                _prepare(repository, granted, other.brand.brand_id), RequiredContextPreparationError
            )
    with repository.connection() as connection:
        rows = TypeAdapter(list[tuple[str]]).validate_python(
            connection.execute("SELECT scope_key FROM brands").fetchall()
        )
        assert {row[0] for row in rows} == {
            scope_key(editor.conversation_scope) for editor in editors
        }


@pytest.mark.parametrize("mismatch", ["authority", "document"])
def test_brand_registration_rejects_channel_scope_mismatch(tmp_path: Path, mismatch: str) -> None:
    # Given a brand owned by channel A and a conflicting channel B authority or SOUL document.
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = _actor("CA")
    repository.register_actor(editor, MembershipRole.ADMIN)
    other = _actor("CB").conversation_scope
    registration = brand_registration(repository, editor, "brand.a", "a")
    if mismatch == "authority":
        registration = replace(
            registration,
            event=registration.event.model_copy(
                update={
                    "authority_ref": registration.event.authority_ref.model_copy(
                        update={"scope": other}
                    )
                }
            ),
        )
    else:
        registration = replace(
            registration, document=registration.document.model_copy(update={"scope": other})
        )
    # When publishing, then the owner rejects the mismatch before catalog publication.
    with pytest.raises(RepositoryConflictError, match="brand_scope_binding_conflict"):
        _ = repository.register_brand(registration)
    assert repository.brand(editor, "brand.a") is None
