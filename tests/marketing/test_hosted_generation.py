"""Contract tests for the planless hosted Codex generation path."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pytest

from ads_booster.candidate_generation import DEFAULT_MAX_BATCH
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

_ASSIGNMENT: Final = re.compile(
    r"- 후보 \d+: 형태 (\w+) \(.+?\) · 도메인 (\w+) \(.+?\)(?: · 소재 축 (.+))?$",
    re.MULTILINE,
)


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


def _assignments(prompt: str) -> list[tuple[str, str, str]]:
    """Every assignment line this one prompt carries: form, domain, subject axis."""
    found: list[tuple[str, str, str]] = _ASSIGNMENT.findall(prompt)
    assert found, prompt
    return found


def _assigned_domains(prompt: str) -> list[str]:
    return [domain for _, domain, _ in _assignments(prompt)]


def _assigned_forms(prompt: str) -> list[str]:
    return [form for form, _, _ in _assignments(prompt)]


def _assigned_axes(prompt: str) -> list[str]:
    return [interest.strip() for _, _, interest in _assignments(prompt)]


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
    # How many candidates to leave out of each answer, so a short batch can be asserted on.
    short_by: int = 0
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
        if self.malformed:
            return {"candidates": [{"topic": "not enough fields"}]}
        assigned = _assigned_domains(prompt)
        kept = assigned[: len(assigned) - self.short_by] if self.short_by else assigned
        return {
            "candidates": [
                _candidate(f"{domain} 주제 {workspace.name}-{position}", domain)
                for position, domain in enumerate(kept)
            ]
        }


def _first(population: Sequence[CandidateDocument], count: int) -> Sequence[CandidateDocument]:
    return list(population)[:count]


def _executor(
    codex: FakeCodex,
    tmp_path: Path,
    *,
    max_batch: int = DEFAULT_MAX_BATCH,
) -> HostedWorkspaceGenerationExecutor:
    return HostedWorkspaceGenerationExecutor(
        codex=codex,
        output_root=tmp_path,
        sample_references=_first,
        max_batch=max_batch,
    )


def _provenance_of(result_candidates: JsonValue, position: int = 0) -> JsonObject:
    assert isinstance(result_candidates, list)
    candidate = result_candidates[position]
    assert isinstance(candidate, dict)
    provenance = candidate["provenance"]
    assert isinstance(provenance, dict)
    return provenance


def test_a_generation_task_is_one_structured_codex_turn(tmp_path: Path) -> None:
    # Given a current #66 hosted-workspace payload and a structured Codex reply
    codex = FakeCodex()
    executor = _executor(codex, tmp_path)

    # When the broker prepares it before admission and executes it after admission
    prepared = executor.prepare(_task())
    result = executor.execute(prepared)

    # Then the callback receives compatible candidate rows from one turn
    assert prepared.execution_admission.job_digest
    assert prepared.execution_admission.export_nonce
    assert result.status is TaskStatus.SUCCEEDED
    assert result.output["pipeline"] == PIPELINE
    assert result.output["persona_id"] == "persona-1"
    assert result.output["requested"] == 2
    assert result.output["failures"] == 0
    assert result.output["failure_reason"] is None
    candidates = result.output["candidates"]
    assert isinstance(candidates, list)
    assert len(candidates) == 2
    assert len(codex.calls) == 1
    assert codex.calls[0][0].name == "call-00"
    assert codex.calls[0][1] == 180.0
    assert codex.schemas[0]["type"] == "object"


def test_the_prompt_carries_the_corpus_the_account_and_a_line_per_candidate(
    tmp_path: Path,
) -> None:
    """This is the regression: the prompt it replaces read nothing and said 540 characters."""
    # Given the packaged corpus and a persona
    codex = FakeCodex()
    executor = _executor(codex, tmp_path)

    # When the batch runs
    _ = executor.execute(executor.prepare(_task()))

    # Then the core documents are in the prompt, by name and by body
    prompt = codex.prompts[0]
    assert "[context 문서: core/PRINCIPLES-KR.md]" in prompt
    assert "[context 문서: core/VOICE-KR.md]" in prompt
    assert "[context 문서: core/FACTS.md]" in prompt
    assert "[context 문서: references/KR/INDEX.md]" in prompt
    # And so are the reference bodies it sampled, not only the index table
    assert "[context 문서: references/KR/kr-001.md]" in prompt
    # And the account block names the person rather than asking for an invented one
    assert "[이 계정으로 씁니다]" in prompt
    assert "이서진" in prompt
    assert "병동 간호사" in prompt
    assert "[정체성 창작 규칙]" not in prompt
    # And each candidate is told what to be, in one line of its own
    assert "[후보별 배정]" in prompt
    assert _assigned_forms(prompt) == ["hook", "testimony"]
    assert _assigned_domains(prompt) == ["office_worker", "office_worker"]
    assert _assigned_axes(prompt) == ["쿠로미", "필라테스"]
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
    provenance = _provenance_of(result.output["candidates"])
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
    # Six hits and two flops, drawn once for the whole batch. Which eight is the corpus's
    # business — that there are eight, and that they are real reference ids, is this one's.
    reference_ids = provenance["reference_ids"]
    assert isinstance(reference_ids, list)
    assert len(reference_ids) == 8
    assert all(isinstance(item, str) and re.fullmatch(r"kr-\d+", item) for item in reference_ids)
    assert len(set(reference_ids)) == 8
    assert provenance["assigned_domains"] == ["office_worker", "office_worker"]
    assert provenance["batch_size"] == 2
    # And each candidate's own share of the assignment
    assert provenance["caption_form"] == "hook"
    assert provenance["assigned_interest"] == "쿠로미"
    second = _provenance_of(result.output["candidates"], 1)
    assert second["caption_form"] == "testimony"
    assert second["assigned_interest"] == "필라테스"


def test_recent_topics_from_the_control_plane_reach_the_prompt(tmp_path: Path) -> None:
    """The worker reads no database, so avoiding last week's batch has to arrive with the job."""
    # Given a payload carrying what this persona has already been given
    codex = FakeCodex()
    executor = _executor(codex, tmp_path)

    # When the batch runs
    _ = executor.execute(
        executor.prepare(_task(_payload(recent_topics=["야간 근무 전날 밤", "퇴근 뒤 필라테스"])))
    )

    # Then they are in the prompt as the recent list
    prompt = codex.prompts[0]
    assert "[최근 생성된 후보 목록]" in prompt
    assert "- [도메인 미기록] 야간 근무 전날 밤" in prompt
    assert "- [도메인 미기록] 퇴근 뒤 필라테스" in prompt


def test_a_publisher_that_sends_no_recent_topics_still_generates(tmp_path: Path) -> None:
    """A control plane that predates the field gets captions, just without this guard."""
    # Given a payload with no recent_topics key at all
    codex = FakeCodex()
    executor = _executor(codex, tmp_path)

    # When the batch runs
    result = executor.execute(executor.prepare(_task()))

    # Then it succeeds and the prompt simply has no recent list
    assert result.status is TaskStatus.SUCCEEDED
    assert "[최근 생성된 후보 목록]" not in codex.prompts[0]


def test_a_request_larger_than_one_batch_is_split_into_ordered_turns(tmp_path: Path) -> None:
    """Four full captions is already a long answer under the Codex wall clock."""
    # Given the largest request the control plane can publish
    codex = FakeCodex()
    executor = _executor(codex, tmp_path)

    # When it runs
    result = executor.execute(executor.prepare(_task(_payload(count=8))))

    # Then it was two turns of four, each in its own workspace
    assert len(codex.calls) == 2
    assert [workspace.name for workspace, _ in codex.calls] == ["call-00", "call-01"]
    assert len(_assigned_domains(codex.prompts[0])) == 4
    candidates = result.output["candidates"]
    assert isinstance(candidates, list)
    assert len(candidates) == 8
    # And the second turn was shown what the first one wrote
    assert "[최근 생성된 후보 목록]" in codex.prompts[1]
    assert "office_worker 주제 call-00-0" in codex.prompts[1]


def test_a_short_answer_delivers_what_arrived_and_reports_the_shortfall(
    tmp_path: Path,
) -> None:
    """Rejecting one good candidate because a second is missing costs the whole batch."""
    # Given a turn that answers with one of the two it was asked for
    codex = FakeCodex(short_by=1)
    executor = _executor(codex, tmp_path)

    # When the batch runs
    result = executor.execute(executor.prepare(_task()))

    # Then the one that arrived is delivered and the missing one is counted
    assert result.status is TaskStatus.SUCCEEDED
    candidates = result.output["candidates"]
    assert isinstance(candidates, list)
    assert len(candidates) == 1
    assert result.output["requested"] == 2
    assert result.output["failures"] == 1


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
        (_payload(recent_topics="not a list"), "hosted_generation_payload_invalid"),
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

    # When the provider boundary raises
    with pytest.raises(MarketingExecutionError) as raised:
        _ = executor.execute(prepared)

    # Then the worker never retries a turn that may have created side effects.
    assert raised.value.failure_code == "hosted_generation_codex_failed"
    assert raised.value.unknown_side_effect
    assert len(codex.calls) == 1


def test_malformed_codex_result_is_unknown_after_execution_admission(tmp_path: Path) -> None:
    # Given Codex returned after it received the request, but violated the output schema
    codex = FakeCodex(malformed=True)
    executor = _executor(codex, tmp_path)
    prepared = executor.prepare(_task())

    # When the executor parses that returned value
    with pytest.raises(MarketingExecutionError) as raised:
        _ = executor.execute(prepared)

    # Then it is guarded like every post-barrier result failure, after one retry.
    assert raised.value.failure_code == "hosted_generation_result_invalid"
    assert raised.value.unknown_side_effect
    assert len(codex.calls) == 2
