from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import pytest

from ads_booster.candidate_generation import (
    REQUIRED_DOCUMENTS,
    CandidateContextSource,
    CandidateDraftEngine,
    CandidateFormatError,
    CandidateProviderError,
    CandidateReferenceSource,
    CaptionForm,
    assign_candidates,
    assign_domains,
    assign_interests,
)
from ads_booster.workspace import (
    CandidateAccountBrief,
    CandidateBackgroundSubject,
    CandidateHistoryEntry,
    CandidatePersonaDomain,
)
from tests.candidate_generation._corpus import first, write_context

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ads_booster.candidate_generation import CandidateDraftBatch
    from ads_booster.transport.json_types import JsonObject, JsonValue

_ASSIGNED: Final = re.compile(r"- 후보 (\d+): 형태 (\w+) \(.+?\) · 도메인 (\w+) \(")

_FOUR: Final = (
    CandidatePersonaDomain.SPORTS_FAN,
    CandidatePersonaDomain.PARENTING,
    CandidatePersonaDomain.EXAM_PREPPER,
    CandidatePersonaDomain.PET_OWNER,
)


def _account() -> CandidateAccountBrief:
    return CandidateAccountBrief(
        display_name="김도현",
        age=29,
        region="서울 성동구",
        occupation="백엔드 개발자",
        concept="야근과 직관 사이에서 버티는 개발자",
        domain=CandidatePersonaDomain.SPORTS_FAN,
        interests=("KIA 타이거즈", "주말 러닝"),
        life_rhythm="평일 10시 출근",
        background_subject=CandidateBackgroundSubject.SPORTS_TEAM,
        background_mood="야간 경기 조명이 켜진 외야 관중석",
    )


def assigned_domains(instruction: str) -> list[str]:
    """Read back the domains this one call was told to write, in order."""
    found: list[tuple[str, str, str]] = _ASSIGNED.findall(instruction)
    assert found, instruction
    return [domain for _, _, domain in found]


def _draft(topic: str, domain: str) -> JsonObject:
    return {
        "topic": topic,
        "country": "KR",
        "posting_slot": "evening",
        "persona_domain": domain,
        "caption": f"{topic} — 잠금화면부터 바꾼다",
        "hypothesis": "1인칭 감탄이 저장률을 올린다",
        "refs_used": ["kr-001"],
        "principles_applied": [1, 4],
        "appium_prompt": "입력_일정: 09:00 스터디",
        "image_inputs": {
            "trace_items": [
                "05:50 한강 러닝",
                "09:00 통계학 2교시",
                "13:00 스터디",
                "19:00 저녁 약속",
                "22:30 인강 복습",
            ],
            "device_time": "07:20",
            "background_subject": "scenery",
            "background_mood": "늦은 밤 책상 위 스탠드 불빛",
            "language": "ko",
        },
    }


@dataclass(slots=True)
class BatchAnswerClient:
    """Answers each call with one draft per domain that call was assigned."""

    failures: dict[str, Exception] = field(default_factory=dict)
    # How many answers to leave out, so a short response can be asserted on.
    short_by: int = 0
    malformed: int = 0
    instructions: list[str] = field(default_factory=list)
    call_ids: list[str] = field(default_factory=list)

    def draft(self, instruction: str, *, call_id: str) -> JsonValue:
        self.instructions.append(instruction)
        self.call_ids.append(call_id)
        failure = self.failures.get(call_id)
        if failure is not None:
            raise failure
        if self.malformed:
            self.malformed -= 1
            return {"candidates": [{"topic": "형식이 깨진 응답"}]}
        domains = assigned_domains(instruction)
        kept = domains[: len(domains) - self.short_by] if self.short_by else domains
        return {
            "candidates": [
                _draft(f"{domain} 주제 {call_id}-{position}", domain)
                for position, domain in enumerate(kept)
            ]
        }


def _engine(client: BatchAnswerClient, **overrides: int) -> CandidateDraftEngine:
    return CandidateDraftEngine(
        client=client,
        model="codex_cli",
        sample_references=first,
        **overrides,
    )


def _run(  # noqa: PLR0913 - each argument is one independent input to the request.
    tmp_path: Path,
    client: BatchAnswerClient,
    *,
    domains: tuple[CandidatePersonaDomain, ...],
    brief: CandidateAccountBrief | None = None,
    interests: Sequence[str] = (),
    history: tuple[CandidateHistoryEntry, ...] = (),
    **overrides: int,
) -> CandidateDraftBatch:
    directory = write_context(tmp_path)
    return _engine(client, **overrides).draft(
        bundle=CandidateContextSource(directory, required=REQUIRED_DOCUMENTS).load(),
        pool=CandidateReferenceSource(directory).load("KR"),
        domains=domains,
        brief=brief,
        interests=interests,
        history=history,
    )


def test_a_batch_of_four_is_one_provider_call(tmp_path: Path) -> None:
    """The whole batch is one Codex turn, and its candidates are told apart in the prompt."""
    # Given four assigned domains
    client = BatchAnswerClient()

    # When the batch is drafted
    batch = _run(tmp_path, client, domains=_FOUR)

    # Then one call produced all four, each carrying its own assignment
    assert client.call_ids == ["00"]
    assert len(batch.drafts) == 4
    assert batch.failures == 0
    assert batch.failure_reason is None
    assert [drafted.draft.persona_domain for drafted in batch.drafts] == list(_FOUR)
    assert [drafted.caption_form for drafted in batch.drafts] == [
        CaptionForm.HOOK,
        CaptionForm.DAILY,
        CaptionForm.HOOK,
        CaptionForm.TESTIMONY,
    ]


def test_the_instruction_states_one_assignment_line_per_candidate(tmp_path: Path) -> None:
    """A batch left to differentiate itself writes the same post four times."""
    # Given an account with two interests and a four-candidate batch
    client = BatchAnswerClient()
    brief = _account()

    # When it runs
    _ = _run(
        tmp_path,
        client,
        domains=(brief.domain,) * 4,
        brief=brief,
        interests=brief.interests,
    )

    # Then every candidate has its own line, naming its form, domain and subject axis
    instruction = client.instructions[0]
    assert "[후보별 배정]" in instruction
    assert (
        "- 후보 1: 형태 hook (훅글) · 도메인 sports_fan (스포츠 팬) · 소재 축 KIA 타이거즈"
        in instruction
    )
    assert (
        "- 후보 2: 형태 daily (일상글) · 도메인 sports_fan (스포츠 팬) · 소재 축 주말 러닝"
        in instruction
    )
    assert (
        "- 후보 4: 형태 testimony (간증글) · 도메인 sports_fan (스포츠 팬) · 소재 축 주말 러닝"
        in instruction
    )
    # And each assigned form is explained once, with its evidence and an example
    assert "- hook (훅글): 질문이나 한 문장 헤드라인으로 열고" in instruction
    assert "근거 레퍼런스: kr-001, kr-003, kr-014." in instruction
    assert "- testimony (간증글): 쓰기 전과 후에" in instruction
    assert "근거 레퍼런스: kr-010." in instruction
    assert "간증글은 한 배치에 많아야 하나입니다" in instruction
    # And the axis is framed as where to start, not as words to copy
    assert "캡션에 옮겨 적을 문구가 아니라" in instruction


def test_a_form_nobody_was_assigned_is_not_explained(tmp_path: Path) -> None:
    """A guide for a form nobody was given is an invitation to write it anyway."""
    # Given a two-candidate batch, which gets a hook and a testimonial but no daily
    client = BatchAnswerClient()

    # When it runs
    _ = _run(
        tmp_path,
        client,
        domains=(CandidatePersonaDomain.SPORTS_FAN, CandidatePersonaDomain.PARENTING),
    )

    # Then only the two assigned forms are explained
    instruction = client.instructions[0]
    assert "- hook (훅글):" in instruction
    assert "- testimony (간증글):" in instruction
    assert "- daily (일상글):" not in instruction


def test_a_batch_carries_at_most_one_testimonial(tmp_path: Path) -> None:
    """Testimony is the one form that claims something, so it is capped rather than cycled."""
    # Given / When / Then no batch size turns the feed into an ad break
    for count in range(1, 5):
        forms = [assignment.form for assignment in assign_candidates(_FOUR[:count])]
        assert len(forms) == count
        assert forms.count(CaptionForm.TESTIMONY) <= 1
    # And a single candidate is not spent on the one form that talks about the product
    batch = _run(tmp_path, BatchAnswerClient(), domains=(CandidatePersonaDomain.SPORTS_FAN,))
    assert batch.drafts[0].caption_form is CaptionForm.HOOK


def test_a_request_larger_than_one_batch_is_written_in_order(tmp_path: Path) -> None:
    """Four full captions is already a long answer; eight is two calls, not one long shot."""
    # Given eight candidates and the default batch ceiling of four
    client = BatchAnswerClient()

    # When the request runs
    batch = _run(tmp_path, client, domains=(*_FOUR, *_FOUR))

    # Then it was two calls, and the second was shown what the first wrote
    assert client.call_ids == ["00", "01"]
    assert len(batch.drafts) == 8
    assert "[최근 생성된 후보 목록]" not in client.instructions[0]
    assert "sports_fan 주제 00-0" in client.instructions[1]


def test_a_failed_call_costs_its_own_candidates_and_names_why(tmp_path: Path) -> None:
    """One batch of four and one failed call is four captions worth keeping."""
    # Given the second of two calls dying at the provider boundary
    client = BatchAnswerClient(failures={"01": RuntimeError("codex exited")})

    # When the request runs
    batch = _run(tmp_path, client, domains=(*_FOUR, *_FOUR))

    # Then the first batch survives and the shortfall is reported with a reason
    assert len(batch.drafts) == 4
    assert batch.failures == 4
    assert batch.failure_reason is not None
    assert "AI 요청에 실패했습니다" in batch.failure_reason


def test_a_short_answer_keeps_the_candidates_it_did_return(tmp_path: Path) -> None:
    """Rejecting three good candidates because a fourth is missing costs the whole request."""
    # Given a call that answers with three of the four it was asked for
    client = BatchAnswerClient(short_by=1)

    # When the batch runs
    batch = _run(tmp_path, client, domains=_FOUR)

    # Then the three arrive, bound to the first three assignments, and one is counted lost
    assert len(batch.drafts) == 3
    assert batch.failures == 1
    assert [drafted.draft.persona_domain for drafted in batch.drafts] == list(_FOUR[:3])
    assert [drafted.caption_form for drafted in batch.drafts] == [
        CaptionForm.HOOK,
        CaptionForm.DAILY,
        CaptionForm.HOOK,
    ]


def test_a_request_whose_every_call_fails_still_raises(tmp_path: Path) -> None:
    # Given the only call failing at the provider boundary
    client = BatchAnswerClient(failures={"00": RuntimeError("codex exited")})

    # When / Then nothing is reported as a success
    with pytest.raises(CandidateProviderError):
        _ = _run(tmp_path, client, domains=_FOUR)


def test_a_malformed_answer_is_retried_once_with_the_validation_error(tmp_path: Path) -> None:
    # Given a call whose first answer does not survive validation
    client = BatchAnswerClient(malformed=1)

    # When the batch runs
    batch = _run(tmp_path, client, domains=_FOUR)

    # Then the retry quotes back what was wrong, in its own place
    assert len(batch.drafts) == 4
    assert client.call_ids == ["00", "00-retry"]
    assert "직전 응답은 형식 검증을 통과하지 못했습니다." in client.instructions[1]
    assert "검증 오류:" in client.instructions[1]


def test_two_malformed_answers_give_up_on_that_batch(tmp_path: Path) -> None:
    # Given a call that never returns a usable answer
    client = BatchAnswerClient(malformed=2)

    # When / Then the request raises rather than inventing candidates
    with pytest.raises(CandidateFormatError):
        _ = _run(tmp_path, client, domains=_FOUR)
    assert client.call_ids == ["00", "00-retry"]


def test_a_batch_reads_six_hits_and_two_flops(tmp_path: Path) -> None:
    """One call carries the whole batch, so it can afford a wider sample than four calls."""
    # Given the fake corpus, which has four hits and two flops
    client = BatchAnswerClient()

    # When a batch runs
    batch = _run(tmp_path, client, domains=_FOUR)

    # Then it asked for six and two, took what the corpus had, and recorded exactly that
    provenance = batch.drafts[0].provenance
    assert provenance.reference_ids == ("kr-900", "kr-901", "kr-902", "kr-903", "kr-904", "kr-905")
    assert "# kr-905 flop 본문" in client.instructions[0]


def test_the_sample_shrinks_until_the_instruction_fits(tmp_path: Path) -> None:
    """The corpus keeps growing and the provider's context does not."""
    # Given the size the same batch reaches with a full sample
    full = _run(tmp_path, BatchAnswerClient(), domains=_FOUR).drafts[0].provenance
    client = BatchAnswerClient()

    # When it is written under a ceiling below that
    batch = _run(
        tmp_path,
        client,
        domains=_FOUR,
        max_instruction_chars=full.instruction_chars - 200,
    )

    # Then it fits, having given up hits first — what did not work is the half of the
    # corpus that says where the line is, so a flop survives
    provenance = batch.drafts[0].provenance
    assert provenance.instruction_chars <= full.instruction_chars - 200
    assert len(provenance.reference_ids) < len(full.reference_ids)
    assert "kr-905" in provenance.reference_ids


def test_the_core_documents_never_give_way(tmp_path: Path) -> None:
    """An instruction without the voice or the facts is not shorter, it is different."""
    # Given a ceiling no sample size can meet
    client = BatchAnswerClient()

    # When the batch is written anyway
    batch = _run(tmp_path, client, domains=_FOUR, max_instruction_chars=1)

    # Then it still carries every named document rather than refusing to write
    paths = tuple(document.relative_path for document in batch.drafts[0].provenance.documents)
    assert paths[: len(REQUIRED_DOCUMENTS)] == REQUIRED_DOCUMENTS
    assert len(batch.drafts) == 4


def test_the_history_it_is_handed_reaches_the_prompt(tmp_path: Path) -> None:
    """The control plane knows what last week's batch said; the worker has no store."""
    # Given topics the control plane sent with the request
    client = BatchAnswerClient()

    # When the batch runs
    _ = _run(
        tmp_path,
        client,
        domains=_FOUR,
        history=(
            CandidateHistoryEntry(persona_domain=None, topic="야간 근무 전날 밤"),
            CandidateHistoryEntry(
                persona_domain=CandidatePersonaDomain.PARENTING, topic="첫째 재우기"
            ),
        ),
    )

    # Then they are in the prompt, with the domain shown only where one was recorded
    instruction = client.instructions[0]
    assert "[최근 생성된 후보 목록]" in instruction
    assert "- [도메인 미기록] 야간 근무 전날 밤" in instruction
    assert "- [육아] 첫째 재우기" in instruction


def test_the_call_records_what_it_read_and_how_many_it_wrote(tmp_path: Path) -> None:
    """Provenance is observed while the call runs, not asserted about it afterwards."""
    # Given
    client = BatchAnswerClient()

    # When a four-candidate batch is drafted
    batch = _run(tmp_path, client, domains=_FOUR)

    # Then every candidate carries the record of the one call that wrote them all
    provenance = batch.drafts[0].provenance
    assert all(drafted.provenance == provenance for drafted in batch.drafts)
    paths = tuple(document.relative_path for document in provenance.documents)
    assert paths[: len(REQUIRED_DOCUMENTS)] == REQUIRED_DOCUMENTS
    assert provenance.assigned_domains == _FOUR
    assert provenance.batch_size == 4
    assert provenance.model == "codex_cli"
    assert provenance.instruction_chars == len(client.instructions[0])
    assert all(document.size_bytes > 0 for document in provenance.documents)


def test_every_call_reads_reference_bodies_not_only_the_index(tmp_path: Path) -> None:
    """The model used to cite ids from a table it had never seen the writing behind."""
    # Given
    client = BatchAnswerClient()

    # When
    _ = _run(tmp_path, client, domains=_FOUR)

    # Then the bodies themselves are in the instruction
    instruction = client.instructions[0]
    assert "[context 문서: references/KR/kr-900.md]" in instruction
    assert "# kr-900 hit 본문" in instruction
    assert "# kr-904 flop 본문" in instruction


def test_an_account_batch_writes_every_candidate_as_that_one_person(tmp_path: Path) -> None:
    # Given an account and a four-candidate batch
    client = BatchAnswerClient()
    brief = _account()

    # When the batch runs
    batch = _run(tmp_path, client, domains=(brief.domain,) * 4, brief=brief)

    # Then the one call carries the account block and its fixed domain
    assert len(batch.drafts) == 4
    assert "김도현" in client.instructions[0]
    assert "[정체성 창작 규칙]" not in client.instructions[0]
    assert all(
        drafted.draft.persona_domain is CandidatePersonaDomain.SPORTS_FAN
        for drafted in batch.drafts
    )


def test_domain_assignment_binds_one_candidate_to_one_domain() -> None:
    """A batch left to pick its own genres writes the same one and reports variety."""

    # Given a shuffle that keeps declaration order, so the expectation is nameable
    def identity(
        domains: Sequence[CandidatePersonaDomain],
    ) -> Sequence[CandidatePersonaDomain]:
        return tuple(domains)

    # When four domains are assigned
    assigned = assign_domains(4, identity)

    # Then they are four different ones, drawn from the closed vocabulary
    assert assigned == (
        CandidatePersonaDomain.SPORTS_FAN,
        CandidatePersonaDomain.IDOL_FANDOM,
        CandidatePersonaDomain.EXAM_PREPPER,
        CandidatePersonaDomain.PARENTING,
    )
    assert len(set(assigned)) == 4


def test_interest_assignment_cycles_and_survives_an_account_with_none() -> None:
    """Two candidates on one axis is a weaker guarantee, not a broken batch."""
    # Given / When / Then
    assert assign_interests(("야구", "러닝"), 5) == ("야구", "러닝", "야구", "러닝", "야구")
    assert assign_interests((), 3) == (None, None, None)


def test_a_batch_without_interests_carries_no_axis(tmp_path: Path) -> None:
    """No axis is honest when there is nothing to divide by; it is not a failure."""
    # Given an account-less batch
    client = BatchAnswerClient()

    # When it runs
    batch = _run(tmp_path, client, domains=_FOUR, interests=())

    # Then the assignment lines carry a form and a domain and stop there
    assert all(drafted.assigned_interest is None for drafted in batch.drafts)
    assert "· 소재 축" not in client.instructions[0]
    assert "- 후보 1: 형태 hook (훅글) · 도메인 sports_fan (스포츠 팬)" in client.instructions[0]
