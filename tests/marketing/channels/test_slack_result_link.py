"""Same-work results use authenticated Web readback without exposing private Runs."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

import pytest

from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.contracts.agent_run import AgentRunState
from tests.marketing.agent_service.test_application import EffectThenStopReasoning, ResearchAdapter
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import effect_descriptor, receive, setup_events

if TYPE_CHECKING:
    from pathlib import Path


def test_normal_answer_keeps_run_diagnostics_out_of_body_but_status_exposes_them(
    tmp_path: Path,
) -> None:
    events, messages = setup_events(tmp_path)
    receive(events)
    assert events.work_once(now=NOW)
    assert "상태:" not in str(messages[-1]["text"])
    assert "실행:" not in str(messages[-1]["text"])
    assert "/runs/" not in str(messages[-1]["text"])
    receive(events, type="message", text="상태", ts="100.002", thread_ts="100.001")
    assert events.work_once(now=NOW)
    assert "상태: completed" in str(messages[-1]["text"])


def test_interrupted_tool_plan_is_not_delivered_as_an_answer(tmp_path: Path) -> None:
    events, _ = setup_events(tmp_path)
    service = events.commands.application.service
    service.registry = ToolRegistry((effect_descriptor(),))
    service.reasoning = EffectThenStopReasoning()
    adapter = ResearchAdapter()
    service.tools = {"creative.image.edit": adapter}

    def interrupt(point: str) -> None:
        if point == "plan_committed":
            detail = "fixture interruption before invocation"
            raise RuntimeError(detail)

    service.fault_hook = interrupt
    receive(events)
    assert events.work_once(now=NOW)
    run = service.repository.list_runs("team")[0]
    assert run.state is AgentRunState.RUNNING
    conversation = events.store.conversation_for_run("team", run.run_id)
    assert conversation is not None
    before = service.repository.records("team", run.run_id)
    answer = events.summary(conversation)
    assert answer == "아직 작업이 완료되지 않았습니다."
    assert not adapter.inputs
    assert service.repository.records("team", run.run_id) == before
    assert "상태: running" in events.summary(conversation, include_status=True)


@pytest.mark.parametrize("public_links", [True, False])
def test_shared_summary_links_same_run_only_when_web_is_enabled(
    tmp_path: Path, public_links: bool
) -> None:
    events, messages = setup_events(tmp_path)
    events.commands.public_links = public_links
    receive(events)
    assert events.work_once(now=NOW)
    run = events.commands.application.service.repository.list_runs("team")[0]
    url = events.commands.application.result_base_url + "/runs/" + quote(run.run_id, safe="")
    assert (url in str(messages[-1])) is public_links


def test_private_summary_does_not_offer_shared_web_projection(tmp_path: Path) -> None:
    events, messages = setup_events(tmp_path)
    receive(events, type="message", channel="D1", channel_type="im", text="개인 질문")
    assert events.work_once(now=NOW)
    assert events.commands.application.result_base_url not in str(messages[-1])
