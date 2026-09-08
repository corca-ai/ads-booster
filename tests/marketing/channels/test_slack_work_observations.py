from __future__ import annotations

import re
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_memory import MemoryAccess, MemoryScope
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.learning.work_observations import WorkObservationStore
from tests.marketing.agent_service.test_work_observations import access, measurement
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import RecordingReasoning, receive, setup_events

if TYPE_CHECKING:
    from pathlib import Path


def test_human_effort_commands_continue_work_without_model_or_double_count(tmp_path: Path) -> None:
    owner, messages = setup_events(tmp_path)
    provider = RecordingReasoning()
    owner.commands.application.service.reasoning = provider
    receive(owner)
    assert owner.work_once(now=NOW)
    run = owner.commands.application.service.repository.list_runs("team")[0]
    receive(
        owner,
        type="message",
        text="작업 기록 현지화 12분 언어=ja 제목 수정",
        ts="100.002",
        thread_ts="100.001",
    )
    assert owner.work_once(now=NOW)
    assert len(provider.requests) == 1
    match = re.search(r"work-observation-[a-f0-9]+", str(messages[-1]))
    assert match is not None
    observation_id = match[0]
    receive(
        owner,
        type="message",
        text=f"작업 정정 {observation_id} 현지화 9분 언어=ja 실제 기록 정정",
        ts="100.003",
        thread_ts="100.001",
    )
    assert owner.work_once(now=NOW)
    receive(owner, type="message", text="작업 요약", ts="100.004", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    assert "9분" in str(messages[-1])
    assert "비용 단위 합계 0" in str(messages[-1])
    assert "통화 금액 아님" in str(messages[-1])
    access = MemoryAccess(
        scope=MemoryScope(workspace_id="team", product_id="trace", work_id=run.run_id),
        actor_id="member",
    )
    summary = WorkObservationStore(owner.store.database_path).summarize(access)
    assert summary.elapsed_minutes == 9
    assert len(summary.observations) == 1
    assert summary.observations[0].window_kind == "report_time"
    assert summary.observations[0].window_start == summary.observations[0].window_end
    assert len(owner.commands.application.service.repository.list_runs("team")) == 1
    receive(
        owner,
        type="message",
        text="작업 방향을 학생 타깃으로 바꿔줘",
        ts="100.005",
        thread_ts="100.001",
    )
    assert owner.work_once(now=NOW)
    assert len(provider.requests) == 2


def test_corrected_learning_excluded_from_retrieval_and_cannot_be_approved(tmp_path: Path) -> None:
    store = WorkObservationStore(tmp_path / "state.db")
    identity = access()
    original = measurement(identity)
    _ = store.record(original, identity)
    note = store.learning_candidate(
        identity,
        note_id="learning",
        observation_ids=("first",),
        observation="Japanese editing takes 12 reported minutes",
        counterexample="No capture comparison",
        applicability="This work",
        now=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    note = store.memory.review(
        note.note_id, identity, expected_sha256=contract_sha256(note), stage="review", now=NOW
    )
    approved = store.memory.review(
        note.note_id, identity, expected_sha256=contract_sha256(note), stage="approved", now=NOW
    )
    assert store.memory.select(identity, query="Japanese", run_id="small", now=NOW).notes
    _ = store.record(
        measurement(identity, observation_id="fixed", supersedes="first", elapsed_minutes=9),
        identity,
    )
    selection = store.memory.select(identity, query="Japanese", run_id="small", now=NOW)
    assert selection.notes == ()
    assert selection.receipt.selected == ()
    with pytest.raises(ValueError, match="learning_source_not_current"):
        _ = store.memory.review(
            note.note_id,
            identity,
            expected_sha256=contract_sha256(approved),
            stage="approved",
            now=NOW,
        )
