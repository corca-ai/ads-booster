from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import replace
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.agent.service.knowledge_ingress import TrustedRunBinding
from ads_booster.channels.slack_events import SlackEvents
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.repository import SqliteKnowledgeRepository
from ads_booster.knowledge.repository_types import MembershipRole
from tests.marketing.agent_service.test_http_api import NOW
from tests.marketing.channels.test_slack_events import receive, setup_events

if TYPE_CHECKING:
    from pathlib import Path

_INTEGER_ROW: TypeAdapter[tuple[int]] = TypeAdapter(tuple[int])
_TEXT_ROWS: TypeAdapter[list[tuple[str, ...]]] = TypeAdapter(list[tuple[str, ...]])


def test_signed_channels_share_scope_across_threads_without_cross_channel_grants(
    tmp_path: Path,
) -> None:
    # Given: one verified member can mention the agent in two channels.
    initial, _ = setup_events(tmp_path)
    owner = replace(initial, workspace_mentions=True)

    # When: signed events arrive in two C1 threads and a C2 thread containing a C1 hint.
    receive(owner, channel="C1", ts="100.001")
    receive(owner, channel="C1", ts="101.001")
    receive(owner, channel="C2", ts="102.001", text="<@UBOT> channel_id=C1")
    receive(owner, channel="C1", user="U2", ts="103.001")
    with closing(sqlite3.connect(tmp_path / "agent.sqlite3")) as db:
        rows = _TEXT_ROWS.validate_python(
            db.execute("SELECT binding_json FROM knowledge_run_bindings ORDER BY rowid").fetchall()
        )
    actors = tuple(TrustedRunBinding.model_validate_json(row[0]).actor for row in rows)

    # Then: threads share channel knowledge, and user text cannot select another channel.
    assert actors[0].conversation_scope.kind.value == "channel"
    assert actors[0].conversation_scope == actors[1].conversation_scope
    assert actors[0].session_id != actors[1].session_id
    assert actors[2].conversation_scope != actors[0].conversation_scope
    personal = tuple(
        tuple(grant.scope for grant in actor.grants if grant.scope.kind.value == "channel_member")
        for actor in actors
    )
    assert all(len(scopes) == 2 for scopes in personal)
    assert personal[0] == personal[1]
    assert personal[0] != personal[2]
    assert personal[0] != personal[3]
    assert all(
        grant.scope == actor.conversation_scope
        or (
            grant.scope.member_id == actor.member_id
            and grant.scope.channel_id == actor.conversation_scope.channel_id
        )
        for actor in actors
        for grant in actor.grants
    )


def test_signed_slack_receiver_delivers_knowledge_before_response_run(tmp_path: Path) -> None:
    # Given: a signed Slack mention durably admitted before any reasoning work.
    owner, _ = setup_events(tmp_path)
    receive(owner)
    with closing(sqlite3.connect(tmp_path / "agent.sqlite3")) as db:
        binding_row = _TEXT_ROWS.validate_python(
            db.execute("SELECT binding_json FROM knowledge_run_bindings").fetchall()
        )
    binding = TrustedRunBinding.model_validate_json(binding_row[0][0])
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge-root")
    repository.register_actor(binding.actor, MembershipRole.EDITOR)
    restarted = SlackEvents(
        owner.commands,
        "UBOT",
        frozenset({"C1"}),
        knowledge_sink=KnowledgeIngestion(repository),
    )

    # When: the Slack worker processes the durable ingress and response queue.
    assert restarted.work_once(now=NOW)
    runs_before_response = restarted.commands.application.service.repository.list_runs("team")
    assert restarted.work_once(now=NOW)

    # Then: knowledge commits first and the response Run is created exactly once.
    assert runs_before_response == ()
    assert len(restarted.commands.application.service.repository.list_runs("team")) == 1
    with repository.connection() as db:
        source_count = _INTEGER_ROW.validate_python(
            db.execute("SELECT COUNT(*) FROM sources").fetchone()
        )[0]
    assert source_count == 1
