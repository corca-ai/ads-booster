from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from tests.knowledge.batch_runtime_support import batch_fixture
from tests.knowledge.change_test_fixtures import NOW

if TYPE_CHECKING:
    from pathlib import Path


def test_collection_denial_settles_item_and_continues(tmp_path: Path) -> None:
    fixture = batch_fixture(tmp_path)
    try:
        fixture.put("historical", at=NOW - timedelta(minutes=1))
        fixture.put("current")
        fixture.provider.release.set()
        assert fixture.runtime.tick(now=NOW + timedelta(seconds=60))
        fixture.runtime.reap(NOW + timedelta(seconds=60))
        assert fixture.states() == (("job.current", "completed"), ("job.historical", "failed"))
    finally:
        fixture.close()


def test_claim_denial_settles_only_unclaimed_batch(tmp_path: Path) -> None:
    fixture = batch_fixture(tmp_path)
    try:
        fixture.put("stale")
        assert fixture.runtime.tick(now=NOW)
        with fixture.repository.connection() as connection:
            _ = connection.execute("UPDATE workspaces SET policy_epoch=policy_epoch+1")
        _ = fixture.runtime.tick(now=NOW + timedelta(seconds=60))
        assert fixture.states() == (("job.stale", "failed"),)
        assert not fixture.runtime.active
    finally:
        fixture.close()
