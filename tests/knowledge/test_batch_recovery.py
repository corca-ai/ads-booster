from __future__ import annotations

import os
from datetime import timedelta
from multiprocessing import get_context
from typing import TYPE_CHECKING

import pytest

from ads_booster.knowledge.batch_curation import BatchCurationCoordinator
from ads_booster.knowledge.maintenance import KnowledgeOwner, KnowledgeOwnerBusyError
from ads_booster.knowledge.repository_batch_recovery import recover_running_batches
from tests.knowledge.batch_runtime_support import batch_fixture
from tests.knowledge.change_test_fixtures import NOW

if TYPE_CHECKING:
    from pathlib import Path


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
