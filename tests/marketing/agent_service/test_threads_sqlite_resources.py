from __future__ import annotations

import gc
import warnings
from pathlib import Path

from ads_booster.threads.drafts import ThreadsDraftRepository
from ads_booster.threads.publications import ThreadsPublicationRepository


def test_threads_repository_construction_closes_sqlite_connections(tmp_path: Path) -> None:
    # Given two repositories composed for every installed agent service.
    database_path = tmp_path / "agent-service.sqlite3"

    # When their schema initialization connections become collectible.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        ThreadsPublicationRepository(database_path)
        ThreadsDraftRepository(database_path)
        _ = gc.collect()

    # Then no database connection is left for the garbage collector to close.
    leaks = [warning for warning in caught if warning.category is ResourceWarning]
    assert leaks == []
