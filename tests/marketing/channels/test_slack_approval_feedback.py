"""Signed Slack input errors must not mutate or retry pending production work."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

import pytest

from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.contracts.agent_run import AgentRecordKind, ToolInvocation, contract_sha256
from tests.marketing.agent_service.test_application import EffectThenStopReasoning, ResearchAdapter
from tests.marketing.channels.test_slack_commands import NOW, request
from tests.marketing.channels.test_slack_events import effect_descriptor, receive, setup_events

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "text", ["검토 1이 뭐야", "검토 -1", "검토 999", "review abc", "검토 ²", "검토 " + "9" * 7000]
)
def test_review_input_preserves_pending_invocation(tmp_path: Path, text: str) -> None:
    owner, messages = setup_events(tmp_path)
    service = owner.commands.application.service
    service.registry = ToolRegistry((effect_descriptor(),))
    service.reasoning = EffectThenStopReasoning()
    service.tools = {"creative.image.edit": ResearchAdapter()}
    receive(owner)
    assert owner.work_once(now=NOW)
    before = service.repository.list_runs("team")[0]
    records = service.repository.records("team", before.run_id)
    receive(owner, type="message", text=text, ts="100.002", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    assert "처리를 완료하지 못했습니다" not in str(messages[-1]["text"])
    assert "검토" in str(messages[-1]["text"])
    assert service.repository.list_runs("team")[0] == before
    assert service.repository.records("team", before.run_id) == records
    # Help itself adds no review evidence; the initial readable proposal is separate.


def test_review_without_pending_work_and_slash_invalid_input(tmp_path: Path) -> None:
    owner, messages = setup_events(tmp_path)
    receive(owner, text="<@UBOT> 검토 1")
    assert owner.work_once(now=NOW)
    assert "승인 대기 중인 작업이 없습니다" in str(messages[-1]["text"])
    body, headers = request("review absent 1이 뭐야")
    result = owner.commands.receive(body, headers, now=NOW)
    assert "검토" in str(result["text"])
    assert not owner.commands.application.service.repository.list_runs("team")


@pytest.mark.parametrize("cause", ["permission", "changed", "unavailable"])
def test_exact_approval_failure_is_actionable_without_execution(
    tmp_path: Path, cause: str, caplog: pytest.LogCaptureFixture
) -> None:
    owner, messages = setup_events(tmp_path)
    service = owner.commands.application.service
    service.registry = ToolRegistry((effect_descriptor(),))
    service.reasoning = EffectThenStopReasoning()
    service.tools = {"creative.image.edit": ResearchAdapter()}
    receive(owner)
    assert owner.work_once(now=NOW)
    before = service.repository.list_runs("team")[0]
    records = service.repository.records("team", before.run_id)
    invocation = ToolInvocation.model_validate(
        next(r.payload for r in reversed(records) if r.kind is AgentRecordKind.INVOCATION)
    )
    digest = contract_sha256(invocation)
    if cause == "permission":
        identity = owner.identity("U1")
        with closing(sqlite3.connect(owner.store.database_path)) as db, db:
            _ = db.execute(
                "UPDATE channel_identity_bindings SET binding_json=? WHERE binding_id=?",
                (
                    identity.model_copy(update={"can_approve": False}).model_dump_json(),
                    identity.binding_id,
                ),
            )
        expected, code = "승인 권한이 없습니다", "slack_approval_not_allowed"
    elif cause == "changed":
        digest = "0" * 64
        expected, code = "현재 승인안과 다릅니다", "agent_approval_invocation_changed"
    else:
        service.registry = ToolRegistry(())
        expected, code = "도구를 현재 사용할 수 없습니다", "tool_dispatch_no_longer_available"
    receive(owner, type="message", text=f"승인 {digest}", ts="100.002", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    assert expected in str(messages[-1]["text"])
    assert code in caplog.text
    assert service.repository.list_runs("team")[0] == before
    assert service.repository.records("team", before.run_id) == records
    assert not owner.work_once(now=NOW)


def test_unknown_failure_after_approval_does_not_leak_or_retry(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    owner, messages = setup_events(tmp_path)
    service = owner.commands.application.service
    service.registry = ToolRegistry((effect_descriptor(),))
    service.reasoning = EffectThenStopReasoning()
    service.tools = {"creative.image.edit": ResearchAdapter()}
    receive(owner)
    assert owner.work_once(now=NOW)
    run = service.repository.list_runs("team")[0]
    records = service.repository.records("team", run.run_id)
    invocation = ToolInvocation.model_validate(
        next(r.payload for r in reversed(records) if r.kind is AgentRecordKind.INVOCATION)
    )
    secret = "synthetic-provider-secret-do-not-print"  # noqa: S105 - redaction fixture.

    def fail_after_commit(stage: str) -> None:
        if stage == "approval_committed":
            raise RuntimeError(secret)

    service.fault_hook = fail_after_commit
    receive(
        owner,
        type="message",
        text=f"승인 {contract_sha256(invocation)}",
        ts="100.002",
        thread_ts="100.001",
    )
    assert owner.work_once(now=NOW)
    assert "승인을 반복하지 말고" in str(messages[-1]["text"])
    assert secret not in str(messages) + caplog.text
    assert "code=unclassified" in caplog.text
    after = service.repository.records("team", run.run_id)
    assert sum(r.kind is AgentRecordKind.APPROVAL for r in after) == 1
    assert not any(r.kind is AgentRecordKind.RECEIPT for r in after)
    assert not owner.work_once(now=NOW)
    assert service.repository.records("team", run.run_id) == after
