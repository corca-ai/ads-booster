from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest


def test_schema_allows_only_one_active_run_per_account() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        migration_root = Path(__file__).parents[2] / "cloudflare" / "migrations"
        for migration in sorted(migration_root.glob("*.sql")):
            _ = connection.executescript(migration.read_text())
        _ = connection.execute(
            """INSERT INTO shared_instructions
            (body, body_sha256, active, created_at) VALUES (?, ?, 1, ?)""",
            ("instruction", "digest", "2026-08-25T00:00:00Z"),
        )
        _ = connection.execute(
            """INSERT INTO marketing_accounts
            (account_id, channel, country, timezone, schedule_minutes,
             instruction_revision, credential_ref, adapter_mode, enabled,
             next_run_at, config_json, created_at, updated_at)
            VALUES ('trace_kr', 'threads', 'KR', 'Asia/Seoul', 60, 1, NULL, 'simulation', 1,
                    '2026-08-25T00:00:00Z', '{}', '2026-08-25T00:00:00Z',
                    '2026-08-25T00:00:00Z')"""
        )
        _ = connection.execute(
            """INSERT INTO marketing_runs
            (run_id, account_id, workflow_instance_id, state, created_at, updated_at)
            VALUES ('run-1', 'trace_kr', 'run-1', 'scheduled', ?, ?)""",
            ("2026-08-25T00:00:00Z", "2026-08-25T00:00:00Z"),
        )

        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """INSERT INTO marketing_runs
                (run_id, account_id, workflow_instance_id, state, created_at, updated_at)
                VALUES ('run-2', 'trace_kr', 'run-2', 'scheduled', ?, ?)""",
                ("2026-08-25T00:01:00Z", "2026-08-25T00:01:00Z"),
            )

        _ = connection.execute("UPDATE marketing_runs SET state = 'failed' WHERE run_id = 'run-1'")
        _ = connection.execute(
            """INSERT INTO marketing_runs
            (run_id, account_id, workflow_instance_id, state, created_at, updated_at)
            VALUES ('run-2', 'trace_kr', 'run-2', 'scheduled', ?, ?)""",
            ("2026-08-25T00:01:00Z", "2026-08-25T00:01:00Z"),
        )
