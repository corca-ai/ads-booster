"""Uncertain effects retain their owner while follow-up dialogue can inspect evidence."""

from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from pathlib import Path

from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.contracts.reasoning import ReasoningDecision, ReasoningRequest, ReasoningResult
from ads_booster.contracts.tool_capability import EffectClass
from tests.marketing.agent_service.test_application import (
    InvokeThenStopReasoning,
    ResearchAdapter,
    _descriptor,  # pyright: ignore[reportPrivateUsage]
    _reasoning_result,  # pyright: ignore[reportPrivateUsage]
)
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import (
    effect_descriptor,
    receive,
    setup_events,
)


def test_uncertain_followup_reaches_model_without_exposing_effect_tools(tmp_path: Path) -> None:
    owner, messages = setup_events(tmp_path)
    receive(owner, text="<@UBOT> 처음 요청")
    assert owner.work_once(now=NOW)
    service = owner.commands.application.service
    original = service.repository.list_runs("team")[0]
    # Model the persisted consumer boundary; deferred owner recovery has separate tests.
    uncertain = service._mark_reconciliation(original, "0" * 64, now=NOW)  # pyright: ignore[reportPrivateUsage]

    class ReadOnceReasoning(InvokeThenStopReasoning):
        @override
        def plan(self, request: ReasoningRequest) -> ReasoningResult:
            if self.requests:
                return super().plan(request)
            self.requests.append(request)
            return _reasoning_result(
                request,
                ReasoningDecision(
                    schema_version="trace.reasoning-decision.v1",
                    action="invoke_tool",
                    capability_id="research.web",
                    tool_input={"query": "sandbox launcher failure"},
                    expected_outcome="Read supporting information",
                    reasoning_summary="Inspect read-only evidence",
                ),
            )

    reasoner = ReadOnceReasoning()
    service.reasoning = reasoner
    observe = _descriptor("research.web", EffectClass.OBSERVE, ready=True)
    observe = observe.model_copy(
        update={"readiness": observe.readiness.model_copy(update={"observed_at": NOW})}
    )
    service.registry = ToolRegistry((effect_descriptor(), observe))
    reader = ResearchAdapter()
    service.tools = {"research.web": reader}
    before = service.repository.records("team", original.run_id)
    receive(
        owner,
        type="message",
        text="왜 멈췄는지 설명하고 확인 가능한 결과를 찾아줘",
        ts="100.002",
        thread_ts="100.001",
    )
    assert owner.work_once(now=NOW)
    assert reasoner.requests
    assert len(reader.inputs) == 1
    assert all(
        d.effect_class is EffectClass.OBSERVE
        for r in reasoner.requests
        for d in r.capability_snapshot.descriptors
    )
    assert service.repository.get("team", original.run_id) == uncertain
    assert service.repository.records("team", original.run_id) == before
    inspection = next(
        r for r in service.repository.list_runs("team") if r.run_id != original.run_id
    )
    assert inspection.run_id != original.run_id
    context = inspection.goal.context["reconciliation_inspection"]
    assert isinstance(context, dict)
    assert context["source_run_id"] == original.run_id

    # A delayed source notification must still bind to the source Run after inspection.
    assert owner.enqueue_run_update("team", original.run_id, event_id="late-source-update")
    before_messages = len(messages)
    assert owner.work_once(now=NOW)
    assert len(messages) > before_messages
    assert "결과가 확인되지" in str(messages[-1]["text"])
