from __future__ import annotations

from contextlib import closing
from pathlib import Path

from pydantic import TypeAdapter

from ads_booster.bootstrap.lifecycle import build_installed_knowledge_runtime
from ads_booster.channels.slack_events import SlackEvents
from ads_booster.knowledge.configuration import (
    KnowledgeSettings,
    initialize_knowledge_store,
    initialize_local_configuration,
)
from ads_booster.providers.codex_cli import CodexCli
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import RecordingReasoning, receive, setup_events


def test_shared_thread_switches_requester_without_reusing_another_members_task(
    tmp_path: Path,
) -> None:
    original, _ = setup_events(tmp_path)
    service = original.commands.application.service
    reasoning = RecordingReasoning()
    service.reasoning = reasoning
    settings = KnowledgeSettings(
        root=tmp_path / "knowledge",
        control_root=tmp_path / "control",
        policy_path=tmp_path / "control/policy.json",
    )
    _ = initialize_local_configuration(*settings.require_enabled(), workspace_id="team")
    _ = initialize_knowledge_store(settings)
    installed = build_installed_knowledge_runtime(
        settings=settings,
        service_database=service.repository.database_path,
        codex=CodexCli(executable=Path("/unused/codex"), model="test"),
        model_id="test",
    )
    with closing(installed.runtime):
        service.knowledge = installed.adapter
        owner = SlackEvents(original.commands, "UBOT", frozenset({"C1"}), workspace_mentions=True)
        receive(owner, text="<@UBOT> 첫 사용자의 요청")
        for _ in range(8):
            if not owner.work_once(now=NOW):
                break
        assert len(reasoning.requests) == 1

        receive(
            owner,
            user="U2",
            text="<@UBOT> 두 번째 사용자의 요청",
            ts="100.002",
            thread_ts="100.001",
        )
        for _ in range(8):
            if not owner.work_once(now=NOW):
                break

        assert len(reasoning.requests) == 2
        assert reasoning.requests[0].run_id == reasoning.requests[1].run_id
        with installed.adapter.repository.connection() as db:
            tasks = TypeAdapter(list[tuple[str, str]]).validate_python(
                db.execute(
                    "SELECT task_id,member_id FROM task_bindings WHERE state='active'"
                ).fetchall()
            )
        assert len(tasks) == 2
        assert len({str(row[0]) for row in tasks}) == 2
        assert len({str(row[1]) for row in tasks}) == 2
