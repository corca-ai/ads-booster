from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast

import pytest

# allow: SIZE_OK - this file is the executable contract for the ordered D1 migration chain.

MIGRATION_ROOT = Path(__file__).parents[2] / "cloudflare" / "migrations"


def apply_migrations(connection: sqlite3.Connection, *, through: str | None = None) -> None:
    _ = connection.execute("PRAGMA foreign_keys = ON")
    for migration in sorted(MIGRATION_ROOT.glob("*.sql")):
        if through is not None and migration.name > through:
            break
        _ = connection.executescript(migration.read_text())


def test_existing_hosted_rows_survive_the_legacy_migration_chain() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        apply_migrations(connection, through="0014_worker_caption_generation.sql")
        _ = connection.execute(
            """INSERT INTO hosted_workspace_candidates
            (candidate_id, account_id, source, country, topic, caption, hypothesis,
             refs_json, principles_json, appium_prompt, image_inputs_json, status,
             revision, created_at, updated_at)
            VALUES ('candidate-legacy', 'trace_kr', 'manual', 'KR', 'topic', 'caption',
                    'hypothesis', '[]', '[]', 'prompt', '{}', 'submitted', 1, 1, 1)"""
        )
        _ = connection.execute(
            """INSERT INTO mac_workers
            (worker_id, display_name, pool, token_sha256, state, created_at, updated_at)
            VALUES ('worker-legacy', 'Legacy Mac', 'appium', 'digest', 'active', 'now', 'now')"""
        )
        _ = connection.executescript((MIGRATION_ROOT / "0016_hosted_threads.sql").read_text())

        rows = cast(
            "tuple[str, str] | None",
            connection.execute(
                """SELECT
                    (SELECT status FROM hosted_workspace_candidates
                     WHERE candidate_id = 'candidate-legacy'),
                    (SELECT state FROM mac_workers WHERE worker_id = 'worker-legacy')"""
            ).fetchone(),
        )

        assert rows == ("submitted", "active")


def test_marketing_agent_foundation_preserves_existing_hosted_rows() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        apply_migrations(connection, through="0016_hosted_threads.sql")
        _ = connection.execute(
            """INSERT INTO hosted_workspace_accounts
            (account_id, display_name, country, language, timezone, morning_time,
             evening_time, revision, created_at, updated_at)
            VALUES ('trace_kr', 'Trace Korea', 'KR', 'ko', 'Asia/Seoul',
                    '07:30', '19:30', 1, 1, 1)"""
        )
        _ = connection.execute(
            """INSERT INTO hosted_workspace_candidates
            (candidate_id, account_id, source, country, topic, caption, hypothesis,
             refs_json, principles_json, appium_prompt, image_inputs_json, status,
             revision, created_at, updated_at)
            VALUES ('candidate-before-agent', 'trace_kr', 'manual', 'KR', 'topic', 'caption',
                    'hypothesis', '[]', '[]', 'prompt', '{}', 'submitted', 1, 1, 1)"""
        )

        _ = connection.executescript(
            (MIGRATION_ROOT / "0017_marketing_agent_foundation.sql").read_text()
        )

        candidate = connection.execute(
            """SELECT status, revision FROM hosted_workspace_candidates
            WHERE candidate_id = 'candidate-before-agent'"""
        ).fetchone()

        assert candidate == ("submitted", 1)


def test_shadow_marketing_campaign_cannot_create_tool_actions() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        apply_migrations(connection)
        _insert_marketing_agent_account_and_packet(connection)
        _ = connection.execute(
            """INSERT INTO hosted_marketing_campaigns
            (campaign_id, account_id, feature_packet_id, feature_packet_sha256,
             runtime_epoch, mode, state, business_outcome, created_at, updated_at)
            VALUES ('campaign-shadow', 'trace_kr', 'packet-1', ?, 'agent_v1', 'shadow',
                    'evidence_candidate', 'completed setup', 'now', 'now')""",
            ("a" * 64,),
        )

        with pytest.raises(sqlite3.IntegrityError, match="shadow campaigns"):
            _ = connection.execute(
                """INSERT INTO hosted_marketing_tool_actions
                (action_id, campaign_id, capability_id, effect_class, state, action_json,
                 action_sha256, idempotency_key, created_at, updated_at)
                VALUES ('action-1', 'campaign-shadow', 'capture.native_png', 'local_artifact',
                        'queued', '{}', ?, 'campaign-shadow:action-1', 'now', 'now')""",
                ("b" * 64,),
            )

        assert connection.execute(
            "SELECT count(*) FROM hosted_marketing_tool_actions"
        ).fetchone() == (0,)


def test_marketing_agent_events_have_single_ordered_revision_lineage() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        apply_migrations(connection)
        _insert_marketing_agent_account_and_packet(connection)
        _ = connection.execute(
            """INSERT INTO hosted_marketing_campaigns
            (campaign_id, account_id, feature_packet_id, feature_packet_sha256,
             runtime_epoch, mode, state, business_outcome, created_at, updated_at)
            VALUES ('campaign-1', 'trace_kr', 'packet-1', ?, 'agent_v1', 'shadow',
                    'evidence_candidate', 'completed setup', 'now', 'now')""",
            ("a" * 64,),
        )
        insert = """INSERT INTO hosted_marketing_run_events
            (event_id, campaign_id, sequence, prior_revision, resulting_revision, event_type,
             event_json, event_sha256, idempotency_key, correlation_id, event_time, observed_at,
             actor_type)
            VALUES (?, 'campaign-1', ?, ?, ?, 'campaign_created', '{}', ?, ?,
                    'correlation-1', 'now', 'now', 'runtime')"""
        _ = connection.execute(
            insert,
            ("event-1", 1, 0, 1, "c" * 64, "campaign-1:create"),
        )

        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                insert,
                ("event-duplicate-sequence", 1, 1, 2, "d" * 64, "campaign-1:other"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                insert,
                ("event-invalid-revision", 2, 1, 3, "e" * 64, "campaign-1:invalid"),
            )


def _insert_marketing_agent_account_and_packet(connection: sqlite3.Connection) -> None:
    _ = connection.execute(
        """INSERT INTO hosted_workspace_accounts
        (account_id, display_name, country, language, timezone, morning_time,
         evening_time, revision, created_at, updated_at)
        VALUES ('trace_kr', 'Trace Korea', 'KR', 'ko', 'Asia/Seoul',
                '07:30', '19:30', 1, 1, 1)"""
    )
    _ = connection.execute(
        """INSERT INTO hosted_marketing_feature_packets
        (packet_id, feature_id, schema_version, lifecycle, repository, mutable_ref,
         resolved_commit_sha, tree_sha, packet_json, packet_sha256, publication_allowed,
         observed_at, created_at)
        VALUES ('packet-1', 'trace.lockscreen.ai-concepts', 'trace.feature-evidence.v1',
                'source_candidate', 'corca-ai/Trace_iOS', 'refs/heads/develop', ?, ?, '{}', ?,
                0, 'now', 'now')""",
        ("b" * 40, "c" * 40, "a" * 64),
    )


def test_schema_allows_only_one_active_run_per_account() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        apply_migrations(connection)
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
        apply_migrations(connection)
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
        apply_migrations(connection)
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
        apply_migrations(connection)

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

        feedback = cast(
            "tuple[int, str, str, str, str, str] | None",
            connection.execute(
                """SELECT candidate_revision, candidate_snapshot_sha256,
                          generation_prompt_version, generation_prompt_sha256,
                          generation_model, feedback_rules_json
                   FROM hosted_workspace_feedback_events WHERE event_id = 'event-1'"""
            ).fetchone(),
        )

        assert feedback == (
            3,
            "b" * 64,
            "trace.workspace-generation.v2",
            "a" * 64,
            "@cf/openai/gpt-oss-20b",
            "[]",
        )


def test_threads_profiles_are_account_scoped_encrypted_and_default_off() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        apply_migrations(connection)
        _ = connection.execute(
            """INSERT INTO hosted_workspace_accounts
            (account_id, display_name, country, language, timezone, morning_time,
             evening_time, revision, created_at, updated_at)
            VALUES ('trace_kr', 'Trace Korea', 'KR', 'ko', 'Asia/Seoul',
                    '07:30', '19:30', 1, 1, 1)"""
        )
        settings = cast(
            "tuple[int, str | None] | None",
            connection.execute(
                """SELECT threads_auto_publish_enabled, default_threads_profile_id
                FROM hosted_workspace_accounts WHERE account_id = 'trace_kr'"""
            ).fetchone(),
        )
        oauth_insert = """INSERT INTO hosted_threads_oauth_states
            (oauth_state_id, account_id, state_sha256, redirect_uri, created_at, expires_at)
            VALUES (?, 'trace_kr', ?, 'https://workspace.example/callback', 't0', 't1')"""
        _ = connection.execute(oauth_insert, ("oauth-1", "f" * 64))
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """INSERT INTO hosted_workspace_accounts
                (account_id, display_name, country, language, timezone, morning_time,
                 evening_time, revision, created_at, updated_at, default_threads_profile_id)
                VALUES ('trace_invalid', 'Invalid', 'KR', 'ko', 'Asia/Seoul',
                        '07:30', '19:30', 1, 1, 1, 'missing-profile')"""
            )
        profile_insert = """INSERT INTO hosted_threads_profiles
            (profile_id, account_id, threads_user_id, username, scopes_json,
             token_ciphertext, token_nonce, token_key_version, token_expires_at,
             state, created_at, updated_at)
            VALUES (?, ?, ?, ?, '["threads_basic","threads_content_publish"]',
                    ?, ?, 'v1', '2026-12-01T00:00:00Z', ?, 'now', 'now')"""
        _ = connection.executemany(
            profile_insert,
            [
                (
                    "profile-a",
                    "trace_kr",
                    "threads-a",
                    "trace_a",
                    b"cipher-a",
                    b"nonce-a",
                    "active",
                ),
                (
                    "profile-b",
                    "trace_kr",
                    "threads-b",
                    "trace_b",
                    b"cipher-b",
                    b"nonce-b",
                    "active",
                ),
            ],
        )
        _ = connection.execute(
            """UPDATE hosted_workspace_accounts SET default_threads_profile_id = 'profile-a'
            WHERE account_id = 'trace_kr'"""
        )

        assert settings == (0, None)
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                profile_insert,
                ("profile-duplicate", "trace_kr", "threads-a", "other", b"c", b"n", "active"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                profile_insert,
                ("profile-invalid", "trace_kr", "threads-c", "other", b"c", b"n", "invalid"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                profile_insert,
                ("profile-missing", "missing", "threads-c", "other", b"c", b"n", "active"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """INSERT INTO hosted_threads_profiles
                (profile_id, account_id, threads_user_id, username, scopes_json,
                 token_ciphertext, token_nonce, token_key_version, state, created_at, updated_at)
                VALUES ('profile-key-invalid', 'trace_kr', 'threads-key-invalid', 'other', '[]',
                        X'01', X'02', '1', 'active', 'now', 'now')"""
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(oauth_insert, ("oauth-2", "f" * 64))
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                "UPDATE hosted_workspace_accounts SET default_threads_profile_id = 'missing'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                "DELETE FROM hosted_threads_profiles WHERE profile_id = 'profile-a'"
            )
        _ = connection.execute("DELETE FROM hosted_threads_profiles WHERE profile_id = 'profile-b'")
        default_binding = cast(
            "tuple[int] | None",
            connection.execute(
                """SELECT COUNT(*) FROM hosted_workspace_accounts AS account
                JOIN hosted_threads_profiles AS profile
                  ON profile.account_id = account.account_id
                 AND profile.profile_id = account.default_threads_profile_id"""
            ).fetchone(),
        )
        profile_columns = cast(
            "list[tuple[int, str, str, int, str | None, int]]",
            connection.execute("PRAGMA table_info(hosted_threads_profiles)").fetchall(),
        )
        assert default_binding == (1,)
        assert "access_token" not in {row[1] for row in profile_columns}


def test_threads_publications_and_engagement_are_durable_and_duplicate_safe() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        apply_migrations(connection)
        _ = connection.execute(
            """INSERT INTO hosted_workspace_accounts
            (account_id, display_name, country, language, timezone, morning_time,
             evening_time, revision, created_at, updated_at)
            VALUES ('trace_kr', 'Trace Korea', 'KR', 'ko', 'Asia/Seoul',
                    '07:30', '19:30', 1, 1, 1)"""
        )
        _ = connection.execute(
            """INSERT INTO hosted_workspace_candidates
            (candidate_id, account_id, source, country, topic, caption, hypothesis,
             refs_json, principles_json, appium_prompt, image_inputs_json, image_key,
             image_sha256, status, revision, created_at, updated_at)
            VALUES ('candidate-1', 'trace_kr', 'auto', 'KR', 'topic', 'caption', 'why',
                    '[]', '[]', 'prompt', '{}', 'image.png', ?, 'submitted', 3, 1, 1)""",
            ("a" * 64,),
        )
        _ = connection.execute(
            """INSERT INTO hosted_threads_profiles
            (profile_id, account_id, threads_user_id, username, scopes_json,
             token_ciphertext, token_nonce, token_key_version, state, created_at, updated_at)
            VALUES ('profile-a', 'trace_kr', 'threads-a', 'trace_a', '[]',
                    X'01', X'02', 'v1', 'active', 'now', 'now')"""
        )
        _ = connection.execute(
            """INSERT INTO hosted_workspace_accounts
            (account_id, display_name, country, language, timezone, morning_time,
             evening_time, revision, created_at, updated_at)
            VALUES ('trace_jp', 'Trace Japan', 'JP', 'ja', 'Asia/Tokyo',
                    '07:30', '19:30', 1, 1, 1)"""
        )
        _ = connection.execute(
            """INSERT INTO hosted_workspace_candidates
            (candidate_id, account_id, source, country, topic, caption, hypothesis,
             refs_json, principles_json, appium_prompt, image_inputs_json, status,
             revision, created_at, updated_at)
            VALUES ('candidate-foreign', 'trace_jp', 'auto', 'JP', 'topic', 'caption',
                    'why', '[]', '[]', 'prompt', '{}', 'submitted', 1, 1, 1)"""
        )
        _ = connection.execute(
            """INSERT INTO hosted_workspace_candidates
            (candidate_id, account_id, source, country, topic, caption, hypothesis,
             refs_json, principles_json, appium_prompt, image_inputs_json, status,
             revision, created_at, updated_at)
            VALUES ('candidate-2', 'trace_kr', 'auto', 'KR', 'topic', 'caption',
                    'why', '[]', '[]', 'prompt', '{}', 'submitted', 1, 1, 1)"""
        )
        _ = connection.execute(
            """UPDATE hosted_workspace_candidates SET threads_profile_id = 'profile-a'
            WHERE candidate_id = 'candidate-1'"""
        )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                "DELETE FROM hosted_threads_profiles WHERE profile_id = 'profile-a'"
            )
        publication_insert = """INSERT INTO hosted_threads_publications
            (publication_id, account_id, candidate_id, candidate_revision, profile_id,
             state, caption_snapshot, image_key_snapshot, image_sha256_snapshot,
             timezone_snapshot, posting_slot_snapshot, scheduled_at, created_at, updated_at)
            VALUES (?, 'trace_kr', 'candidate-1', ?, 'profile-a', ?, 'caption',
                    'image.png', ?, 'Asia/Seoul', 'evening',
                    '2026-09-01T10:30:00Z', 'now', 'now')"""
        _ = connection.execute(publication_insert, ("publication-1", 3, "scheduled", "a" * 64))
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """UPDATE hosted_threads_publications SET candidate_revision = 4
                WHERE publication_id = 'publication-1'"""
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """UPDATE hosted_threads_publications
                SET candidate_id = 'candidate-2', candidate_revision = 1
                WHERE publication_id = 'publication-1'"""
            )
        _ = connection.execute(
            """UPDATE hosted_threads_publications
            SET candidate_id = 'candidate-1', candidate_revision = 3
            WHERE publication_id = 'publication-1'"""
        )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """UPDATE hosted_threads_publications
                SET candidate_id = 'candidate-foreign' WHERE publication_id = 'publication-1'"""
            )
        states = (
            "canceled",
            "creating_container",
            "container_ready",
            "publishing",
            "published",
            "unknown_side_effect",
            "failed",
            "rate_limited",
            "auth_required",
            "unavailable",
        )
        _ = connection.executemany(
            publication_insert,
            [
                (f"publication-{index}", index, state, "a" * 64)
                for index, state in enumerate(states, 10)
            ],
        )
        metric_insert = """INSERT INTO hosted_threads_metric_snapshots
            (snapshot_id, account_id, publication_id, observed_at, views, likes, replies,
             reposts, quotes, shares, delete_after)
            VALUES (?, 'trace_kr', 'publication-1', ?, ?, 2, 3, 4, 5, 6, '2027-09-01')"""
        _ = connection.executemany(metric_insert, [("metric-1", "t1", 10), ("metric-2", "t2", 8)])
        reply_insert = """INSERT INTO hosted_threads_replies
            (reply_id, account_id, publication_id, threads_reply_id, root_threads_post_id,
             body, replied_at, first_seen_at, last_seen_at, delete_after)
            VALUES (?, 'trace_kr', 'publication-1', 'reply-external', 'post-1',
                    'reply text', 't0', 't1', 't1', '2026-10-01')"""
        _ = connection.execute(reply_insert, ("reply-1",))

        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                publication_insert, ("publication-duplicate", 3, "scheduled", "a" * 64)
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                publication_insert, ("publication-invalid", 99, "invalid", "a" * 64)
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(reply_insert, ("reply-2",))
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(metric_insert, ("metric-3", "t1", 7))
        assert connection.execute(
            "SELECT COUNT(*), MIN(views), MAX(views) FROM hosted_threads_metric_snapshots"
        ).fetchone() == (2, 8, 10)
        indexes = {
            row[0]
            for row in cast(
                "list[tuple[str]]",
                connection.execute(
                    """SELECT name FROM sqlite_master
                    WHERE type = 'index' AND name LIKE 'hosted_threads_%'"""
                ).fetchall(),
            )
        }
        assert {
            "hosted_threads_oauth_states_expiry",
            "hosted_threads_profiles_token_expiry",
            "hosted_threads_publications_scheduling",
            "hosted_threads_publications_poll",
            "hosted_threads_metric_snapshots_cleanup",
            "hosted_threads_replies_cleanup",
        } <= indexes
        _ = connection.execute(
            """UPDATE hosted_workspace_candidates SET threads_profile_id = NULL
            WHERE candidate_id = 'candidate-1'"""
        )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                "DELETE FROM hosted_threads_profiles WHERE profile_id = 'profile-a'"
            )
        _ = connection.execute(
            "DELETE FROM hosted_threads_publications WHERE profile_id = 'profile-a'"
        )
        _ = connection.execute("DELETE FROM hosted_threads_profiles WHERE profile_id = 'profile-a'")
        dangling = cast(
            "tuple[int, int] | None",
            connection.execute(
                """SELECT
                    (SELECT COUNT(*) FROM hosted_workspace_candidates
                     WHERE threads_profile_id IS NOT NULL),
                    (SELECT COUNT(*) FROM hosted_threads_publications
                     WHERE profile_id = 'profile-a')"""
            ).fetchone(),
        )
        assert dangling == (0, 0)


def test_threads_profile_cannot_move_to_a_different_account_when_bound() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        apply_migrations(connection)
        _ = connection.executemany(
            """INSERT INTO hosted_workspace_accounts
            (account_id, display_name, country, language, timezone, morning_time,
             evening_time, revision, created_at, updated_at)
            VALUES (?, ?, 'KR', 'ko', 'Asia/Seoul', '07:30', '19:30', 1, 'now', 'now')""",
            [("account-a", "Account A"), ("account-b", "Account B")],
        )
        _ = connection.execute(
            """INSERT INTO hosted_threads_profiles
            (profile_id, account_id, threads_user_id, username, scopes_json,
             token_ciphertext, token_nonce, token_key_version, state, created_at, updated_at)
            VALUES ('profile-a', 'account-a', 'external-a', 'account_a', '[]',
                    X'01', X'02', 'v1', 'active', 'now', 'now')"""
        )
        _ = connection.execute(
            """UPDATE hosted_workspace_accounts SET default_threads_profile_id = 'profile-a'
            WHERE account_id = 'account-a'"""
        )

        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """UPDATE hosted_threads_profiles SET account_id = 'account-b'
                WHERE profile_id = 'profile-a'"""
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """UPDATE hosted_threads_profiles SET profile_id = 'profile-moved'
                WHERE profile_id = 'profile-a'"""
            )


def test_threads_publication_candidate_cannot_move_to_a_different_account() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        apply_migrations(connection)
        _ = connection.executemany(
            """INSERT INTO hosted_workspace_accounts
            (account_id, display_name, country, language, timezone, morning_time,
             evening_time, revision, created_at, updated_at)
            VALUES (?, ?, 'KR', 'ko', 'Asia/Seoul', '07:30', '19:30', 1, 'now', 'now')""",
            [("account-a", "Account A"), ("account-b", "Account B")],
        )
        _ = connection.execute(
            """INSERT INTO hosted_workspace_candidates
            (candidate_id, account_id, source, country, topic, caption, hypothesis,
             refs_json, principles_json, appium_prompt, image_inputs_json, image_key,
             image_sha256, status, revision, created_at, updated_at)
            VALUES ('candidate-a', 'account-a', 'auto', 'KR', 'topic', 'caption', 'why',
                    '[]', '[]', 'prompt', '{}', 'image.png', ?, 'submitted', 1, 'now', 'now')""",
            ("a" * 64,),
        )
        _ = connection.execute(
            """INSERT INTO hosted_threads_profiles
            (profile_id, account_id, threads_user_id, username, scopes_json,
             token_ciphertext, token_nonce, token_key_version, state, created_at, updated_at)
            VALUES ('profile-a', 'account-a', 'external-a', 'account_a', '[]',
                    X'01', X'02', 'v1', 'active', 'now', 'now')"""
        )
        _ = connection.execute(
            """INSERT INTO hosted_threads_publications
            (publication_id, account_id, candidate_id, candidate_revision, profile_id,
             state, caption_snapshot, image_key_snapshot, image_sha256_snapshot,
             timezone_snapshot, posting_slot_snapshot, scheduled_at, created_at, updated_at)
            VALUES ('publication-a', 'account-a', 'candidate-a', 1, 'profile-a', 'scheduled',
                    'caption', 'image.png', ?, 'Asia/Seoul', 'morning', 'later', 'now', 'now')""",
            ("a" * 64,),
        )

        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """UPDATE hosted_workspace_candidates SET account_id = 'account-b'
                WHERE candidate_id = 'candidate-a'"""
            )


def test_threads_oauth_reconnect_profile_is_account_scoped_and_deleted_with_profile() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        apply_migrations(connection)
        _ = connection.executemany(
            """INSERT INTO hosted_workspace_accounts
            (account_id, display_name, country, language, timezone, morning_time,
             evening_time, revision, created_at, updated_at)
            VALUES (?, ?, 'KR', 'ko', 'Asia/Seoul', '07:30', '19:30', 1, 'now', 'now')""",
            [("account-a", "Account A"), ("account-b", "Account B")],
        )
        _ = connection.executemany(
            """INSERT INTO hosted_threads_profiles
            (profile_id, account_id, threads_user_id, username, scopes_json,
             token_ciphertext, token_nonce, token_key_version, state, created_at, updated_at)
            VALUES (?, ?, ?, ?, '[]', X'01', X'02', 'v1', 'active', 'now', 'now')""",
            [
                ("profile-a", "account-a", "external-a", "account_a"),
                ("profile-b", "account-b", "external-b", "account_b"),
            ],
        )
        oauth_insert = """INSERT INTO hosted_threads_oauth_states
            (oauth_state_id, account_id, state_sha256, reconnect_profile_id, redirect_uri,
             created_at, expires_at)
            VALUES (?, 'account-a', ?, ?, 'https://workspace.example/callback', 't0', 't1')"""
        _ = connection.execute(oauth_insert, ("oauth-a", "a" * 64, "profile-a"))

        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(oauth_insert, ("oauth-b", "b" * 64, "profile-b"))
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(oauth_insert, ("oauth-missing", "c" * 64, "missing"))
        _ = connection.execute("DELETE FROM hosted_threads_profiles WHERE profile_id = 'profile-a'")

        assert connection.execute(
            "SELECT COUNT(*) FROM hosted_threads_oauth_states"
        ).fetchone() == (0,)


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
