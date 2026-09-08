from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import AgentRunState, contract_sha256
from ads_booster.contracts.tool_capability import EffectClass
from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.work_continuation import continue_work
from tests.marketing.agent_service.test_application import (
    NOW,
    AskThenStopReasoning,
    EffectThenStopReasoning,
    FlakyReasoning,
    ResearchAdapter,
    _descriptor,  # pyright: ignore[reportPrivateUsage]
    _request,  # pyright: ignore[reportPrivateUsage]
    _service,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from pathlib import Path


def test_completed_work_accepts_idempotent_human_result_in_same_run(tmp_path: Path) -> None:
    service = _service(tmp_path / "state.db", AskThenStopReasoning(stop=True))
    run = service.create(_request(), now=NOW)
    result = continue_work(
        service,
        run.tenant_id,
        run.run_id,
        event_id="human-1",
        actor_id="member",
        note="Figma에서 만들었어. 캐릭터는 유지해줘",
        action="revise",
        now=NOW,
    )
    assert result.run_id == run.run_id
    revision = result.revision
    replay = continue_work(
        service,
        run.tenant_id,
        run.run_id,
        event_id="human-1",
        actor_id="member",
        note="Figma에서 만들었어. 캐릭터는 유지해줘",
        action="revise",
        now=NOW,
    )
    assert replay.revision == revision
    records = service.repository.records(run.tenant_id, run.run_id)
    assert "human_reported" in str([r.payload for r in records])
    with pytest.raises(ValueError, match="idempotency"):
        _ = continue_work(
            service,
            run.tenant_id,
            run.run_id,
            event_id="human-1",
            actor_id="member",
            note="다른 내용",
            action="revise",
            now=NOW,
        )


def test_pause_invalidates_pending_approval_and_survives_restart(tmp_path: Path) -> None:
    service = _service(tmp_path / "state.db", AskThenStopReasoning())
    service.reasoning = EffectThenStopReasoning()
    service.registry = ToolRegistry(
        (_descriptor("capture.appium", EffectClass.LOCAL_ARTIFACT, ready=True),)
    )
    service.tools = {"capture.appium": ResearchAdapter()}
    run = service.create(_request(), now=NOW)
    assert run.state is AgentRunState.AWAITING_APPROVAL
    pending = service.repository.records(run.tenant_id, run.run_id)[-1]
    paused = continue_work(
        service,
        run.tenant_id,
        run.run_id,
        event_id="pause-1",
        actor_id="member",
        note="잠깐 멈춰줘",
        action="pause",
        now=NOW,
    )
    assert paused.state is AgentRunState.AWAITING_INPUT
    restarted = _service(tmp_path / "state.db", AskThenStopReasoning(stop=True))
    assert restarted.drive(run.tenant_id, run.run_id, now=NOW).state is AgentRunState.AWAITING_INPUT
    with pytest.raises(ValueError, match="not_awaiting_approval"):
        _ = restarted.decide_approval(
            run.tenant_id,
            run.run_id,
            approver_id="member",
            granted=False,
            expected_invocation_sha256=contract_sha256(pending.payload),
            now=NOW,
        )


def test_continuation_commit_crash_replays_only_its_unsubmitted_input(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    service = _service(database, AskThenStopReasoning(stop=True))
    run = service.create(_request(), now=NOW)

    def crash(point: str) -> None:
        if point == "work_continuation_committed":
            message = "fixture continuation commit crash"
            raise RuntimeError(message)

    service.fault_hook = crash
    with pytest.raises(RuntimeError, match="fixture continuation"):
        _ = continue_work(
            service,
            "trace",
            run.run_id,
            event_id="human-1",
            actor_id="member",
            note="Keep the character",
            action="revise",
            now=NOW,
        )
    reasoning = AskThenStopReasoning(stop=True)
    restarted = _service(database, reasoning)
    resumed = continue_work(
        restarted,
        "trace",
        run.run_id,
        event_id="human-1",
        actor_id="member",
        note="Keep the character",
        action="revise",
        now=NOW,
    )
    assert resumed.state is AgentRunState.COMPLETED
    assert len(reasoning.requests) == 1
    inputs = [
        r
        for r in restarted.repository.records("trace", run.run_id)
        if r.payload_schema_version == "trace.agent-input-evidence.v1"
    ]
    assert len(inputs) == 1
    replay = continue_work(
        restarted,
        "trace",
        run.run_id,
        event_id="human-1",
        actor_id="member",
        note="Keep the character",
        action="revise",
        now=NOW,
    )
    assert replay.revision == resumed.revision
    assert len(reasoning.requests) == 1


def test_provider_failure_replay_drives_the_same_continuation_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    service = _service(database, AskThenStopReasoning(stop=True))
    run = service.create(_request(), now=NOW)
    service.reasoning = FlakyReasoning()
    with pytest.raises(RuntimeError, match="provider unavailable"):
        _ = continue_work(
            service,
            "trace",
            run.run_id,
            event_id="human-1",
            actor_id="member",
            note="Keep the calendar",
            action="revise",
            now=NOW,
        )
    reasoning = AskThenStopReasoning(stop=True)
    restarted = _service(database, reasoning)
    resumed = continue_work(
        restarted,
        "trace",
        run.run_id,
        event_id="human-1",
        actor_id="member",
        note="Keep the calendar",
        action="revise",
        now=NOW,
    )
    assert resumed.state is AgentRunState.COMPLETED
    assert len(reasoning.requests) == 1


def test_old_continuation_replay_cannot_resume_a_newer_human_pause(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    reasoning = AskThenStopReasoning(stop=True)
    service = _service(database, reasoning)
    run = service.create(_request(), now=NOW)
    _ = continue_work(
        service,
        "trace",
        run.run_id,
        event_id="human-1",
        actor_id="member",
        note="Keep the calendar",
        action="revise",
        now=NOW,
    )
    paused = continue_work(
        service,
        "trace",
        run.run_id,
        event_id="human-2",
        actor_id="member",
        note="Stop for human review",
        action="pause",
        now=NOW,
    )
    calls = len(reasoning.requests)
    replay = continue_work(
        service,
        "trace",
        run.run_id,
        event_id="human-1",
        actor_id="member",
        note="Keep the calendar",
        action="revise",
        now=NOW,
    )
    assert replay.state is AgentRunState.AWAITING_INPUT
    assert replay.revision == paused.revision
    assert len(reasoning.requests) == calls
