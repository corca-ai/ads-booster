from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast

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


def test_schema_deduplicates_workspace_review_events() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        migration_root = Path(__file__).parents[2] / "cloudflare" / "migrations"
        for migration in sorted(migration_root.glob("*.sql")):
            _ = connection.executescript(migration.read_text())
        _ = connection.execute(
            """INSERT INTO shared_instructions
            (body, body_sha256, active, created_at) VALUES ('instruction', 'digest', 1, 'now')"""
        )
        _ = connection.execute(
            """INSERT INTO marketing_accounts
            (account_id, channel, country, timezone, schedule_minutes,
             instruction_revision, credential_ref, adapter_mode, enabled,
             next_run_at, config_json, created_at, updated_at)
            VALUES ('trace_kr', 'threads', 'KR', 'Asia/Seoul', 60, 1, NULL, 'simulation', 1,
                    'now', '{}', 'now', 'now')"""
        )
        _ = connection.execute(
            """INSERT INTO marketing_runs
            (run_id, account_id, workflow_instance_id, state, created_at, updated_at)
            VALUES ('run-1', 'trace_kr', 'run-1', 'awaiting_candidate_approval', 'now', 'now')"""
        )
        insert = """INSERT INTO marketing_review_event_receipts
            (approval_id, run_id, account_id, phase, body_json, created_at, updated_at)
            VALUES ('run-1:candidates', 'run-1', 'trace_kr', 'candidates', '{}', 'now', 'now')"""
        _ = connection.execute(insert)

        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(insert)


def test_dynamic_mac_workers_have_revocable_identities_and_single_task_leases() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        migration_root = Path(__file__).parents[2] / "cloudflare" / "migrations"
        for migration in sorted(migration_root.glob("*.sql")):
            _ = connection.executescript(migration.read_text())
        _ = connection.execute(
            """INSERT INTO mac_workers
            (worker_id, display_name, pool, token_sha256, state, created_at, updated_at)
            VALUES ('worker-1', 'Studio Mac', 'appium', 'digest-1', 'active', 'now', 'now')"""
        )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """INSERT INTO mac_workers
                (worker_id, display_name, pool, token_sha256, state, created_at, updated_at)
                VALUES ('worker-2', 'Backup Mac', 'appium', 'digest-1', 'active', 'now', 'now')"""
            )
        _ = connection.execute(
            """INSERT INTO hosted_workspace_capture_tasks
            (task_id, run_id, account_id, candidate_id, candidate_revision, idempotency_key,
             task_json, state, dispatch_mode, worker_id, lease_id, lease_expires_at,
             lease_started_at, lease_accepted_at, attempt_count, created_at, updated_at)
            VALUES ('task-1', 'run-1', 'trace_kr', 'candidate-1', 2, 'hosted:1', '{}',
                    'queued', 'worker_broker', 'worker-1', 'lease-1', 'later', 'start',
                    'accepted', 1, 'now', 'now')"""
        )
        task = cast(
            "tuple[str, str, str, str, str, int] | None",
            connection.execute(
                """SELECT dispatch_mode, worker_id, lease_id, lease_started_at,
                    lease_accepted_at, attempt_count
                FROM hosted_workspace_capture_tasks WHERE task_id = 'task-1'"""
            ).fetchone(),
        )

        assert task == ("worker_broker", "worker-1", "lease-1", "start", "accepted", 1)


def test_feedback_rules_are_scoped_and_reversible() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        migration_root = Path(__file__).parents[2] / "cloudflare" / "migrations"
        for migration in sorted(migration_root.glob("*.sql")):
            _ = connection.executescript(migration.read_text())

        rows = cast(
            "list[tuple[int, str, str, int, object, object]]",
            connection.execute("PRAGMA table_info(hosted_workspace_feedback_events)").fetchall(),
        )
        columns = {row[1] for row in rows}
        assert {
            "candidate_revision",
            "capture_task_id",
            "artifact_sha256",
            "generation_provenance_json",
            "context_snapshot_json",
            "context_snapshot_sha256",
        } <= columns

        _ = connection.execute(
            """INSERT INTO hosted_workspace_feedback_rules
            (rule_id, account_id, profile_scope, stage, target, tag, instruction,
             evidence_count, enabled, created_at, updated_at)
            VALUES ('rule-1', 'trace_kr', 'profile-1', 'image', 'visual_quality',
                    '이미지 품질·AI 티', '자연스러운 이미지 품질을 우선할 것', 3, 1, 1, 1)"""
        )
        _ = connection.execute(
            "UPDATE hosted_workspace_feedback_rules SET enabled = 0 WHERE rule_id = 'rule-1'"
        )
        assert connection.execute(
            "SELECT enabled FROM hosted_workspace_feedback_rules WHERE rule_id = 'rule-1'"
        ).fetchone() == (0,)
