from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.repository import SqliteKnowledgeRepository
from ads_booster.knowledge.repository_types import MembershipRole
from ads_booster.agent.service.knowledge_ingress import TrustedRunBinding
from ads_booster.channels.slack_events import SlackEvents
from tests.marketing.agent_service.test_http_api import NOW
from tests.marketing.channels.test_slack_events import receive, setup_events

if TYPE_CHECKING:
    from pathlib import Path

_INTEGER_ROW: TypeAdapter[tuple[int]] = TypeAdapter(tuple[int])
_TEXT_ROWS: TypeAdapter[list[tuple[str, ...]]] = TypeAdapter(list[tuple[str, ...]])


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
