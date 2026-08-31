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


def test_hosted_feedback_keeps_reviewed_revision_and_generation_provenance() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        migration_root = Path(__file__).parents[2] / "cloudflare" / "migrations"
        for migration in sorted(migration_root.glob("*.sql")):
            _ = connection.executescript(migration.read_text())

        _ = connection.execute(
            """INSERT INTO hosted_workspace_candidates
            (candidate_id, account_id, source, country, topic, caption, hypothesis,
             refs_json, principles_json, appium_prompt, image_inputs_json,
             generation_prompt_version, generation_prompt_sha256, generation_model,
             feedback_rules_json, status, revision, created_at, updated_at)
            VALUES ('candidate-1', 'trace_kr', 'auto', 'KR', 'topic', 'caption', 'hypothesis',
                    '["kr-study-day"]', '[1]', 'prompt', '{}',
                    'trace.workspace-generation.v2', ?, '@cf/openai/gpt-oss-20b', '[]',
                    'awaiting_review', 3, 1, 1)""",
            ("a" * 64,),
        )
        _ = connection.execute(
            """INSERT INTO hosted_workspace_feedback_events
            (event_id, account_id, candidate_id, stage, decision, rating, tags_json,
             candidate_revision, candidate_snapshot_json, candidate_snapshot_sha256,
             generation_prompt_version, generation_prompt_sha256, generation_model,
             feedback_rules_json, created_at)
            VALUES ('event-1', 'trace_kr', 'candidate-1', 'caption', 'rejected', 2,
                    '["컨셉이 약함"]', 3, '{"candidate_revision":3}', ?,
                    'trace.workspace-generation.v2', ?, '@cf/openai/gpt-oss-20b', '[]', 2)""",
            ("b" * 64, "a" * 64),
        )

        feedback = connection.execute(
            """SELECT candidate_revision, candidate_snapshot_sha256,
                      generation_prompt_version, generation_prompt_sha256,
                      generation_model, feedback_rules_json
               FROM hosted_workspace_feedback_events WHERE event_id = 'event-1'"""
        ).fetchone()

        assert feedback == (
            3,
            "b" * 64,
            "trace.workspace-generation.v2",
            "a" * 64,
            "@cf/openai/gpt-oss-20b",
            "[]",
        )


def test_feedback_loop_schema_keeps_exact_retry_binding_and_capability_gate() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        migration_root = Path(__file__).parents[2] / "cloudflare" / "migrations"
        for migration in sorted(migration_root.glob("*.sql")):
            _ = connection.executescript(migration.read_text())

        candidate_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(hosted_workspace_candidates)")
        }
        feedback_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(hosted_workspace_feedback_events)")
        }
        task_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(hosted_workspace_capture_tasks)")
        }

        assert {
            "last_image_feedback_event_id",
            "capture_feedback_context_sha256",
            "capture_feedback_application_sha256",
        } <= candidate_columns
        assert {"capture_task_id", "artifact_sha256"} <= feedback_columns
        assert "required_capability" in task_columns
