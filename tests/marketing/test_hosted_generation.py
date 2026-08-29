"""Contract tests for the planless hosted Codex generation path."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from ads_booster.marketing.hosted_generation import (
    PIPELINE,
    HostedWorkspaceGenerationExecutor,
)
from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind, TaskStatus

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject, JsonValue


def _payload(**overrides: JsonValue) -> JsonObject:
    payload: JsonObject = {
        "pipeline": PIPELINE,
        "persona_id": "persona-1",
        "persona": {
            "display_name": "이서진",
            "age": 27,
            "region": "서울 마포구",
            "occupation": "병동 간호사",
            "concept": "3교대를 잠금화면 일정으로 버티는 간호사",
            "domain": "office_worker",
            "interests": ["쿠로미", "필라테스"],
            "life_rhythm": "데이 출근일 5시 40분 기상",
            "taste": {
                "background_subject": "character_other",
                "background_mood": "파스텔 톤의 캐릭터 배경",
                "font": "sf_pro_rounded",
            },
        },
        "country": "KR",
        "language": "ko",
        "count": 2,
        "context_profile_id": "profile-1",
        "requested_by": "hosted_workspace",
    }
    payload.update(overrides)
    return payload


def _task(payload: JsonObject | None = None) -> MarketingTask:
    return MarketingTask(
        task_id="task-1",
        run_id="run-1",
        account_id="trace_demo_kr",
        kind=TaskKind.GENERATE_CANDIDATES,
        idempotency_key="hosted-generation:trace_demo_kr:task-1",
        payload=_payload() if payload is None else payload,
        created_at=datetime.now(UTC),
    )


def _candidate(topic: str = "야간 근무 전날 밤") -> JsonObject:
    return {
        "topic": topic,
        "country": "KR",
        "caption": "내일 나이트라 오늘은 일찍 눕는다.",
        "hypothesis": "교대 근무의 하루 리듬이 공감을 만든다.",
        "posting_slot": "evening",
        "persona_domain": "office_worker",
        "refs_used": [],
        "principles_applied": [3],
        "appium_prompt": "",
        "image_inputs": {
            "trace_items": [
                "05:40 기상",
                "07:00 출근",
                "12:30 점심 식사",
                "17:30 퇴근",
                "20:00 인계",
            ],
            "device_time": "22:40",
            "background_subject": "character_other",
            "background_mood": "파스텔 톤의 캐릭터 배경",
            "background_search_query": "쿠로미 배경화면",
            "language": "ko",
        },
    }


@dataclass(slots=True)
class FakeCodex:
    """A narrow protocol fake that records the actual structured turn boundary."""

    result: JsonObject = field(default_factory=lambda: {"candidates": [_candidate()]})
    error: RuntimeError | None = None
    calls: list[tuple[Path, float]] = field(default_factory=list)
    schemas: list[JsonObject] = field(default_factory=list)

    def run_generation_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        del prompt
        self.calls.append((workspace, timeout_seconds))
        self.schemas.append(schema)
        if self.error is not None:
            raise self.error
        return self.result


def test_generation_job_returns_hosted_candidate_shape_after_structured_codex_turn(
    tmp_path: Path,
) -> None:
    # Given a current #66 hosted-workspace payload and a structured Codex reply
    codex = FakeCodex(result={"candidates": [_candidate(), _candidate("퇴근 뒤 필라테스")]})
    executor = HostedWorkspaceGenerationExecutor(codex=codex, output_root=tmp_path)

    # When the broker prepares it before admission and executes it after admission
    prepared = executor.prepare(_task())
    result = executor.execute(prepared)

    # Then the callback receives compatible candidate rows with locally-owned provenance.
    assert prepared.execution_admission.job_digest
    assert prepared.execution_admission.export_nonce
    assert result.status is TaskStatus.SUCCEEDED
    assert result.output["pipeline"] == PIPELINE
    assert result.output["persona_id"] == "persona-1"
    assert result.output["requested"] == 2
    assert result.output["failures"] == 0
    candidates = result.output["candidates"]
    assert isinstance(candidates, list)
    assert len(candidates) == 2
    assert codex.calls == [(prepared.workspace, 180.0)]
    assert codex.schemas[0]["type"] == "object"


@pytest.mark.parametrize(
    ("payload", "failure_code"),
    [
        (_payload(pipeline="other"), "unsupported_hosted_generation_pipeline"),
        (_payload(country="korea"), "hosted_generation_payload_invalid"),
        (_payload(count=0), "hosted_generation_payload_invalid"),
        (_payload(persona={"display_name": "이서진"}), "hosted_generation_payload_invalid"),
    ],
)
def test_invalid_payload_fails_before_structured_codex_is_called(
    tmp_path: Path,
    payload: JsonObject,
    failure_code: str,
) -> None:
    # Given malformed input received from the hosted control plane
    codex = FakeCodex()
    executor = HostedWorkspaceGenerationExecutor(codex=codex, output_root=tmp_path)

    # When it is parsed before the worker crosses its execution barrier
    with pytest.raises(MarketingExecutionError) as raised:
        _ = executor.prepare(_task(payload))

    # Then it is an ordinary failed task and no provider turn was spent.
    assert raised.value.failure_code == failure_code
    assert not raised.value.unknown_side_effect
    assert codex.calls == []


def test_codex_transport_failure_is_unknown_after_execution_admission(tmp_path: Path) -> None:
    # Given a valid, admitted request whose Codex process fails while executing
    codex = FakeCodex(error=RuntimeError("process exited"))
    executor = HostedWorkspaceGenerationExecutor(codex=codex, output_root=tmp_path)
    prepared = executor.prepare(_task())

    # When the provider boundary raises
    with pytest.raises(MarketingExecutionError) as raised:
        _ = executor.execute(prepared)

    # Then the worker never retries a turn that may have created side effects.
    assert raised.value.failure_code == "hosted_generation_codex_failed"
    assert raised.value.unknown_side_effect
    assert len(codex.calls) == 1


def test_malformed_codex_result_is_unknown_after_execution_admission(tmp_path: Path) -> None:
    # Given Codex returned after it received the request, but violated the output schema
    codex = FakeCodex(result={"candidates": [{"topic": "not enough fields"}]})
    executor = HostedWorkspaceGenerationExecutor(codex=codex, output_root=tmp_path)
    prepared = executor.prepare(_task())

    # When the executor parses that returned value
    with pytest.raises(MarketingExecutionError) as raised:
        _ = executor.execute(prepared)

    # Then it is guarded like every post-barrier result failure.
    assert raised.value.failure_code == "hosted_generation_result_invalid"
    assert raised.value.unknown_side_effect
