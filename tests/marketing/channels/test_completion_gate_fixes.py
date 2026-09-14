from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.agent.service.drive_work import DriveWorkQueue
from ads_booster.agent.service.task_progress import TaskProjection, project_task, task_records
from ads_booster.channels import slack_commands
from ads_booster.channels.http.http_api import MarketingAgentApi
from ads_booster.channels.task_results import result_for
from ads_booster.contracts.agent_run import AgentRecordKind, AgentRunState, contract_sha256
from ads_booster.contracts.task_progress import TaskPolicy
from tests.marketing.agent_service.test_application import (
    AskThenStopReasoning,
    build_service,
    run_request,
)
from tests.marketing.channels.test_slack_commands import NOW, request, setup_commands

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path
    from sqlite3 import Connection

    from ads_booster.agent.service.drive_work import DriveClaim
    from ads_booster.channels.task_results import TaskResult
    from ads_booster.transport.json_types import JsonObject


def test_current_input_question_is_projected_from_real_service(tmp_path: Path) -> None:
    # Given an admitted request whose actual planner asks for evidence.
    service = build_service(tmp_path / "question.sqlite3", AskThenStopReasoning())
    run = service.create(run_request(), now=service.clock())
    assert run.state is AgentRunState.AWAITING_INPUT
    # When the channel projects that canonical waiting result.
    result = result_for(run, service.repository.records(run.tenant_id, run.run_id))
    # Then the user receives the exact question, without an accepted completion identity.
    assert result.text == "Ask for evidence first"
    assert result.task_disposition == "waiting"
    assert result.identity is None


def test_prior_question_is_not_reused_for_another_task_revision(tmp_path: Path) -> None:
    # Given the canonical question followed by a different persisted task revision.
    service = build_service(tmp_path / "stale.sqlite3", AskThenStopReasoning())
    run = service.create(run_request(), now=service.clock())
    records = service.repository.records(run.tenant_id, run.run_id)
    task = project_task(run, records)
    changed = task.spec.model_copy(update={"task_revision": 2, "prior_revision": 1})
    stale = TaskProjection(
        changed,
        task.checkpoint.model_copy(
            update={"task_revision": 2, "spec_sha256": contract_sha256(changed)}
        ),
    )
    # When a waiting projection has no question from that revision.
    result = result_for(run, (*records, *task_records(run, stale, NOW)))
    # Then the previous question is not shown as a current instruction.
    assert "Ask for evidence first" not in result.text
    assert result.identity is None


def test_http_run_detail_returns_the_current_question(tmp_path: Path) -> None:
    # Given the real waiting Run behind the authenticated HTTP API.
    service = build_service(tmp_path / "http.sqlite3", AskThenStopReasoning())
    run = service.create(run_request(), now=service.clock())
    api = MarketingAgentApi(service, "trace", "member", "secret")
    # When the user reads its result.
    response = api.dispatch("GET", f"/v1/runs/{run.run_id}", authorization="Bearer secret")
    # Then the API preserves the question and waiting status.
    assert response.status == 200
    assert isinstance(response.body, dict)
    assert response.body["task"] == {
        "disposition": "waiting",
        "result": "Ask for evidence first",
        "accepted_identity": None,
    }


def test_signed_slash_command_delivers_the_current_question(tmp_path: Path) -> None:
    # Given a signed slash command and a planner requesting evidence.
    owner = setup_commands(tmp_path)
    owner.application.service.reasoning = AskThenStopReasoning()
    messages: list[JsonObject] = []

    def send(payload: JsonObject) -> JsonObject:
        messages.append(payload)
        return {"ok": True, "ts": "question"}

    owner.sender = send
    body, headers = request()
    _ = owner.receive(body, headers, now=NOW)
    # When the command worker processes the admitted request.
    assert owner.work_once(now=NOW)
    # Then the delivered message is the actual question, not generic incomplete text.
    assert len(messages) == 1
    assert messages[0]["text"] == "Ask for evidence first"


@pytest.mark.parametrize("fault", ["before_commit", "after_commit"])
def test_slash_notify_recovers_outbox_crash_without_reexecution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    # Given a real signed command whose completion needs a durable notify claim.
    owner = setup_commands(tmp_path)
    owner.application.service.task_policy = TaskPolicy(slice_provider_calls=1)
    messages: list[JsonObject] = []

    def send(payload: JsonObject) -> JsonObject:
        messages.append(payload)
        return {"ok": True, "ts": "one"}

    owner.sender = send
    body, headers = request()
    _ = owner.receive(body, headers, now=NOW)
    assert owner.work_once(now=NOW)
    assert owner.work_once(now=NOW)
    run = owner.application.service.repository.list_runs("team")[0]
    assert run.state is AgentRunState.COMPLETED
    records = owner.application.service.repository.records("team", run.run_id)
    reasoning_count = sum(item.kind is AgentRecordKind.REASONING for item in records)
    discard = DriveWorkQueue.discard

    def fail_bind(_db: Connection, _notification_id: str, _result: TaskResult) -> None:
        message = "simulated_outbox_crash"
        raise RuntimeError(message)

    def fail_discard(
        queue: DriveWorkQueue, claim: DriveClaim, *, now: datetime | None = None
    ) -> None:
        if claim.phase == "notify":
            message = "simulated_outbox_crash"
            raise RuntimeError(message)
        _ = discard(queue, claim, now=now)

    with monkeypatch.context() as faults:
        if fault == "before_commit":
            faults.setattr(slack_commands, "bind_result", fail_bind)
        else:
            faults.setattr(DriveWorkQueue, "discard", fail_discard)
        with pytest.raises(RuntimeError, match="simulated_outbox_crash"):
            _ = owner.work_once(now=NOW)
    # When a reconstructed worker resumes the interrupted notification phase.
    restarted = setup_commands(tmp_path)
    restarted.sender = send
    restarted.recover()
    for _ in range(4):
        _ = restarted.work_once(now=NOW)
    # Then exactly one final response is delivered and planning is not repeated.
    assert len(messages) == 1
    assert messages[0]["text"] == "No execution tool is needed"
    after = restarted.application.service.repository.records("team", run.run_id)
    assert sum(item.kind is AgentRecordKind.REASONING for item in after) == reasoning_count
    assert not restarted.work_once(now=NOW)
