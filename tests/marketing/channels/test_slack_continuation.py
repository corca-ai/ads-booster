from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, override

from ads_booster.contracts.agent_memory import MemoryAccess, MemoryNote, MemoryScope
from ads_booster.contracts.agent_run import AgentRunState, contract_sha256
from ads_booster.contracts.reasoning import ReasoningDecision
from ads_booster.marketing.agent_service.memory import SQLiteMemoryStore
from tests.marketing.agent_service.test_application import (
    _reasoning_result,  # pyright: ignore[reportPrivateUsage]
)
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import (
    RecordingReasoning,
    receive,
    setup_events,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult


class AlternativesReasoning(RecordingReasoning):
    @override
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        self.requests.append(request)
        return _reasoning_result(
            request,
            ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="stop",
                expected_outcome="Compare two copy directions",
                reasoning_summary="1안: 아침 일정 확인. 2안: 퇴근 후 내 시간 찾기.",
            ),
        )


def test_followup_can_resolve_the_agents_previous_alternatives(tmp_path: Path) -> None:
    owner, _ = setup_events(tmp_path)
    provider = AlternativesReasoning()
    owner.commands.application.service.reasoning = provider
    receive(owner, text="<@UBOT> 홍보 문구 두 안을 제안해줘")
    assert owner.work_once(now=NOW)
    # A new channel/service instance must recover the same dialogue from SQLite.
    owner, _ = setup_events(tmp_path)
    owner.commands.application.service.reasoning = provider
    receive(owner, type="message", text="2안으로 짧게 써줘", ts="100.002", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    assert len(owner.commands.application.service.repository.list_runs("team")) == 1
    assert "퇴근 후 내 시간 찾기" in provider.requests[-1].model_dump_json()
    receive(owner, text="<@UBOT> 별도 질문", ts="200.001")
    assert owner.work_once(now=NOW)
    assert "퇴근 후 내 시간 찾기" not in provider.requests[-1].model_dump_json()


def test_natural_status_and_pause_do_not_create_new_work(tmp_path: Path) -> None:
    owner, messages = setup_events(tmp_path)
    provider = RecordingReasoning()
    owner.commands.application.service.reasoning = provider
    receive(owner)
    assert owner.work_once(now=NOW)
    receive(owner, type="message", text="어디까지 됐어?", ts="100.002", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    assert len(provider.requests) == 1
    receive(owner, type="message", text="잠깐 멈춰줘", ts="100.003", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    runs = owner.commands.application.service.repository.list_runs("team")
    assert len(runs) == 1
    assert runs[0].state is AgentRunState.AWAITING_INPUT
    assert "멈" in str(messages[-1])


def test_partial_correction_continues_same_run(tmp_path: Path) -> None:
    owner, _ = setup_events(tmp_path)
    provider = RecordingReasoning()
    owner.commands.application.service.reasoning = provider
    receive(owner, text="<@UBOT> 캐릭터 배경을 검토해줘")
    assert owner.work_once(now=NOW)
    receive(
        owner,
        type="message",
        text="캐릭터는 유지하고 위쪽 여백만 늘려줘",
        ts="100.002",
        thread_ts="100.001",
    )
    assert owner.work_once(now=NOW)
    runs = owner.commands.application.service.repository.list_runs("team")
    assert len(runs) == 1
    assert "위쪽 여백" in provider.requests[-1].model_dump_json()


def test_signed_file_share_is_preserved_without_visual_claim(tmp_path: Path) -> None:
    owner, _ = setup_events(tmp_path)
    provider = RecordingReasoning()
    owner.commands.application.service.reasoning = provider
    receive(
        owner,
        subtype="file_share",
        files=[{"id": "F123", "name": "background.png", "mimetype": "image/png"}],
    )
    assert owner.work_once(now=NOW)
    request = provider.requests[-1].model_dump_json()
    assert "F123" in request
    assert "reference_only_not_visually_inspected" in request


def test_reviewed_memory_enters_next_plan_but_expired_note_does_not(tmp_path: Path) -> None:

    owner, _ = setup_events(tmp_path)
    provider = RecordingReasoning()
    owner.commands.application.service.reasoning = provider
    store = SQLiteMemoryStore(owner.store.database_path)
    access = MemoryAccess(
        scope=MemoryScope(workspace_id="team", product_id="trace"),
        actor_id="member",
        can_review=True,
    )
    note = MemoryNote(
        note_id="font-note",
        scope=access.scope,
        category="preference",
        domain="asset_format",
        text="캐릭터에는 여백 있는 배경 선호",
        source_ref="human-review",
        source_sha256="a" * 64,
        author_id="member",
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    store.put(note, access, now=NOW)
    note = store.review(
        note.note_id, access, expected_sha256=contract_sha256(note), stage="review", now=NOW
    )
    _ = store.review(
        note.note_id, access, expected_sha256=contract_sha256(note), stage="approved", now=NOW
    )
    receive(owner, text="<@UBOT> 캐릭터 배경 추천")
    assert owner.work_once(now=NOW)
    assert "font-note" in provider.requests[-1].model_dump_json()
    run = owner.commands.application.service.repository.list_runs("team")[0]
    assert owner._current_memory(run, NOW + timedelta(days=2)) is None  # pyright: ignore[reportPrivateUsage]
