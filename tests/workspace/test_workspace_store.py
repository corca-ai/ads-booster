from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

if TYPE_CHECKING:
    from pathlib import Path

from trace_capture.workspace import (
    AssetCreate,
    ContextCreate,
    ContextKind,
    PrivateSessionCreate,
    RevisionConflictError,
    ScopedRecordNotFoundError,
    SqliteWorkspaceStore,
    WorkspaceId,
)


@pytest.fixture
def store(tmp_path: Path) -> SqliteWorkspaceStore:
    return SqliteWorkspaceStore(tmp_path)


def test_workspace_code_is_hashed_verified_and_rotated(
    store: SqliteWorkspaceStore,
) -> None:
    # Given
    provisioned = store.create_workspace("Trace team")
    old_code = provisioned.access_code

    # When
    rotated = store.rotate_workspace_code(
        provisioned.workspace.workspace_id,
        expected_version=provisioned.workspace.code_version,
    )

    # Then
    assert not store.verify_workspace_code(provisioned.workspace.workspace_id, old_code)
    assert store.verify_workspace_code(provisioned.workspace.workspace_id, rotated.access_code)
    database_bytes = store.database_path.read_bytes()
    assert old_code.encode() not in database_bytes
    assert rotated.access_code.encode() not in database_bytes


def test_invalid_and_malformed_codes_fail_closed(store: SqliteWorkspaceStore) -> None:
    # Given
    provisioned = store.create_workspace("Trace team")

    # When
    results = (
        store.verify_workspace_code(provisioned.workspace.workspace_id, ""),
        store.verify_workspace_code(provisioned.workspace.workspace_id, "not-the-code"),
        store.verify_workspace_code(WorkspaceId("missing-workspace"), provisioned.access_code),
    )

    # Then
    assert results == (False, False, False)


def test_member_invite_code_is_scoped_hashed_and_rotatable(
    store: SqliteWorkspaceStore,
) -> None:
    # Given
    first_workspace = store.create_workspace("First")
    second_workspace = store.create_workspace("Second")
    provisioned = store.create_member(first_workspace.workspace.workspace_id, "Ada")
    old_code = provisioned.invite_code

    # When
    rotated = store.rotate_member_code(
        first_workspace.workspace.workspace_id,
        provisioned.member.member_id,
        expected_version=provisioned.member.code_version,
    )

    # Then
    assert not store.verify_member_code(
        first_workspace.workspace.workspace_id,
        provisioned.member.member_id,
        old_code,
    )
    assert store.verify_member_code(
        first_workspace.workspace.workspace_id,
        provisioned.member.member_id,
        rotated.invite_code,
    )
    assert not store.verify_member_code(
        second_workspace.workspace.workspace_id,
        provisioned.member.member_id,
        rotated.invite_code,
    )


def test_two_members_share_context_but_sessions_remain_private(
    store: SqliteWorkspaceStore,
) -> None:
    # Given
    workspace = store.create_workspace("Trace team").workspace
    ada = store.create_member(workspace.workspace_id, "Ada").member
    grace = store.create_member(workspace.workspace_id, "Grace").member
    shared = store.create_context(
        workspace.workspace_id,
        ContextCreate(
            kind=ContextKind.PERSONA,
            title="Launch persona",
            body="Independent makers shipping their first campaign",
        ),
    )
    ada_session = store.create_private_session(
        workspace.workspace_id,
        ada.member_id,
        PrivateSessionCreate(title="Ada private", history=({"role": "user", "content": "A"},)),
    )
    grace_session = store.create_private_session(
        workspace.workspace_id,
        grace.member_id,
        PrivateSessionCreate(
            title="Grace private",
            history=({"role": "user", "content": "G"},),
        ),
    )

    # When
    contexts_for_ada = store.list_contexts(workspace.workspace_id)
    contexts_for_grace = store.list_contexts(workspace.workspace_id)

    # Then
    assert contexts_for_ada == contexts_for_grace == (shared,)
    assert (
        store.get_private_session(workspace.workspace_id, ada.member_id, ada_session.session_id)
        == ada_session
    )
    assert (
        store.get_private_session(workspace.workspace_id, grace.member_id, grace_session.session_id)
        == grace_session
    )


def test_wrong_member_cannot_read_private_session(store: SqliteWorkspaceStore) -> None:
    # Given
    workspace = store.create_workspace("Trace team").workspace
    owner = store.create_member(workspace.workspace_id, "Owner").member
    intruder = store.create_member(workspace.workspace_id, "Intruder").member
    session = store.create_private_session(
        workspace.workspace_id,
        owner.member_id,
        PrivateSessionCreate(title="Private", history=()),
    )

    # When / Then
    with pytest.raises(ScopedRecordNotFoundError):
        _ = store.get_private_session(
            workspace.workspace_id,
            intruder.member_id,
            session.session_id,
        )


def test_wrong_workspace_cannot_read_context_or_session(store: SqliteWorkspaceStore) -> None:
    # Given
    first = store.create_workspace("First").workspace
    second = store.create_workspace("Second").workspace
    member = store.create_member(first.workspace_id, "Owner").member
    context = store.create_context(
        first.workspace_id,
        ContextCreate(kind=ContextKind.RULE, title="Rule", body="Keep claims factual"),
    )
    session = store.create_private_session(
        first.workspace_id,
        member.member_id,
        PrivateSessionCreate(title="Private", history=()),
    )

    # When / Then
    with pytest.raises(ScopedRecordNotFoundError):
        _ = store.get_context(second.workspace_id, context.context_id)
    with pytest.raises(ScopedRecordNotFoundError):
        _ = store.get_private_session(second.workspace_id, member.member_id, session.session_id)


def test_asset_metadata_is_scoped_to_its_workspace(store: SqliteWorkspaceStore) -> None:
    # Given
    first = store.create_workspace("First").workspace
    second = store.create_workspace("Second").workspace
    context = store.create_context(
        first.workspace_id,
        ContextCreate(kind=ContextKind.REFERENCE, title="Reference", body="Source image"),
    )
    asset = store.create_asset(
        first.workspace_id,
        AssetCreate(
            context_id=context.context_id,
            filename="reference.png",
            media_type="image/png",
            relative_path="assets/reference.png",
            sha256="a" * 64,
            size_bytes=123,
        ),
    )

    # When / Then
    assert store.get_asset(first.workspace_id, asset.asset_id) == asset
    with pytest.raises(ScopedRecordNotFoundError):
        _ = store.get_asset(second.workspace_id, asset.asset_id)


def test_asset_metadata_supports_workspace_scoped_crud_and_safe_paths(
    store: SqliteWorkspaceStore,
) -> None:
    workspace = store.create_workspace("Trace team").workspace
    asset = store.create_asset(
        workspace.workspace_id,
        AssetCreate(
            filename="reference.png",
            media_type="image/png",
            relative_path="assets/reference.png",
            sha256="a" * 64,
            size_bytes=123,
        ),
    )

    listed = store.list_assets(workspace.workspace_id)
    updated = store.update_asset(
        workspace.workspace_id,
        asset.asset_id,
        AssetCreate(
            filename="reference-v2.png",
            media_type="image/png",
            relative_path="assets/reference-v2.png",
            sha256="b" * 64,
            size_bytes=456,
        ),
    )
    store.delete_asset(workspace.workspace_id, asset.asset_id)

    assert listed == (asset,)
    assert updated.filename == "reference-v2.png"
    assert updated.sha256 == "b" * 64
    with pytest.raises(ScopedRecordNotFoundError):
        _ = store.get_asset(workspace.workspace_id, asset.asset_id)
    with pytest.raises(ScopedRecordNotFoundError):
        _ = store.update_asset(
            workspace.workspace_id,
            asset.asset_id,
            AssetCreate(
                filename="missing.png",
                media_type="image/png",
                relative_path="assets/missing.png",
                sha256="c" * 64,
                size_bytes=1,
            ),
        )
    with pytest.raises(ValidationError, match="unsafe_asset_path"):
        _ = AssetCreate(
            filename="escape.png",
            media_type="image/png",
            relative_path="assets/../escape.png",
            sha256="d" * 64,
            size_bytes=1,
        )


def test_stale_session_update_is_rejected(store: SqliteWorkspaceStore) -> None:
    # Given
    workspace = store.create_workspace("Trace team").workspace
    member = store.create_member(workspace.workspace_id, "Ada").member
    session = store.create_private_session(
        workspace.workspace_id,
        member.member_id,
        PrivateSessionCreate(title="Private", history=()),
    )
    updated = store.update_private_session(
        workspace.workspace_id,
        member.member_id,
        session.session_id,
        expected_revision=session.revision,
        history=({"role": "user", "content": "first"},),
    )

    # When / Then
    with pytest.raises(RevisionConflictError):
        _ = store.update_private_session(
            workspace.workspace_id,
            member.member_id,
            session.session_id,
            expected_revision=session.revision,
            history=({"role": "user", "content": "stale"},),
        )
    assert (
        store.get_private_session(workspace.workspace_id, member.member_id, session.session_id)
        == updated
    )


def test_stale_context_update_is_rejected(store: SqliteWorkspaceStore) -> None:
    # Given
    workspace = store.create_workspace("Trace team").workspace
    context = store.create_context(
        workspace.workspace_id,
        ContextCreate(kind=ContextKind.RULE, title="Rule", body="Original"),
    )
    updated = store.update_context(
        workspace.workspace_id,
        context.context_id,
        ContextCreate(kind=ContextKind.RULE, title="Rule", body="Current"),
        expected_revision=context.revision,
    )

    # When / Then
    with pytest.raises(RevisionConflictError):
        _ = store.update_context(
            workspace.workspace_id,
            context.context_id,
            ContextCreate(kind=ContextKind.RULE, title="Rule", body="Stale"),
            expected_revision=context.revision,
        )
    assert store.get_context(workspace.workspace_id, context.context_id) == updated


def test_concurrent_session_updates_have_one_winner(store: SqliteWorkspaceStore) -> None:
    # Given
    workspace = store.create_workspace("Trace team").workspace
    member = store.create_member(workspace.workspace_id, "Ada").member
    session = store.create_private_session(
        workspace.workspace_id,
        member.member_id,
        PrivateSessionCreate(title="Private", history=()),
    )

    def update(content: str) -> str:
        try:
            _ = store.update_private_session(
                workspace.workspace_id,
                member.member_id,
                session.session_id,
                expected_revision=session.revision,
                history=({"role": "user", "content": content},),
            )
        except RevisionConflictError:
            return "conflict"
        return "updated"

    # When
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(update, ("first", "second")))

    # Then
    assert sorted(outcomes) == ["conflict", "updated"]
