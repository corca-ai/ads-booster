from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from ads_booster.agent.service.knowledge_ingress import (
    CanonicalKnowledgeIngress,
    TrustedRunBinding,
)
from ads_booster.agent.service.knowledge_ingress_authority import (
    KnowledgeIngressAuthority,
)
from ads_booster.bootstrap.lifecycle import (
    InstalledServicePaths,
    build_installed_knowledge_runtime,
    build_installed_marketing_agent_service,
)
from ads_booster.channels.contracts import ChannelIdentityBinding
from ads_booster.channels.http.http_api import MarketingAgentApi
from ads_booster.channels.knowledge_ingress_slack import (
    SlackIngressRequest,
    build_slack_ingress,
)
from ads_booster.channels.slack_events import SlackEvents
from ads_booster.contracts.knowledge_preparation import PreparedKnowledgeContext
from ads_booster.knowledge.configuration import (
    KnowledgeSettings,
    initialize_knowledge_store,
    initialize_local_configuration,
)
from ads_booster.knowledge.contracts import (
    ConversationEventKind,
    GrantCapability,
    SourceDisposition,
)
from ads_booster.knowledge.curation_contracts import (
    CurationBatchDecision,
    CurationBatchJobContext,
    CurationBatchJobDecision,
    CurationDecision,
    CurationDecisionAction,
    SourceDispositionIntent,
)
from ads_booster.knowledge.errors import AccessDeniedError, PolicyEpochStaleError
from ads_booster.knowledge.grant_policy import authorize_read, authorize_write
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.repository import MembershipRole, SqliteKnowledgeRepository
from ads_booster.providers.codex_cli import CodexCli
from ads_booster.providers.codex_knowledge import CodexKnowledgeProvider
from tests.knowledge.change_test_fixtures import actor as catalog_actor
from tests.marketing.agent_service.test_http_api import StopReasoning
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import receive, setup_events


def reference_batch(
    self: CodexKnowledgeProvider,
    batch_id: str,
    jobs: tuple[CurationBatchJobContext, ...],
    *,
    timeout_seconds: float,
) -> CurationBatchDecision:
    _ = self, timeout_seconds
    return CurationBatchDecision(
        schema="knowledge.curation-batch-decision.v1",
        batch_id=batch_id,
        decisions=tuple(
            CurationBatchJobDecision(
                job_id=item.request.job_id,
                decision=CurationDecision(
                    schema="knowledge.curation-decision.v1",
                    action=CurationDecisionAction.FINISH,
                    finish_summary="Synthetic reference accepted",
                    disposition_intent=SourceDispositionIntent(
                        source_id=item.request.excerpts[0].source_id,
                        revision_id=item.request.excerpts[0].revision_id,
                        expected_admission_revision=0,
                        disposition=SourceDisposition.REFERENCE,
                        reason="Synthetic controlled reference",
                    ),
                ),
            )
            for item in jobs
        ),
    )


def test_installed_service_admits_canonical_session_before_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(CodexKnowledgeProvider, "decide_batch", reference_batch)
    # Given: the actual installed composition and a separately named local member.
    settings = KnowledgeSettings(
        root=tmp_path / "store",
        control_root=tmp_path / "control",
        policy_path=tmp_path / "control/policy.json",
    )
    identity, _ = initialize_local_configuration(
        *settings.require_enabled(),
        workspace_id="trace",
    )
    _ = initialize_knowledge_store(settings)
    paths = InstalledServicePaths(tmp_path / "service")
    paths.prepare()
    installed = build_installed_knowledge_runtime(
        settings=settings,
        service_database=paths.database,
        codex=CodexCli(executable=Path("/unused/codex"), model="test"),
        model_id="test",
    )
    try:
        service = replace(
            build_installed_marketing_agent_service(
                paths=paths,
                codex_executable=Path("/unused/codex"),
                model_id="test",
                timeout_seconds=1,
                knowledge=installed.adapter,
            ),
            reasoning=StopReasoning(),
        )
        api = MarketingAgentApi(
            service,
            "trace",
            identity.actor_id,
            "secret",
            knowledge_ingress=installed.adapter.ingress,
        )
        # When: authenticated creation prepares context before the asynchronous outbox drains.
        result = api.dispatch(
            "POST",
            "/v1/runs",
            authorization="Bearer secret",
            body=json.dumps(
                {
                    "run_id": "run-one",
                    "request_id": "request-one",
                    "goal": {
                        "objective": "Remember launch date",
                        "success_criteria": ["recall"],
                        "context": {"actor_id": "forged", "brand_id": "forged"},
                    },
                    "budget": {"max_tool_calls": 4, "max_cost_units": 20},
                }
            ).encode(),
        )
        # Then: the real task/session FK and read capability allow preparation and ingestion.
        assert result.status == 202
        binding = installed.adapter.ingress.binding_for_run("run-one")
        assert binding is not None
        assert binding.actor.member_id == identity.member_id
        assert binding.actor.actor_id == identity.actor_id
        assert installed.adapter.ingress.dispatch_once()

        installed.runtime.run_until_idle(flush_batches=True)
        second = api.dispatch(
            "POST",
            "/v1/runs",
            authorization="Bearer secret",
            body=json.dumps(
                {
                    "run_id": "run-two",
                    "request_id": "request-two",
                    "goal": {
                        "objective": "launch date",
                        "success_criteria": ["recall"],
                        "context": {},
                    },
                    "budget": {"max_tool_calls": 4, "max_cost_units": 20},
                }
            ).encode(),
        )
        assert second.status == 202
        record = next(
            record
            for record in service.repository.records("trace", "run-two")
            if record.payload_schema_version == "trace.prepared-knowledge-context-record.v1"
        )
        prepared = PreparedKnowledgeContext.model_validate(record.payload["prepared_context"])
        assert prepared.receipt.selected_source_revisions
        with installed.adapter.repository.connection() as db:
            _ = db.execute("UPDATE workspaces SET policy_epoch=2 WHERE workspace_id='trace'")
        assert installed.adapter.ingress.dispatch_once()
        with installed.adapter.ingress.connect() as db:
            assert db.execute(
                """SELECT state,error_code FROM knowledge_ingress_outbox
                WHERE delivery_id='request-two'"""
            ).fetchone() == ("failed", "knowledge_ingress_actor_denied")
        fresh = api.dispatch(
            "POST",
            "/v1/runs",
            authorization="Bearer secret",
            body=json.dumps(
                {
                    "run_id": "run-current",
                    "request_id": "request-current",
                    "goal": {
                        "objective": "launch date",
                        "success_criteria": ["recall"],
                        "context": {},
                    },
                    "budget": {"max_tool_calls": 4, "max_cost_units": 20},
                }
            ).encode(),
        )
        assert fresh.status == 202
        current_binding = installed.adapter.ingress.binding_for_run("run-current")
        assert current_binding is not None
        assert current_binding.actor.policy_epoch == 2
        with installed.adapter.repository.connection() as db:
            _ = db.execute(
                "UPDATE members SET state='disabled' WHERE actor_id=?", (identity.actor_id,)
            )
        denied = api.dispatch(
            "POST",
            "/v1/runs",
            authorization="Bearer secret",
            body=json.dumps(
                {
                    "run_id": "run-three",
                    "request_id": "request-three",
                    "goal": {
                        "objective": "launch date",
                        "success_criteria": ["recall"],
                        "context": {},
                    },
                    "budget": {"max_tool_calls": 4, "max_cost_units": 20},
                }
            ).encode(),
        )
        assert denied.status == 403
        assert installed.adapter.ingress.dispatch_once()
        with installed.adapter.ingress.connect() as db:
            assert db.execute(
                """SELECT state,error_code FROM knowledge_ingress_outbox
                WHERE delivery_id='request-current'"""
            ).fetchone() == ("failed", "knowledge_ingress_actor_denied")
    finally:
        installed.runtime.close()


def test_private_slack_ingress_keeps_members_and_sessions_separate(tmp_path: Path) -> None:
    # Given: authenticated Slack identities projected by the production ingress builder.
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    initial_actor = catalog_actor()
    initial_actor = initial_actor.model_copy(
        update={
            "policy_epoch": 1,
            "grants": tuple(
                grant.model_copy(update={"policy_epoch": 1}) for grant in initial_actor.grants
            ),
        }
    )
    repository.register_actor(initial_actor, MembershipRole.ADMIN)
    now = datetime.now(UTC)
    owner = CanonicalKnowledgeIngress(
        tmp_path / "agent.db",
        sink=KnowledgeIngestion(repository),
        authority=KnowledgeIngressAuthority(repository),
    )
    bindings: list[TrustedRunBinding] = []
    for member in ("alice", "bob"):
        ingress = build_slack_ingress(
            SlackIngressRequest(
                conversation_id=f"dm-{member}",
                message_id=f"message-{member}",
                run_id=f"run-{member}",
                action="create",
                text=f"Private launch data for {member}",
                revision=1,
                external_revision="1",
                created_revision="1",
                event_kind=ConversationEventKind.MESSAGE_FINALIZED,
                identity=ChannelIdentityBinding(
                    schema_version="trace.channel-identity-binding.v1",
                    binding_id=f"binding-{member}",
                    installation_id="slack-one",
                    external_user_id=member,
                    tenant_id=catalog_actor().workspace_id,
                    member_id=member,
                    created_at=now,
                ),
                private=True,
                channel_id="D1",
                reply_to=None,
                attachments=(),
                observed_at=now,
            )
        )
        # When: both private conversations use the real canonical outbox and SQLite sink.
        assert owner.admit_standalone(ingress)
        assert owner.dispatch_once()
        binding = owner.binding_for_run(f"run-{member}")
        assert binding is not None
        bindings.append(binding)
    # Then: private context stays local and stale epochs fail closed.
    alice, bob = (binding.actor for binding in bindings)
    assert {grant.capability for grant in alice.grants} == {
        GrantCapability.READ,
        GrantCapability.WRITE,
    }
    with pytest.raises(AccessDeniedError):
        _ = authorize_read(actor=alice, target_scope=catalog_actor().conversation_scope, at=now)
    with pytest.raises(AccessDeniedError):
        _ = authorize_write(actor=alice, target_scope=catalog_actor().conversation_scope, at=now)
    with pytest.raises(AccessDeniedError):
        _ = authorize_read(actor=alice, target_scope=bob.conversation_scope, at=now)
    with repository.connection() as db:
        _ = db.execute("UPDATE workspaces SET policy_epoch=policy_epoch+1")
    with pytest.raises(PolicyEpochStaleError):
        _ = KnowledgeIngressAuthority(repository).bind_actor(alice)


def test_signed_slack_uses_installed_ingress_without_manual_sink(tmp_path: Path) -> None:
    # Given: channel service connected to the installed knowledge adapter.

    original, _ = setup_events(tmp_path)
    settings = KnowledgeSettings(
        root=tmp_path / "store",
        control_root=tmp_path / "control",
        policy_path=tmp_path / "control/policy.json",
    )
    _ = initialize_local_configuration(*settings.require_enabled(), workspace_id="team")
    _ = initialize_knowledge_store(settings)
    service = original.commands.application.service
    installed = build_installed_knowledge_runtime(
        settings=settings,
        service_database=service.repository.database_path,
        codex=CodexCli(executable=Path("/unused/codex"), model="test"),
        model_id="test",
    )
    try:
        service.knowledge = installed.adapter
        owner = SlackEvents(original.commands, "UBOT", frozenset({"C1"}))
        # When: a signed event enters the actual Slack receiver and worker.
        receive(owner)
        assert owner.work_once(now=NOW)
        assert service.repository.list_runs("team") == ()
        assert owner.work_once(now=NOW)
        # Then: the default installed sink committed knowledge before response planning.
        assert len(service.repository.list_runs("team")) == 1
        with installed.adapter.repository.connection() as db:
            assert TypeAdapter(tuple[int]).validate_python(
                db.execute("SELECT COUNT(*) FROM sources").fetchone()
            ) == (1,)
    finally:
        installed.runtime.close()
