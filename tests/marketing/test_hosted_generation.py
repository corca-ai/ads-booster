"""Contract tests for the planless hosted Codex generation path."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pytest

from ads_booster.marketing.hosted_generation import (
    PIPELINE,
    HostedWorkspaceGenerationExecutor,
)
from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind, TaskStatus
from ads_booster.workspace import CandidatePersonaDomain

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ads_booster.candidate_generation import CandidateDocument
    from ads_booster.transport.json_types import JsonObject, JsonValue

_ACCOUNT_DOMAIN: Final = re.compile(r"persona_domain 은 (\w+) 으로 고정합니다")
_ASSIGNED_DOMAIN: Final = re.compile(r"- 후보 1: (\w+) \(")


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


def _assigned_domain(prompt: str) -> str:
    match = _ACCOUNT_DOMAIN.search(prompt) or _ASSIGNED_DOMAIN.search(prompt)
    assert match is not None, prompt
    return match.group(1)


def _candidate(topic: str, domain: str) -> JsonObject:
    return {
        "topic": topic,
        "country": "KR",
        "caption": "내일 나이트라 오늘은 일찍 눕는다.",
        "hypothesis": "교대 근무의 하루 리듬이 공감을 만든다.",
        "posting_slot": "evening",
        "persona_domain": domain,
        "refs_used": ["kr-001"],
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

    error: RuntimeError | None = None
    malformed: bool = False
    # Turns beyond this many fail, so a partial batch can be asserted on.
    fail_after: int | None = None
    calls: list[tuple[Path, float]] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    schemas: list[JsonObject] = field(default_factory=list)

    def run_generation_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        self.calls.append((workspace, timeout_seconds))
        self.prompts.append(prompt)
        self.schemas.append(schema)
        if self.error is not None:
            raise self.error
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            timed_out = "codex_generation_job_timed_out"
            raise RuntimeError(timed_out)
        if self.malformed:
            return {"candidates": [{"topic": "not enough fields"}]}
        domain = _assigned_domain(prompt)
        return {"candidates": [_candidate(f"{domain} 주제", domain)]}


def _first(population: Sequence[CandidateDocument], count: int) -> Sequence[CandidateDocument]:
    return list(population)[:count]


def _executor(codex: FakeCodex, tmp_path: Path) -> HostedWorkspaceGenerationExecutor:
    return HostedWorkspaceGenerationExecutor(
        codex=codex,
        output_root=tmp_path,
        sample_references=_first,
    )


def test_generation_job_returns_hosted_candidate_shape_after_structured_codex_turns(
    tmp_path: Path,
) -> None:
    # Given a current #66 hosted-workspace payload and a structured Codex reply
    codex = FakeCodex()
    executor = _executor(codex, tmp_path)

    # When the broker prepares it before admission and executes it after admission
    prepared = executor.prepare(_task())
    result = executor.execute(prepared)

    # Then the callback receives compatible candidate rows, one Codex turn each
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
    assert len(codex.calls) == 2
    # And each turn ran in its own directory, because the Codex invocation receipt refuses
    # a second run in the same place.
    assert [workspace.name for workspace, _ in codex.calls] == ["call-00", "call-01"]
    assert len({workspace for workspace, _ in codex.calls}) == 2
    assert all(timeout == 180.0 for _, timeout in codex.calls)
    assert codex.schemas[0]["type"] == "object"


def test_the_prompt_carries_the_context_corpus_the_account_and_the_form(
    tmp_path: Path,
) -> None:
    """This is the regression: the prompt it replaces read nothing and said 540 characters."""
    # Given the packaged corpus and a persona
    codex = FakeCodex()
    executor = _executor(codex, tmp_path)

    # When the batch runs
    prepared = executor.prepare(_task())
    _ = executor.execute(prepared)

    # Then the core documents are in the prompt, by name and by body
    prompt = codex.prompts[0]
    assert "[context 문서: core/PRINCIPLES-KR.md]" in prompt
    assert "[context 문서: core/VOICE-KR.md]" in prompt
    assert "[context 문서: core/FACTS.md]" in prompt
    assert "[context 문서: references/KR/INDEX.md]" in prompt
    # And so are the reference bodies this call sampled, not only the index table
    assert "[context 문서: references/KR/kr-001.md]" in prompt
    # And the account block names the person rather than asking for an invented one
    assert "[이 계정으로 씁니다]" in prompt
    assert "이서진" in prompt
    assert "병동 간호사" in prompt
    assert "[정체성 창작 규칙]" not in prompt
    # And the batch's two candidates were told to open in two different ways
    assert "- 후보 1: hook (훅글)" in codex.prompts[0]
    assert "- 후보 1: testimony (간증글)" in codex.prompts[1]
    # And it is an instruction, not a sentence
    assert len(prompt) > 20_000


def test_provenance_records_what_the_call_read_rather_than_an_empty_shell(
    tmp_path: Path,
) -> None:
    # Given
    codex = FakeCodex()
    executor = _executor(codex, tmp_path)

    # When
    result = executor.execute(executor.prepare(_task()))

    # Then every provenance field is a fact the call observed
    candidates = result.output["candidates"]
    assert isinstance(candidates, list)
    first_candidate = candidates[0]
    assert isinstance(first_candidate, dict)
    provenance = first_candidate["provenance"]
    assert isinstance(provenance, dict)
    documents = provenance["documents"]
    assert isinstance(documents, list)
    paths: list[str] = []
    for document in documents:
        assert isinstance(document, dict)
        relative_path = document["relative_path"]
        assert isinstance(relative_path, str)
        assert isinstance(document["size_bytes"], int)
        paths.append(relative_path)
    assert set(paths) >= {"core/FACTS.md", "core/VOICE-KR.md", "references/KR/INDEX.md"}
    assert provenance["model"] == "codex_cli"
    assert provenance["instruction_chars"] == len(codex.prompts[0])
    assert provenance["reference_ids"] == ["kr-001", "kr-002", "kr-003", "kr-004"]
    assert provenance["assigned_domains"] == ["office_worker"]
    assert provenance["caption_form"] == "hook"


def test_a_second_call_is_told_what_the_first_one_already_wrote(tmp_path: Path) -> None:
    """Topic separation is the guard that can only act while the batch is running."""
    # Given
    codex = FakeCodex()
    executor = _executor(codex, tmp_path)

    # When
    _ = executor.execute(executor.prepare(_task()))

    # Then the second turn sees the first turn's topic and the first has nothing to avoid
    assert "office_worker 주제" not in codex.prompts[0]
    assert "- [직군 직장인] office_worker 주제" in codex.prompts[1]


def test_a_request_without_a_persona_spreads_the_batch_across_domains(tmp_path: Path) -> None:
    # Given a payload with no persona to write as
    codex = FakeCodex()
    executor = _executor(codex, tmp_path)

    # When the batch is prepared
    prepared = executor.prepare(_task(_payload(persona=None, count=3)))

    # Then each candidate is bound to its own domain, and no account block is built
    assert prepared.brief is None
    assert len(prepared.domains) == 3
    assert len(set(prepared.domains)) == 3
    assert all(domain in set(CandidatePersonaDomain) for domain in prepared.domains)


def test_a_persona_domain_outside_the_vocabulary_fails_before_admission(
    tmp_path: Path,
) -> None:
    """Relabelling an account's domain quietly is the drift this path exists to stop."""
    # Given a persona whose domain is not one this system counts coverage over
    codex = FakeCodex()
    executor = _executor(codex, tmp_path)
    persona = _payload()["persona"]
    assert isinstance(persona, dict)

    # When it is prepared
    with pytest.raises(MarketingExecutionError) as raised:
        _ = executor.prepare(_task(_payload(persona={**persona, "domain": "지역 맛집 탐방"})))

    # Then the task fails plainly and no provider turn was spent
    assert raised.value.failure_code == "hosted_generation_persona_domain_unknown"
    assert not raised.value.unknown_side_effect
    assert codex.calls == []


def test_a_country_without_a_reference_corpus_fails_before_admission(tmp_path: Path) -> None:
    """A batch written from the wrong audience's posts is worse than no batch."""
    # Given a request for a country the corpus does not cover
    codex = FakeCodex()
    executor = _executor(codex, tmp_path)

    # When it is prepared
    with pytest.raises(MarketingExecutionError) as raised:
        _ = executor.prepare(_task(_payload(country="JP", language="ja")))

    # Then it is an ordinary failed task rather than a spent Codex turn
    assert raised.value.failure_code == "hosted_generation_context_unavailable"
    assert not raised.value.unknown_side_effect
    assert codex.calls == []


def test_an_unreadable_context_directory_fails_before_admission(tmp_path: Path) -> None:
    # Given a configured corpus location with nothing in it
    codex = FakeCodex()
    executor = HostedWorkspaceGenerationExecutor(
        codex=codex,
        output_root=tmp_path,
        context_directory=tmp_path / "missing-context",
    )

    # When / Then
    with pytest.raises(MarketingExecutionError) as raised:
        _ = executor.prepare(_task())
    assert raised.value.failure_code == "hosted_generation_context_unavailable"
    assert codex.calls == []


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
    executor = _executor(codex, tmp_path)

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
    executor = _executor(codex, tmp_path)
    prepared = executor.prepare(_task())

    # When the provider boundary raises for every candidate
    with pytest.raises(MarketingExecutionError) as raised:
        _ = executor.execute(prepared)

    # Then the worker never retries a turn that may have created side effects.
    assert raised.value.failure_code == "hosted_generation_codex_failed"
    assert raised.value.unknown_side_effect
    assert len(codex.calls) == 2


def test_malformed_codex_result_is_unknown_after_execution_admission(tmp_path: Path) -> None:
    # Given Codex returned after it received the request, but violated the output schema
    codex = FakeCodex(malformed=True)
    executor = _executor(codex, tmp_path)
    prepared = executor.prepare(_task())

    # When the executor parses that returned value
    with pytest.raises(MarketingExecutionError) as raised:
        _ = executor.execute(prepared)

    # Then it is guarded like every post-barrier result failure, after one retry each.
    assert raised.value.failure_code == "hosted_generation_result_invalid"
    assert raised.value.unknown_side_effect
    assert len(codex.calls) == 4


def test_one_failed_turn_keeps_the_candidates_the_others_produced(tmp_path: Path) -> None:
    """A batch that produced one of two is one worth keeping, and the shortfall is reported."""
    # Given a batch whose second turn dies at the process boundary
    codex = FakeCodex(fail_after=1)
    executor = _executor(codex, tmp_path)

    # When it runs
    result = executor.execute(executor.prepare(_task()))

    # Then the successful candidate is delivered and the failure is counted
    assert result.status is TaskStatus.SUCCEEDED
    candidates = result.output["candidates"]
    assert isinstance(candidates, list)
    assert len(candidates) == 1
    assert result.output["requested"] == 2
    assert result.output["failures"] == 1
