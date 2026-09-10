from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from ads_booster.bootstrap.lifecycle import build_installed_knowledge_runtime
from ads_booster.channels.contracts import ChannelIdentityBinding
from ads_booster.channels.knowledge_ingress_slack import (
    SlackIngressRequest,
    build_slack_ingress,
)
from ads_booster.knowledge.configuration import (
    KnowledgeSettings,
    initialize_knowledge_store,
    initialize_local_configuration,
)
from ads_booster.knowledge.contracts import ConversationEventKind
from ads_booster.providers.codex_cli import CodexCli
from ads_booster.providers.codex_knowledge import CodexKnowledgeProvider
from tests.knowledge.test_installed_service_context import reference_batch


@pytest.mark.parametrize(
    ("private", "same_channel"), [(True, False), (False, False), (False, True)]
)
def test_private_slack_messages_reach_curation_with_registered_actors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    private: bool,
    same_channel: bool,
) -> None:
    # Given: the installed service composition with a controlled model and two private sessions.
    monkeypatch.setattr(CodexKnowledgeProvider, "decide_batch", reference_batch)
    settings = KnowledgeSettings(
        root=tmp_path / "store",
        control_root=tmp_path / "control",
        policy_path=tmp_path / "control/policy.json",
    )
    _ = initialize_local_configuration(*settings.require_enabled(), workspace_id="trace")
    _ = initialize_knowledge_store(settings)
    installed = build_installed_knowledge_runtime(
        settings=settings,
        service_database=tmp_path / "agent.db",
        codex=CodexCli(executable=Path("/unused/codex"), model="fixture"),
        model_id="fixture",
    )
    now = datetime.now(UTC)
    try:
        for session in ("first", "second"):
            ingress = build_slack_ingress(
                SlackIngressRequest(
                    conversation_id=f"dm-{session}",
                    message_id=f"message-{session}",
                    run_id=f"run-{session}",
                    action="create",
                    text=f"Private launch data {session}",
                    revision=1,
                    external_revision=str(now.timestamp()),
                    created_revision=str(now.timestamp()),
                    event_kind=ConversationEventKind.MESSAGE_FINALIZED,
                    identity=ChannelIdentityBinding(
                        schema_version="trace.channel-identity-binding.v1",
                        binding_id="binding-alice",
                        installation_id="slack-one",
                        external_user_id="alice",
                        tenant_id="trace",
                        member_id="alice",
                        created_at=now - timedelta(days=1),
                    ),
                    private=private,
                    channel_id="D1" if private else ("C1" if same_channel else f"C{session}"),
                    reply_to=None,
                    attachments=(),
                    observed_at=now,
                )
            )
            assert installed.adapter.ingress.admit_standalone(ingress)

        # When: real ingress, collection and child curation process both sessions.
        while installed.adapter.ingress.dispatch_once():
            pass
        installed.runtime.run_until_idle(flush_batches=True)

        # Then: each private message was curated without relabeling the service administrator.
        with installed.adapter.repository.connection() as db:
            assert TypeAdapter(list[tuple[str]]).validate_python(
                db.execute(
                    "SELECT state FROM jobs WHERE kind='curation' ORDER BY job_id",
                ).fetchall()
            ) == [("completed",), ("completed",)]
            assert TypeAdapter(tuple[int]).validate_python(
                db.execute(
                    """SELECT count(DISTINCT scope_key) FROM curation_batches
                    WHERE state='completed'""",
                ).fetchone()
            ) == (1 if same_channel else 2,)
            assert TypeAdapter(tuple[int]).validate_python(
                db.execute(
                    "SELECT count(*) FROM curation_batches WHERE state='completed'"
                ).fetchone()
            ) == (2,)
    finally:
        installed.runtime.close()
