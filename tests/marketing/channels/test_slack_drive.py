from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import TYPE_CHECKING, override

from pydantic import TypeAdapter

from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.contracts.agent_run import AgentBudget, AgentRunState
from ads_booster.execution_control import checkpoint
from tests.marketing.agent_service.test_application import build_service
from tests.marketing.agent_service.test_task_drive import FreshResearch, Steps
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import receive, setup_events

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.channels.slack_events import SlackEvents
    from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult


def configure(owner: SlackEvents, root: Path, actor: Steps, adapter: FreshResearch) -> None:
    fixture = build_service(root / "descriptor.sqlite3", actor, research_adapter=adapter)
    service = owner.commands.application.service
    service.registry = ToolRegistry(
        tuple(
            item.model_copy(
                update={"readiness": item.readiness.model_copy(update={"observed_at": NOW})}
            )
            for item in fixture.registry.descriptors
        )
    )
    service.tools = fixture.tools
    service.reasoning = actor
    service.clock = lambda: NOW
    owner.commands.new_run_budget = AgentBudget(max_tool_calls=12, max_cost_units=50)


def test_slack_worker_resumes_slices_after_reconstruction(tmp_path: Path) -> None:
    owner, _ = setup_events(tmp_path)
    adapter = FreshResearch()
    configure(owner, tmp_path, Steps(), adapter)
    receive(owner)
    assert owner.work_once(now=NOW)
    run = owner.commands.application.service.repository.list_runs("team")[0]
    assert run.state is AgentRunState.RUNNING
    restarted, _ = setup_events(tmp_path)
    configure(restarted, tmp_path, Steps(), adapter)
    restarted.recover()
    for _ in range(5):
        _ = restarted.work_once(now=NOW)
        current = restarted.commands.application.service.repository.get("team", run.run_id)
        if current is not None and current.state is AgentRunState.COMPLETED:
            break
    final = restarted.commands.application.service.repository.get("team", run.run_id)
    assert final is not None
    assert final.state is AgentRunState.COMPLETED
    assert len(adapter.inputs) == 10
    delivery, messages = setup_events(tmp_path)
    configure(delivery, tmp_path, Steps(), adapter)
    delivery.recover()
    for _ in range(4):
        _ = delivery.work_once(now=NOW)
    assert sum(item.get("text") == "Finished draft" for item in messages) == 1
    assert len(adapter.inputs) == 10


class BlockingSecondSlice(Steps):
    def __init__(self) -> None:
        super().__init__()
        self.entered: Event = Event()
        self.release: Event = Event()

    @override
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        if request.remaining_tool_calls <= 8:
            self.entered.set()
            assert self.release.wait(5)
            checkpoint("continued provider boundary")
        return super().plan(request)


def test_second_slack_slice_obeys_original_cancel_control(tmp_path: Path) -> None:
    owner, _ = setup_events(tmp_path)
    actor, adapter = BlockingSecondSlice(), FreshResearch()
    configure(owner, tmp_path, actor, adapter)
    receive(owner)
    assert owner.work_once(now=NOW)
    conversation = owner.store.conversations()[0]
    with owner.store.connect() as db:
        row = TypeAdapter(tuple[str]).validate_python(
            db.execute(
                "SELECT message_id FROM slack_message_jobs WHERE conversation_id=?",
                (conversation.conversation_id,),
            ).fetchone()
        )
        message_id = row[0]
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(owner.work_once, now=NOW)
        assert actor.entered.wait(5)
        owner.progress.cancel(message_id)
        actor.release.set()
        assert future.result(timeout=5)
    run = owner.commands.application.service.repository.list_runs("team")[0]
    assert run.state is AgentRunState.STOPPED
    assert len(adapter.inputs) == 4
