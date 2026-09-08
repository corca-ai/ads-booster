from __future__ import annotations

import os
from dataclasses import replace
from datetime import timedelta
from multiprocessing import get_context
from typing import TYPE_CHECKING

import pytest

from ads_booster.knowledge.batch_actor import load_batch_actor
from ads_booster.knowledge.batch_curation import BatchCurationCoordinator
from ads_booster.knowledge.contracts import ActorContext
from ads_booster.knowledge.errors import AccessDeniedError
from ads_booster.knowledge.grant_policy import authorize_read, authorize_write
from ads_booster.knowledge.maintenance import KnowledgeOwner, KnowledgeOwnerBusyError
from ads_booster.knowledge.repository import MembershipRole
from ads_booster.knowledge.repository_batch_recovery import recover_running_batches
from tests.knowledge.batch_runtime_support import batch_fixture
from tests.knowledge.change_test_fixtures import NOW, PRIVATE_SCOPE

if TYPE_CHECKING:
    from pathlib import Path


def test_private_batch_uses_registered_conversation_identity(tmp_path: Path) -> None:
    fixture = batch_fixture(tmp_path)
    private = ActorContext(
        actor_id="actor.private",
        workspace_id=PRIVATE_SCOPE.workspace_id,
        member_id="member.private",
        session_id="session.private",
        conversation_scope=PRIVATE_SCOPE,
        grants=tuple(
            grant.model_copy(
                update={
                    "grant_id": f"private.{grant.grant_id}",
                    "scope": PRIVATE_SCOPE,
                }
            )
            for grant in fixture.actor.grants
        ),
        policy_epoch=fixture.actor.policy_epoch,
        authenticated_at=NOW,
    )
    fixture.repository.register_actor(private, MembershipRole.EDITOR)
    try:
        replace(fixture, actor=private).put("private")
        fixture.provider.release.set()
        assert fixture.runtime.tick(now=NOW + timedelta(seconds=60))
        batch_actor = load_batch_actor(fixture.repository, PRIVATE_SCOPE, NOW)
        assert batch_actor.actor_id == private.actor_id
        assert batch_actor.grants == private.grants
        fixture.runtime.reap(NOW + timedelta(seconds=60))
        assert fixture.states() == (("job.private", "completed"),)
    finally:
        fixture.close()


def test_private_batch_refuses_closed_session(tmp_path: Path) -> None:
    fixture = batch_fixture(tmp_path)
    try:
        scope = PRIVATE_SCOPE.model_copy(
            update={
                "member_id": fixture.actor.member_id,
                "session_id": fixture.actor.session_id,
            }
        )
        private = fixture.actor.model_copy(
            update={
                "conversation_scope": scope,
                "grants": tuple(
                    grant.model_copy(update={"scope": scope}) for grant in fixture.actor.grants
                ),
            }
        )
        fixture.repository.register_actor(private, MembershipRole.EDITOR)
        replace(fixture, actor=private).put("closed")
        with fixture.repository.connection() as connection:
            _ = connection.execute("UPDATE sessions SET state='closed'")
        fixture.put("valid")
        assert fixture.runtime.tick(now=NOW)
        fixture.provider.release.set()
        assert fixture.runtime.tick(now=NOW + timedelta(seconds=60))
        fixture.runtime.reap(NOW + timedelta(seconds=60))
        assert fixture.states() == (("job.closed", "failed"), ("job.valid", "completed"))
    finally:
        fixture.close()


def test_new_owner_recovers_running_batch(tmp_path: Path) -> None:
    fixture = batch_fixture(tmp_path)
    try:
        fixture.put("crash")
        assert fixture.runtime.tick(now=NOW)

        def crash_after_claim() -> None:
            owner = KnowledgeOwner(tmp_path, "crashed-service")
            owner.acquire()
            claimed = BatchCurationCoordinator(fixture.repository).claim(
                fixture.actor,
                NOW + timedelta(seconds=60),
            )
            assert claimed is not None
            os._exit(23)

        process = get_context("fork").Process(target=crash_after_claim)
        process.start()
        process.join(timeout=5)
        assert process.exitcode == 23
        with KnowledgeOwner(tmp_path, "restarted-service") as owner:
            assert (
                recover_running_batches(fixture.repository, fixture.actor.workspace_id, owner) == 1
            )
        assert fixture.states() == (("job.crash", "queued"),)
        fixture.provider.release.set()
        assert fixture.runtime.tick(now=NOW + timedelta(seconds=60))
        fixture.runtime.reap(NOW + timedelta(seconds=60))
        assert fixture.states() == (("job.crash", "completed"),)
        with KnowledgeOwner(tmp_path, "restarted-service") as owner:
            assert (
                recover_running_batches(fixture.repository, fixture.actor.workspace_id, owner) == 0
            )
    finally:
        fixture.close()


def test_recovery_requires_owner_lock(tmp_path: Path) -> None:
    fixture = batch_fixture(tmp_path)
    try:
        fixture.put("active")
        assert fixture.runtime.tick(now=NOW)
        assert (
            BatchCurationCoordinator(fixture.repository).claim(
                fixture.actor,
                NOW + timedelta(seconds=60),
            )
            is not None
        )
        with KnowledgeOwner(tmp_path, "active-service"):
            contender = KnowledgeOwner(tmp_path, "other-service")
            with pytest.raises(KnowledgeOwnerBusyError):
                contender.acquire()
            with pytest.raises(RuntimeError, match="owner_not_acquired"):
                _ = recover_running_batches(
                    fixture.repository, fixture.actor.workspace_id, contender
                )
            assert fixture.states() == (("job.active", "running"),)
    finally:
        fixture.close()


@pytest.mark.parametrize("revocation", ["grant", "epoch", "membership", "session"])
def test_private_batch_rechecks_authority_before_claim(tmp_path: Path, revocation: str) -> None:
    fixture = batch_fixture(tmp_path)
    scope = PRIVATE_SCOPE.model_copy(
        update={
            "member_id": fixture.actor.member_id,
            "session_id": fixture.actor.session_id,
        }
    )
    private = fixture.actor.model_copy(
        update={
            "conversation_scope": scope,
            "grants": tuple(
                grant.model_copy(update={"scope": scope}) for grant in fixture.actor.grants
            ),
        }
    )
    fixture.repository.register_actor(private, MembershipRole.EDITOR)
    try:
        replace(fixture, actor=private).put("revoked")
        assert fixture.runtime.tick(now=NOW)
        with fixture.repository.connection() as connection:
            statements = {
                "grant": "DELETE FROM scope_grants WHERE capability='write'",
                "epoch": "UPDATE workspaces SET policy_epoch=policy_epoch+1",
                "membership": "UPDATE memberships SET state='disabled'",
                "session": "UPDATE sessions SET state='closed'",
            }
            _ = connection.execute(statements[revocation])
        _ = fixture.runtime.tick(now=NOW + timedelta(seconds=60))
        assert not fixture.runtime.active
        assert fixture.states() == (("job.revoked", "failed"),)
    finally:
        fixture.close()


def test_recovery_preserves_committed_receipts(tmp_path: Path) -> None:
    fixture = batch_fixture(tmp_path)
    try:
        fixture.provider.release.set()
        fixture.put("committed")
        assert fixture.runtime.tick(now=NOW + timedelta(seconds=60))
        fixture.runtime.reap(NOW + timedelta(seconds=60))
        with fixture.repository.connection() as connection:
            before = connection.execute("SELECT receipt_json FROM batch_items").fetchall()
        fixture.put("unfinished")
        assert fixture.runtime.tick(now=NOW)
        assert (
            BatchCurationCoordinator(fixture.repository).claim(
                fixture.actor,
                NOW + timedelta(seconds=60),
            )
            is not None
        )
        with KnowledgeOwner(tmp_path, "new-owner") as owner:
            assert (
                recover_running_batches(fixture.repository, fixture.actor.workspace_id, owner) == 1
            )
        assert fixture.states() == (("job.committed", "completed"), ("job.unfinished", "queued"))
        with fixture.repository.connection() as connection:
            after = connection.execute(
                "SELECT receipt_json FROM batch_items WHERE event_id='event.committed'"
            ).fetchall()
        assert after == before
        assert fixture.provider.calls.get(timeout=1) == ("job.committed",)
    finally:
        fixture.close()


def test_private_actor_excludes_other_session_grants(tmp_path: Path) -> None:
    fixture = batch_fixture(tmp_path)
    scope = PRIVATE_SCOPE.model_copy(
        update={
            "member_id": fixture.actor.member_id,
            "session_id": fixture.actor.session_id,
        }
    )
    other = scope.model_copy(update={"session_id": "session.other"})
    try:
        for index, private_scope in enumerate((scope, other)):
            fixture.repository.register_actor(
                fixture.actor.model_copy(
                    update={
                        "session_id": private_scope.session_id,
                        "conversation_scope": private_scope,
                        "grants": tuple(
                            grant.model_copy(
                                update={
                                    "grant_id": f"private.{index}.{grant.grant_id}",
                                    "scope": private_scope,
                                }
                            )
                            for grant in fixture.actor.grants
                        ),
                    }
                ),
                MembershipRole.EDITOR,
            )
        loaded = load_batch_actor(fixture.repository, scope, NOW)
        assert authorize_read(actor=loaded, target_scope=scope, at=NOW)
        assert authorize_read(actor=loaded, target_scope=fixture.actor.conversation_scope, at=NOW)
        with pytest.raises(AccessDeniedError, match="required_grant_missing"):
            _ = authorize_read(actor=loaded, target_scope=other, at=NOW)
        with pytest.raises(AccessDeniedError, match="private_shared_write_forbidden"):
            _ = authorize_write(actor=loaded, target_scope=fixture.actor.conversation_scope, at=NOW)
    finally:
        fixture.close()
