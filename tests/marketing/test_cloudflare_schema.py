from __future__ import annotations

import json
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
        _ = connection.execute(
            """INSERT INTO hosted_workspace_capture_tasks
            (task_id, run_id, account_id, candidate_id, candidate_revision, idempotency_key,
             task_json, state, result_json, callback_id, last_dispatched_at, created_at, updated_at,
             dispatch_mode, worker_id, lease_id, lease_expires_at, lease_started_at,
             lease_accepted_at, attempt_count, execution_started_at, callback_reservation_id,
             callback_reserved_at, callback_result_sha256, kind, persona_id,
             required_capability)
            VALUES ('task-before-agent', 'run-before-agent', 'trace_kr',
                    'candidate-before-agent', 1, 'capture:before-agent', '{}', 'queued', NULL,
                    NULL, 'dispatched', 'created', 'updated', 'worker_broker', 'worker-1',
                    'lease-1', 'expires', 'started', 'accepted', 2, NULL, 'reservation-1',
                    'reserved', ?, 'generate_candidates', 'persona-1', 'feedback_context_v1')""",
            ("d" * 64,),
        )

        _ = connection.executescript(
            (MIGRATION_ROOT / "0017_marketing_agent_foundation.sql").read_text()
        )

        candidate = connection.execute(
            """SELECT status, revision FROM hosted_workspace_candidates
            WHERE candidate_id = 'candidate-before-agent'"""
        ).fetchone()
        task = connection.execute(
            """SELECT task_id, run_id, candidate_id, candidate_revision, state, dispatch_mode,
                      worker_id, lease_id, attempt_count, callback_reservation_id,
                      callback_result_sha256, kind, persona_id, required_capability
               FROM hosted_workspace_capture_tasks WHERE task_id = 'task-before-agent'"""
        ).fetchone()

        assert candidate == ("submitted", 1)
        assert task == (
            "task-before-agent",
            "run-before-agent",
            "candidate-before-agent",
            1,
            "queued",
            "worker_broker",
            "worker-1",
            "lease-1",
            2,
            "reservation-1",
            "d" * 64,
            "generate_candidates",
            "persona-1",
            "feedback_context_v1",
        )
        _ = connection.execute(
            """INSERT INTO hosted_workspace_capture_tasks
            (task_id, run_id, account_id, candidate_id, candidate_revision, idempotency_key,
             task_json, state, created_at, updated_at, dispatch_mode, kind)
            VALUES ('judgment-task', 'judgment-run', 'trace_kr', '', 1, 'judgment:1', '{}',
                    'queued', 'now', 'now', 'worker_broker', 'marketing_judgment')"""
        )


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


def test_adapter_capability_migration_seeds_trace_reference_installations() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        apply_migrations(connection, through="0022_marketing_knowledge_snapshots.sql")
        _ = connection.execute(
            """INSERT INTO hosted_workspace_accounts
            (account_id, display_name, country, language, timezone, morning_time,
             evening_time, revision, created_at, updated_at)
            VALUES ('trace_kr', 'Trace Korea', 'KR', 'ko', 'Asia/Seoul',
                    '07:30', '19:30', 1, 1, 1)"""
        )
        _ = connection.executescript(
            (MIGRATION_ROOT / "0023_marketing_adapter_capabilities.sql").read_text()
        )

        rows = connection.execute(
            """SELECT capability_id, effect_class, owner_id, enabled, activation_state,
                      length(descriptor_sha256), length(request_schema_sha256),
                      length(receipt_schema_sha256)
               FROM hosted_marketing_adapter_capabilities
               WHERE account_id = 'trace_kr' ORDER BY capability_id"""
        ).fetchall()

        assert rows == [
            (
                "capture.native_png",
                "local_artifact",
                "trace.native_capture",
                1,
                "active",
                64,
                64,
                64,
            ),
            (
                "publish.threads",
                "external",
                "threads.publisher",
                1,
                "registered_reference",
                64,
                64,
                64,
            ),
        ]
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(hosted_marketing_artifact_requests)")
        }
        assert "capability_binding_sha256" in columns

        _ = connection.execute(
            """INSERT INTO hosted_marketing_feature_packets
            (packet_id, feature_id, schema_version, lifecycle, repository, mutable_ref,
             resolved_commit_sha, tree_sha, packet_json, packet_sha256, publication_allowed,
             observed_at, created_at)
            VALUES ('packet-1', 'trace.lockscreen.ai-concepts', 'trace.feature-evidence.v1',
                    'installed_confirmed', 'corca-ai/Trace_iOS', 'develop', ?, ?, '{}', ?,
                    1, 'now', 'now')""",
            ("b" * 40, "c" * 40, "a" * 64),
        )

        _ = connection.execute(
            """INSERT INTO hosted_marketing_campaigns
            (campaign_id, account_id, feature_packet_id, feature_packet_sha256,
             runtime_epoch, mode, state, business_outcome, created_at, updated_at)
                VALUES ('campaign-assisted', 'trace_kr', 'packet-1', ?, 'agent_v1', 'live',
                    'creative_planned', 'outcome', 'now', 'now')""",
            ("a" * 64,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="active capability"):
            _ = connection.execute(
                """INSERT INTO hosted_marketing_tool_actions
                (action_id, campaign_id, capability_id, effect_class, state, action_json,
                 action_sha256, idempotency_key, created_at, updated_at)
                VALUES ('threads-action', 'campaign-assisted', 'publish.threads', 'external',
                        'queued', '{}', ?, 'threads-action', 'now', 'now')""",
                ("b" * 64,),
            )


def test_bound_artifacts_require_immutable_context_capabilities() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        apply_migrations(connection)
        _insert_marketing_execution_fixture(connection, mode="assisted")
        catalog = connection.execute(
            """SELECT capability_id, effect_class, owner_id, enabled, activation_state
            FROM hosted_marketing_adapter_capabilities
            WHERE account_id = 'trace_kr' ORDER BY capability_id"""
        ).fetchall()
        assert ("copy.text", "local_artifact", "trace.marketing_copy", 1, "active") in catalog

        with pytest.raises(sqlite3.IntegrityError, match="capability binding is immutable"):
            _ = connection.execute(
                """UPDATE hosted_marketing_capability_bindings
                SET owner_id = 'forged-owner'
                WHERE context_receipt_id = 'receipt-execution' AND capability_id = 'copy.text'"""
            )
        with pytest.raises(sqlite3.IntegrityError, match="artifact request requires"):
            _ = connection.execute(
                """INSERT INTO hosted_marketing_artifact_requests
                (request_id, campaign_id, treatment_id, capability_id, proof_kind,
                 request_json, request_sha256, capability_binding_sha256, state, created_at,
                 updated_at)
                VALUES ('request-unbound', 'campaign-execution', 'treatment-execution',
                        'copy.text', 'copy_only', '{}', ?, '', 'planned', 'now', 'now')""",
                ("a" * 64,),
            )


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


def test_marketing_execution_ledger_blocks_shadow_candidate_effects() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        apply_migrations(connection)
        _insert_marketing_execution_fixture(connection, mode="shadow")

        with pytest.raises(sqlite3.IntegrityError, match="marketing candidate assignment"):
            _ = connection.execute(
                """UPDATE hosted_workspace_candidates
                SET marketing_campaign_id = 'campaign-execution',
                    marketing_experiment_id = 'experiment-execution',
                    marketing_hypothesis_id = 'hypothesis-execution',
                    marketing_treatment_id = 'treatment-execution',
                    marketing_assignment_id = 'assignment-execution',
                    marketing_assignment_sha256 = ?
                WHERE candidate_id = 'candidate-execution'""",
                ("9" * 64,),
            )


def test_artifact_manifests_are_immutable_and_principles_need_exact_learning_approval() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        apply_migrations(connection)
        _insert_marketing_execution_fixture(connection, mode="assisted")
        _ = connection.execute(
            """UPDATE hosted_workspace_candidates
            SET marketing_campaign_id = 'campaign-execution',
                marketing_experiment_id = 'experiment-execution',
                marketing_hypothesis_id = 'hypothesis-execution',
                marketing_treatment_id = 'treatment-execution',
                marketing_assignment_id = 'assignment-execution',
                marketing_assignment_sha256 = ?
            WHERE candidate_id = 'candidate-execution'""",
            ("9" * 64,),
        )
        assert connection.execute(
            """SELECT marketing_assignment_id FROM hosted_workspace_candidates
            WHERE candidate_id = 'candidate-execution'"""
        ).fetchone() == ("assignment-execution",)
        _ = connection.execute(
            """INSERT INTO hosted_marketing_artifact_manifests
            (manifest_id, campaign_id, assignment_id, treatment_id, request_id, schema_version,
             manifest_json, manifest_sha256, artifact_uri, artifact_sha256, input_sha256,
             capability_binding_sha256, created_at)
            VALUES ('manifest-1', 'campaign-execution', 'assignment-execution',
                    'treatment-execution', 'request-execution',
                    'trace.artifact-manifest.v1', '{"capability_id":"copy.text"}', ?,
                    'r2://artifact.png', ?, ?, ?, 'now')""",
            ("1" * 64, "2" * 64, "3" * 64, "1" * 64),
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            _ = connection.execute(
                """UPDATE hosted_marketing_artifact_manifests
                SET artifact_uri = 'r2://mutated.png' WHERE manifest_id = 'manifest-1'"""
            )

        _ = connection.execute(
            """INSERT INTO hosted_marketing_learning_candidates
            (learning_id, campaign_id, schema_version, candidate_json, candidate_sha256,
             state, created_at, updated_at)
            VALUES ('learning-1', 'campaign-execution', 'trace.learning-candidate.v1',
                    '{}', ?, 'candidate', 'now', 'now')""",
            ("4" * 64,),
        )
        principle_insert = """INSERT INTO hosted_marketing_principles
            (principle_id, learning_id, approval_grant_id, principle_json,
             principle_sha256, state, created_at, updated_at)
            VALUES ('principle-1', 'learning-1', 'learning-grant', '{}', ?,
                    'provisional', 'now', 'now')"""
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(principle_insert, ("5" * 64,))

        _ = connection.execute(
            """INSERT INTO hosted_marketing_approval_grants
            (grant_id, campaign_id, scope, target_kind, target_id, target_sha256,
             decision, reviewer_id, reviewed_at)
            VALUES ('learning-grant', 'campaign-execution', 'learning',
                    'learning_candidate', 'learning-1', ?, 'approved', 'reviewer-1', 'now')""",
            ("4" * 64,),
        )
        _ = connection.execute(principle_insert, ("5" * 64,))
        assert connection.execute(
            "SELECT state FROM hosted_marketing_principles WHERE principle_id = 'principle-1'"
        ).fetchone() == ("provisional",)


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


def _insert_marketing_execution_fixture(
    connection: sqlite3.Connection,
    *,
    mode: str,
) -> None:
    _insert_marketing_agent_account_and_packet(connection)
    origin_campaign_id: str | None = None
    if mode == "assisted":
        origin_campaign_id = "campaign-shadow-origin"
        _ = connection.execute(
            """INSERT INTO hosted_marketing_campaigns
            (campaign_id, account_id, feature_packet_id, feature_packet_sha256, runtime_epoch,
             mode, state, projection_revision, business_outcome, created_at, updated_at)
            VALUES (?, 'trace_kr', 'packet-1', ?, 'agent_v1', 'shadow',
                    'completed', 1, 'shadow outcome', 'now', 'now')""",
            (origin_campaign_id, "a" * 64),
        )
        _ = connection.execute(
            "UPDATE hosted_marketing_feature_packets SET publication_allowed = 1 "
            "WHERE packet_id = 'packet-1'"
        )
        _ = connection.execute(
            """INSERT INTO hosted_marketing_product_truth_approvals
            (approval_id, packet_id, packet_sha256, approved_claim_ids_json,
             decision, reviewer_id, reviewed_at)
            VALUES ('truth-approval', 'packet-1', ?, '[]', 'approved', 'reviewer-1', 'now')""",
            ("a" * 64,),
        )
    _ = connection.execute(
        """INSERT INTO hosted_marketing_campaigns
        (campaign_id, account_id, feature_packet_id, feature_packet_sha256, runtime_epoch,
         mode, origin_campaign_id, state, projection_revision, business_outcome,
         created_at, updated_at)
        VALUES ('campaign-execution', 'trace_kr', 'packet-1', ?, 'agent_v1', ?, ?,
                'creative_planned', 4, 'outcome', 'now', 'now')""",
        ("a" * 64, mode, origin_campaign_id),
    )
    _ = connection.execute(
        """INSERT INTO hosted_marketing_context_receipts
        (receipt_id, campaign_id, schema_version, receipt_json, receipt_sha256,
         feature_packet_sha256, knowledge_snapshot_sha256, capability_snapshot_sha256,
         prompt_sha256, output_schema_sha256, created_at)
        VALUES ('receipt-execution', 'campaign-execution', 'trace.context-receipt.v1', '{}',
                ?, ?, ?, ?, ?, ?, 'now')""",
        ("6" * 64, "a" * 64, "7" * 64, "8" * 64, "9" * 64, "0" * 64),
    )
    _ = connection.execute(
        """INSERT INTO hosted_marketing_capability_bindings
        (context_receipt_id, capability_id, binding_sha256, descriptor_sha256, effect_class,
         request_schema_sha256, receipt_schema_sha256, owner_id, created_at)
        VALUES ('receipt-execution', 'copy.text', ?, ?, 'local_artifact', ?, ?,
                'trace.marketing_copy', 'now')""",
        ("1" * 64, "2" * 64, "3" * 64, "4" * 64),
    )
    _ = connection.execute(
        """INSERT INTO hosted_marketing_strategy_briefs
        (brief_id, campaign_id, context_receipt_id, schema_version, brief_json,
         brief_sha256, created_at)
        VALUES ('brief-execution', 'campaign-execution', 'receipt-execution',
                'trace.strategy-brief.v1', '{}', ?, 'now')""",
        ("b" * 64,),
    )
    _ = connection.execute(
        """INSERT INTO hosted_marketing_experiments
        (experiment_id, campaign_id, strategy_brief_id, state, primary_outcome_scope,
         registration_json, registration_sha256, created_at, updated_at)
        VALUES ('experiment-execution', 'campaign-execution', 'brief-execution', 'registered',
                'direct_response_attribution', '{}', ?, 'now', 'now')""",
        ("c" * 64,),
    )
    _ = connection.execute(
        """INSERT INTO hosted_marketing_hypotheses
        (hypothesis_id, campaign_id, strategy_brief_id, portfolio_role, hypothesis_json,
         hypothesis_sha256, created_at)
        VALUES ('hypothesis-execution', 'campaign-execution', 'brief-execution', 'control',
                '{}', ?, 'now')""",
        ("d" * 64,),
    )
    _ = connection.execute(
        """INSERT INTO hosted_marketing_media_plans
        (plan_id, campaign_id, strategy_brief_id, context_receipt_id, schema_version,
         plan_json, plan_sha256, publication_allowed, human_review_required, state,
         created_at, updated_at)
        VALUES ('plan-execution', 'campaign-execution', 'brief-execution', 'receipt-execution',
                'trace.media-plan.v1', '{}', ?, 1, 1, 'approved', 'now', 'now')""",
        ("e" * 64,),
    )
    _ = connection.execute(
        """INSERT INTO hosted_marketing_approval_grants
        (grant_id, campaign_id, scope, target_kind, target_id, target_sha256,
         decision, reviewer_id, reviewed_at)
        VALUES ('creative-grant', 'campaign-execution', 'creative', 'media_plan',
                'plan-execution', ?, 'approved', 'reviewer-1', 'now')""",
        ("e" * 64,),
    )
    _ = connection.execute(
        """INSERT INTO hosted_marketing_creative_treatments
        (treatment_id, plan_id, campaign_id, experiment_id, hypothesis_id, format,
         treatment_json, treatment_sha256, created_at)
        VALUES ('treatment-execution', 'plan-execution', 'campaign-execution',
                'experiment-execution', 'hypothesis-execution', 'explanatory_carousel',
                '{}', ?, 'now')""",
        ("f" * 64,),
    )
    _ = connection.execute(
        """INSERT INTO hosted_marketing_artifact_requests
        (request_id, campaign_id, treatment_id, capability_id, proof_kind,
         request_json, request_sha256, capability_binding_sha256, state, created_at, updated_at)
        VALUES ('request-execution', 'campaign-execution', 'treatment-execution',
                'copy.text', 'copy_only', '{}', ?, ?, 'planned', 'now', 'now')""",
        ("1" * 64, "1" * 64),
    )
    _ = connection.execute(
        """INSERT INTO hosted_workspace_candidates
        (candidate_id, account_id, source, country, topic, caption, hypothesis,
         refs_json, principles_json, appium_prompt, image_inputs_json, status,
         revision, created_at, updated_at)
        VALUES ('candidate-execution', 'trace_kr', 'manual', 'KR', 'topic', 'caption',
                'hypothesis', '[]', '[]', 'prompt', '{}', 'awaiting_review', 1, 1, 1)"""
    )
    _ = connection.execute(
        """INSERT INTO hosted_marketing_post_assignments
        (assignment_id, campaign_id, experiment_id, hypothesis_id, treatment_id,
         candidate_id, candidate_revision, candidate_content_sha256, eligible_block_id,
         assignment_json, assignment_sha256, assigned_at)
        VALUES ('assignment-execution', 'campaign-execution', 'experiment-execution',
                'hypothesis-execution', 'treatment-execution', 'candidate-execution',
                1, ?, 'block-1', '{}', ?, 'now')""",
        ("8" * 64, "9" * 64),
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


def test_knowledge_snapshot_migration_backfills_the_existing_strategy_task() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        apply_migrations(connection, through="0021_marketing_artifact_assignment_lineage.sql")
        principles = '["One post has one situation."]'
        digest = "a" * 64
        _ = connection.execute(
            """INSERT INTO hosted_workspace_accounts
            (account_id, display_name, country, language, timezone, morning_time,
             evening_time, revision, created_at, updated_at)
            VALUES ('trace_kr', 'Trace KR', 'KR', 'ko', 'Asia/Seoul', '07:30', '19:30',
                    1, 'now', 'now')"""
        )
        _ = connection.execute(
            """INSERT INTO hosted_marketing_feature_packets
            (packet_id, feature_id, schema_version, lifecycle, repository, mutable_ref,
             resolved_commit_sha, tree_sha, packet_json, packet_sha256, publication_allowed,
             observed_at, created_at)
            VALUES ('packet-1', 'feature-1', 'trace.feature-evidence.v1', 'source_candidate',
                    'corca-ai/trace', 'develop', ?, ?, '{}', ?, 0, 'now', 'now')""",
            ("b" * 40, "c" * 40, digest),
        )
        _ = connection.execute(
            """INSERT INTO hosted_marketing_campaigns
            (campaign_id, account_id, feature_packet_id, feature_packet_sha256, runtime_epoch,
             mode, state, projection_revision, business_outcome, created_at, updated_at)
            VALUES ('campaign-1', 'trace_kr', 'packet-1', ?, 'agent_v1', 'shadow',
                    'experiment_registered', 2, 'outcome', 'now', 'now')""",
            (digest,),
        )
        task_payload = json.dumps(
            {
                "payload": {
                    "campaign_id": "campaign-1",
                    "judgment": "shadow_strategy",
                    "canonical_principles": json.loads(principles),
                    "knowledge_snapshot_sha256": digest,
                }
            },
            separators=(",", ":"),
        )
        _ = connection.execute(
            """INSERT INTO hosted_workspace_capture_tasks
            (task_id, run_id, account_id, candidate_id, candidate_revision, idempotency_key,
             task_json, state, dispatch_mode, kind, created_at, updated_at)
            VALUES ('task-1', 'run-1', 'trace_kr', '', 1, 'strategy:campaign-1', ?, 'succeeded',
                    'worker_broker', 'marketing_judgment', 'now', 'now')""",
            (task_payload,),
        )

        _ = connection.executescript(
            (MIGRATION_ROOT / "0022_marketing_knowledge_snapshots.sql").read_text()
        )

        assert connection.execute(
            """SELECT snapshot_json, snapshot_sha256
            FROM hosted_marketing_knowledge_snapshots WHERE campaign_id = 'campaign-1'"""
        ).fetchone() == ('{"principles":["One post has one situation."]}', digest)
