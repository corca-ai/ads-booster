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
    assign_domains,
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

_ASSIGNED_DOMAIN: Final = re.compile(r"- 후보 1: (\w+) \(")
_ACCOUNT_DOMAIN: Final = re.compile(r"persona_domain 은 (\w+) 으로 고정합니다")


def _account() -> CandidateAccountBrief:
    return CandidateAccountBrief(
        display_name="김도현",
        age=29,
        region="서울 성동구",
        occupation="백엔드 개발자",
        concept="야근과 직관 사이에서 버티는 개발자",
        domain=CandidatePersonaDomain.SPORTS_FAN,
        interests=("KIA 타이거즈",),
        life_rhythm="평일 10시 출근",
        background_subject=CandidateBackgroundSubject.SPORTS_TEAM,
        background_mood="야간 경기 조명이 켜진 외야 관중석",
    )


def assigned_domain(instruction: str) -> str:
    """Read back the domain this one call was told to write."""
    match = _ACCOUNT_DOMAIN.search(instruction) or _ASSIGNED_DOMAIN.search(instruction)
    assert match is not None, instruction
    return match.group(1)


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
class DomainAnswerClient:
    """Answers every call with a draft carrying the domain that call was assigned."""

    failures: dict[str, Exception] = field(default_factory=dict)
    malformed: dict[str, int] = field(default_factory=dict)
    instructions: list[str] = field(default_factory=list)
    call_ids: list[str] = field(default_factory=list)

    def draft(self, instruction: str, *, call_id: str) -> JsonValue:
        self.instructions.append(instruction)
        self.call_ids.append(call_id)
        domain = assigned_domain(instruction)
        failure = self.failures.get(domain)
        if failure is not None:
            raise failure
        remaining = self.malformed.get(domain, 0)
        if remaining:
            self.malformed[domain] = remaining - 1
            return {"candidates": [{"topic": "형식이 깨진 응답"}]}
        return {"candidates": [_draft(f"{domain} 주제", domain)]}


def _engine(client: DomainAnswerClient) -> CandidateDraftEngine:
    return CandidateDraftEngine(client=client, model="codex_cli", sample_references=first)


def _run(
    tmp_path: Path,
    client: DomainAnswerClient,
    *,
    domains: tuple[CandidatePersonaDomain, ...],
    brief: CandidateAccountBrief | None = None,
    history: tuple[CandidateHistoryEntry, ...] = (),
) -> CandidateDraftBatch:
    directory = write_context(tmp_path)
    return _engine(client).draft(
        bundle=CandidateContextSource(directory, required=REQUIRED_DOCUMENTS).load(),
        pool=CandidateReferenceSource(directory).load("KR"),
        domains=domains,
        brief=brief,
        history=history,
    )


def test_a_batch_is_one_provider_call_per_candidate(tmp_path: Path) -> None:
    """One call for the whole batch had to keep every candidate distinct by itself."""
    # Given three assigned domains
    client = DomainAnswerClient()

    # When the batch is drafted
    batch = _run(
        tmp_path,
        client,
        domains=(
            CandidatePersonaDomain.SPORTS_FAN,
            CandidatePersonaDomain.PARENTING,
            CandidatePersonaDomain.EXAM_PREPPER,
        ),
    )

    # Then each candidate cost its own call, in its own place, with its own form
    assert len(batch.drafts) == 3
    assert batch.failures == 0
    assert client.call_ids == ["00", "01", "02"]
    assert [drafted.caption_form for drafted in batch.drafts] == [
        CaptionForm.HOOK,
        CaptionForm.DAILY,
        CaptionForm.TESTIMONY,
    ]
    assert [drafted.draft.persona_domain for drafted in batch.drafts] == [
        CandidatePersonaDomain.SPORTS_FAN,
        CandidatePersonaDomain.PARENTING,
        CandidatePersonaDomain.EXAM_PREPPER,
    ]


def test_every_call_records_what_it_actually_read(tmp_path: Path) -> None:
    """Provenance is observed while the call runs, not asserted about it afterwards."""
    # Given
    client = DomainAnswerClient()

    # When one candidate is drafted
    batch = _run(tmp_path, client, domains=(CandidatePersonaDomain.SPORTS_FAN,))

    # Then the record names the documents, the sample and the instruction it paid for
    provenance = batch.drafts[0].provenance
    paths = tuple(document.relative_path for document in provenance.documents)
    assert paths[: len(REQUIRED_DOCUMENTS)] == REQUIRED_DOCUMENTS
    assert provenance.reference_ids == ("kr-900", "kr-901", "kr-902", "kr-904")
    assert provenance.assigned_domains == (CandidatePersonaDomain.SPORTS_FAN,)
    assert provenance.model == "codex_cli"
    assert provenance.instruction_chars == len(client.instructions[0])
    assert all(document.size_bytes > 0 for document in provenance.documents)


def test_every_call_reads_reference_bodies_not_only_the_index(tmp_path: Path) -> None:
    """The model used to cite ids from a table it had never seen the writing behind."""
    # Given
    client = DomainAnswerClient()

    # When
    _ = _run(tmp_path, client, domains=(CandidatePersonaDomain.SPORTS_FAN,))

    # Then the bodies themselves are in the instruction
    instruction = client.instructions[0]
    assert "[context 문서: references/KR/kr-900.md]" in instruction
    assert "# kr-900 hit 본문" in instruction
    assert "# kr-904 flop 본문" in instruction


def test_the_batch_puts_what_it_just_wrote_ahead_of_the_stored_history(tmp_path: Path) -> None:
    """Running the calls in order is what makes the topic guard real rather than best effort."""
    # Given a stored history and a two-candidate batch
    client = DomainAnswerClient()

    # When the batch runs
    _ = _run(
        tmp_path,
        client,
        domains=(CandidatePersonaDomain.SPORTS_FAN, CandidatePersonaDomain.PARENTING),
        history=(
            CandidateHistoryEntry(
                persona_domain=CandidatePersonaDomain.PET_OWNER, topic="산책 시간표"
            ),
        ),
    )

    # Then the first call sees only the stored topic and the second also sees the first
    assert "산책 시간표" in client.instructions[0]
    assert "sports_fan 주제" not in client.instructions[0]
    assert "- [스포츠 팬] sports_fan 주제" in client.instructions[1]
    assert "산책 시간표" in client.instructions[1]


def test_a_malformed_answer_is_retried_once_with_the_validation_error(tmp_path: Path) -> None:
    # Given a call whose first answer does not survive validation
    client = DomainAnswerClient(malformed={"sports_fan": 1})

    # When the batch runs
    batch = _run(tmp_path, client, domains=(CandidatePersonaDomain.SPORTS_FAN,))

    # Then the retry quotes back what was wrong, in its own place
    assert len(batch.drafts) == 1
    assert client.call_ids == ["00", "00-retry"]
    assert "직전 응답은 형식 검증을 통과하지 못했습니다." in client.instructions[1]
    assert "검증 오류:" in client.instructions[1]


def test_two_malformed_answers_give_up_on_that_candidate(tmp_path: Path) -> None:
    # Given a call that never returns a usable answer
    client = DomainAnswerClient(malformed={"sports_fan": 2})

    # When / Then the batch raises rather than inventing a candidate
    with pytest.raises(CandidateFormatError):
        _ = _run(tmp_path, client, domains=(CandidatePersonaDomain.SPORTS_FAN,))
    assert client.call_ids == ["00", "00-retry"]


def test_a_call_that_fails_keeps_the_candidates_that_worked(tmp_path: Path) -> None:
    """Two captions and one timeout is two captions worth keeping."""
    # Given one of three calls whose provider process dies
    client = DomainAnswerClient(failures={"parenting": RuntimeError("codex exited")})

    # When the batch runs
    batch = _run(
        tmp_path,
        client,
        domains=(
            CandidatePersonaDomain.SPORTS_FAN,
            CandidatePersonaDomain.PARENTING,
            CandidatePersonaDomain.EXAM_PREPPER,
        ),
    )

    # Then the shortfall is carried out rather than logged away
    assert len(batch.drafts) == 2
    assert batch.failures == 1
    assert [drafted.draft.persona_domain for drafted in batch.drafts] == [
        CandidatePersonaDomain.SPORTS_FAN,
        CandidatePersonaDomain.EXAM_PREPPER,
    ]


def test_a_batch_whose_every_call_fails_still_raises(tmp_path: Path) -> None:
    # Given every call failing at the provider boundary
    client = DomainAnswerClient(
        failures={
            "sports_fan": RuntimeError("codex exited"),
            "parenting": OSError("codex missing"),
        }
    )

    # When / Then nothing is reported as a success
    with pytest.raises(CandidateProviderError):
        _ = _run(
            tmp_path,
            client,
            domains=(CandidatePersonaDomain.SPORTS_FAN, CandidatePersonaDomain.PARENTING),
        )


def test_an_account_batch_writes_every_candidate_as_that_one_person(tmp_path: Path) -> None:
    # Given an account and a three-candidate batch
    client = DomainAnswerClient()
    brief = _account()

    # When the batch runs
    batch = _run(tmp_path, client, domains=(brief.domain,) * 3, brief=brief)

    # Then every call carries the account block and its fixed domain
    assert len(batch.drafts) == 3
    assert all("김도현" in instruction for instruction in client.instructions)
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
