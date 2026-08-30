from __future__ import annotations

import re
from dataclasses import dataclass, field
from threading import Barrier, BrokenBarrierError, Lock
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

_ASSIGNED_DOMAIN: Final = re.compile(r"- 후보 1: (\w+) \(")
_ACCOUNT_DOMAIN: Final = re.compile(r"persona_domain 은 (\w+) 으로 고정합니다")
_ASSIGNED_INTEREST: Final = re.compile(r'이 후보는 "(.+?)" 에서 출발합니다')
# Enough for the barrier to prove concurrency without hanging a suite if it never does.
_BARRIER_TIMEOUT: Final = 5.0

_THREE: Final = (
    CandidatePersonaDomain.SPORTS_FAN,
    CandidatePersonaDomain.PARENTING,
    CandidatePersonaDomain.EXAM_PREPPER,
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


def assigned_domain(instruction: str) -> str:
    """Read back the domain this one call was told to write."""
    match = _ACCOUNT_DOMAIN.search(instruction) or _ASSIGNED_DOMAIN.search(instruction)
    assert match is not None, instruction
    return match.group(1)


def assigned_interest(instruction: str) -> str | None:
    """Read back the subject axis this one call was told to start from."""
    match = _ASSIGNED_INTEREST.search(instruction)
    return None if match is None else match.group(1)


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
    """Answers every call with a draft keyed to what that call was assigned.

    Keyed rather than sequenced because a parallel batch has no call order: a list popped
    in turn cannot say which answer belongs to which candidate once the threads interleave.
    """

    failures: dict[str, Exception] = field(default_factory=dict)
    malformed: dict[str, int] = field(default_factory=dict)
    # Answers keyed by call id, for the tests that need one specific turn to differ.
    topics: dict[str, str] = field(default_factory=dict)
    # Turns that die at the provider boundary, named by call id rather than by domain.
    failing_call_ids: frozenset[str] = frozenset()
    # When set, every call waits here until this many are in flight at once.
    barrier: Barrier | None = None
    barrier_timeout: float = _BARRIER_TIMEOUT
    instructions: list[str] = field(default_factory=list)
    call_ids: list[str] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)

    def draft(self, instruction: str, *, call_id: str) -> JsonValue:
        with self.lock:
            self.instructions.append(instruction)
            self.call_ids.append(call_id)
        if self.barrier is not None:
            _ = self.barrier.wait(timeout=self.barrier_timeout)
        if call_id in self.failing_call_ids:
            timed_out = "codex_generation_job_timed_out"
            raise RuntimeError(timed_out)
        domain = assigned_domain(instruction)
        failure = self.failures.get(domain)
        if failure is not None:
            raise failure
        with self.lock:
            remaining = self.malformed.get(domain, 0)
            if remaining:
                self.malformed[domain] = remaining - 1
        if remaining:
            return {"candidates": [{"topic": "형식이 깨진 응답"}]}
        # Distinct per turn by default: an account batch gives every call the same domain,
        # and a fake that answered all of them identically would spend every test on the
        # duplicate check rather than on what it meant to assert.
        topic = self.topics.get(call_id, f"{domain} 주제 {call_id}")
        return {"candidates": [_draft(topic, domain)]}

    def instruction_for(self, domain: str) -> str:
        with self.lock:
            found = [text for text in self.instructions if assigned_domain(text) == domain]
        assert found, domain
        return found[0]


def _engine(client: DomainAnswerClient, *, max_workers: int = 4) -> CandidateDraftEngine:
    return CandidateDraftEngine(
        client=client,
        model="codex_cli",
        sample_references=first,
        max_workers=max_workers,
    )


def _run(  # noqa: PLR0913 - each argument is one independent input to the batch.
    tmp_path: Path,
    client: DomainAnswerClient,
    *,
    domains: tuple[CandidatePersonaDomain, ...],
    brief: CandidateAccountBrief | None = None,
    interests: Sequence[str] = (),
    history: tuple[CandidateHistoryEntry, ...] = (),
    max_workers: int = 4,
) -> CandidateDraftBatch:
    directory = write_context(tmp_path)
    return _engine(client, max_workers=max_workers).draft(
        bundle=CandidateContextSource(directory, required=REQUIRED_DOCUMENTS).load(),
        pool=CandidateReferenceSource(directory).load("KR"),
        domains=domains,
        brief=brief,
        interests=interests,
        history=history,
    )


def test_a_batch_is_one_provider_call_per_candidate(tmp_path: Path) -> None:
    """One call for the whole batch had to keep every candidate distinct by itself."""
    # Given three assigned domains
    client = DomainAnswerClient()

    # When the batch is drafted
    batch = _run(tmp_path, client, domains=_THREE)

    # Then each candidate cost its own call, in its own place, with its own form
    assert len(batch.drafts) == 3
    assert batch.failures == 0
    assert sorted(client.call_ids) == ["00", "01", "02"]
    assert [drafted.caption_form for drafted in batch.drafts] == [
        CaptionForm.HOOK,
        CaptionForm.DAILY,
        CaptionForm.TESTIMONY,
    ]
    # And the results come back in the order the batch assigned them, not the order the
    # turns happened to finish.
    assert [drafted.draft.persona_domain for drafted in batch.drafts] == list(_THREE)


def test_the_turns_actually_run_at_the_same_time(tmp_path: Path) -> None:
    """A barrier is the only honest proof: three turns that never overlap cannot pass it.

    Nothing in the execution path serialises a generation turn — each is its own process in
    its own workspace — so this is what that claim looks like when it is checked rather than
    asserted. A sequential engine deadlocks here and the barrier times out.
    """
    # Given a fake that will not answer until three calls are in flight together
    client = DomainAnswerClient(barrier=Barrier(3))

    # When a three-candidate batch runs
    batch = _run(tmp_path, client, domains=_THREE, max_workers=3)

    # Then every turn got through, which only happens if all three overlapped
    assert len(batch.drafts) == 3
    assert batch.parallel is True


def test_one_worker_runs_the_batch_in_order(tmp_path: Path) -> None:
    """The fallback for an environment that cannot afford concurrent Codex turns."""
    # Given a fake that would block forever if two calls ever overlapped
    client = DomainAnswerClient(barrier=Barrier(2), barrier_timeout=0.05)

    # When the batch is limited to one worker
    with pytest.raises(CandidateProviderError):
        _ = _run(tmp_path, client, domains=_THREE, max_workers=1)

    # Then the calls went out one at a time and each waited alone until the barrier broke
    assert client.call_ids[0] == "00"


def test_a_sequential_batch_shows_each_call_what_the_earlier_ones_wrote(
    tmp_path: Path,
) -> None:
    """Running in order buys one thing a parallel batch cannot have, and this is it."""
    # Given a two-candidate batch limited to one worker
    client = DomainAnswerClient()

    # When it runs
    _ = _run(
        tmp_path,
        client,
        domains=(CandidatePersonaDomain.SPORTS_FAN, CandidatePersonaDomain.PARENTING),
        max_workers=1,
    )

    # Then the second call sees the first call's topic and the first has nothing to avoid
    assert "sports_fan 주제" not in client.instructions[0]
    assert "- [스포츠 팬] sports_fan 주제" in client.instructions[1]


def test_a_parallel_batch_separates_candidates_by_assigned_axis(tmp_path: Path) -> None:
    """A parallel call cannot be shown what the others wrote, so the axis carries it."""
    # Given an account with two interests and a three-candidate batch
    client = DomainAnswerClient()
    brief = _account()

    # When it runs
    batch = _run(
        tmp_path,
        client,
        domains=(brief.domain,) * 3,
        brief=brief,
        interests=brief.interests,
    )

    # Then each call was told which axis to start from, cycling through the interests
    assert [drafted.assigned_interest for drafted in batch.drafts] == [
        "KIA 타이거즈",
        "주말 러닝",
        "KIA 타이거즈",
    ]
    axes = sorted(
        interest
        for instruction in client.instructions
        if (interest := assigned_interest(instruction)) is not None
    )
    assert axes == ["KIA 타이거즈", "KIA 타이거즈", "주말 러닝"]
    # And the block says what an axis is for, so it is not copied into the caption
    instruction = client.instructions[0]
    assert "[이번 후보의 소재 축]" in instruction
    assert "축은 소재를 고르는 출발점이지 캡션에 옮겨 적을 문구가 아닙니다" in instruction
    # And no call was told to avoid a list it was never shown
    assert "[최근 생성된 후보 목록]" not in instruction


def test_an_account_with_no_interests_still_generates(tmp_path: Path) -> None:
    """No axis is honest when there is nothing to divide by; it is not a failure."""
    # Given / When / Then
    assert assign_interests((), 3) == (None, None, None)
    client = DomainAnswerClient()
    batch = _run(tmp_path, client, domains=_THREE, interests=())
    assert len(batch.drafts) == 3
    assert all(drafted.assigned_interest is None for drafted in batch.drafts)
    assert "[이번 후보의 소재 축]" not in client.instructions[0]


def test_a_restated_topic_is_asked_again_once(tmp_path: Path) -> None:
    """Parallel turns cannot see each other, so the check happens after they land."""
    # Given two candidates that came back saying the same thing two ways
    client = DomainAnswerClient(
        topics={"00": "야간 근무 전날 밤", "01": "밤, 야간 근무 전날", "01-1": "주말 아침 등산"}
    )

    # When the batch runs
    batch = _run(
        tmp_path,
        client,
        domains=(CandidatePersonaDomain.SPORTS_FAN, CandidatePersonaDomain.PARENTING),
    )

    # Then the later one was rewritten once, in its own place, and the earlier one kept its
    # topic
    assert [drafted.draft.topic for drafted in batch.drafts] == [
        "야간 근무 전날 밤",
        "주말 아침 등산",
    ]
    assert [drafted.regenerated for drafted in batch.drafts] == [False, True]
    assert not any(drafted.duplicate_topic for drafted in batch.drafts)
    assert "01-1" in client.call_ids
    # And the rewrite was shown exactly what it collided with
    rewrite = client.instructions[client.call_ids.index("01-1")]
    assert "야간 근무 전날 밤" in rewrite


def test_a_rewrite_that_collides_again_is_kept_and_labelled(tmp_path: Path) -> None:
    """Two flagged captions beat one candidate silently thrown away."""
    # Given a rewrite that restates the same topic a second time
    client = DomainAnswerClient(
        topics={
            "00": "야간 근무 전날 밤",
            "01": "밤, 야간 근무 전날",
            "01-1": "야간 근무 전날의 밤",
        }
    )

    # When the batch runs
    batch = _run(
        tmp_path,
        client,
        domains=(CandidatePersonaDomain.SPORTS_FAN, CandidatePersonaDomain.PARENTING),
    )

    # Then both candidates survive and the second says what happened to it
    assert len(batch.drafts) == 2
    assert batch.drafts[1].regenerated is True
    assert batch.drafts[1].duplicate_topic is True
    assert batch.drafts[0].duplicate_topic is False


def test_a_failed_rewrite_keeps_the_draft_it_was_replacing(tmp_path: Path) -> None:
    """Losing a candidate because its rewrite timed out would be the worse trade."""
    # Given two candidates that collided, and a rewrite that dies at the provider boundary
    client = DomainAnswerClient(
        topics={"00": "같은 주제", "01": "같은 주제"},
        failing_call_ids=frozenset({"01-1"}),
    )

    # When the batch runs
    batch = _run(
        tmp_path,
        client,
        domains=(CandidatePersonaDomain.SPORTS_FAN, CandidatePersonaDomain.PARENTING),
    )

    # Then the original draft is still delivered, flagged rather than discarded
    assert len(batch.drafts) == 2
    assert batch.drafts[1].draft.topic == "같은 주제"
    assert batch.drafts[1].regenerated is True
    assert batch.drafts[1].duplicate_topic is True


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
    batch = _run(tmp_path, client, domains=_THREE)

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


def test_interest_assignment_cycles_when_there_are_fewer_axes_than_candidates() -> None:
    """Two candidates on one axis is a weaker guarantee, not a broken batch."""
    # Given / When / Then
    assert assign_interests(("야구", "러닝"), 5) == ("야구", "러닝", "야구", "러닝", "야구")
    assert assign_interests(("야구",), 2) == ("야구", "야구")


def test_the_barrier_helper_would_actually_catch_a_sequential_engine() -> None:
    """Guards the concurrency test above: a barrier nobody else reaches must break."""
    # Given a barrier expecting two parties and only one arriving
    barrier = Barrier(2)

    # When / Then it refuses rather than passing quietly
    with pytest.raises(BrokenBarrierError):
        _ = barrier.wait(timeout=0.05)
