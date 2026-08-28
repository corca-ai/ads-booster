from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from ads_booster.candidate_generation.background_judge import BackgroundJudge, JudgePersona
from ads_booster.candidate_generation.background_selection import (
    EXHAUSTED_CODE,
    JudgedBackgroundSelector,
)
from ads_booster.providers.codex import ModelTurn
from ads_booster.search.image.background import BackgroundSearchError
from ads_booster.search.image.open_background import CollectedBackground, CollectedBackgrounds
from ads_booster.workspace import CandidateBackgroundGrade, CandidateQuerySource

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ads_booster.contracts.tools import ToolDescriptor
    from ads_booster.transport.json_types import JsonObject

_PERSONA = JudgePersona(
    topic="시험기간 일정 관리",
    subject="character_other",
    mood="늦은 밤 책상 위 스탠드 불빛",
    query="쿠로미 배경화면 고화질",
)


@dataclass(slots=True)
class _FakeClient:
    """Answers each judge call from a script, recording every history it was handed."""

    answers: list[str]
    histories: list[tuple[JsonObject, ...]] = field(default_factory=list)

    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn:
        del tools
        self.histories.append(history)
        return ModelTurn(self.answers.pop(0), ())


@dataclass(slots=True)
class _FakeFetcher:
    """Returns one prepared collection per round, recording the queries it was asked for."""

    rounds: list[CollectedBackgrounds]
    queries: list[str] = field(default_factory=list)

    def collect(self, query: str, limit: int = 6) -> CollectedBackgrounds:
        del limit
        self.queries.append(query)
        return self.rounds.pop(0)


def _image(image_id: str) -> CollectedBackground:
    return CollectedBackground(
        image_id=image_id,
        content=f"{image_id}-full".encode(),
        preview=f"{image_id}-preview".encode(),
        image_url=f"https://images.example.com/{image_id}.jpg",
        source_url=f"https://blog.example.com/{image_id}",
    )


def _collected(*image_ids: str) -> CollectedBackgrounds:
    return CollectedBackgrounds(
        query="쿠로미 배경화면 고화질",
        provider="duckduckgo",
        images=tuple(_image(image_id) for image_id in image_ids),
        results_seen=len(image_ids),
        passed_filters=len(image_ids),
    )


def _stocked(results: int, filtered: int) -> CollectedBackgrounds:
    """A round whose results were all stock-library previews, dropped before download."""
    return CollectedBackgrounds(
        query="쿠로미 배경화면 고화질",
        provider="duckduckgo",
        images=(),
        results_seen=results,
        passed_filters=0,
        filtered_stock=filtered,
    )


def _empty(results: int = 0) -> CollectedBackgrounds:
    """A round that produced no usable image, either from no results or from filtered ones."""
    return CollectedBackgrounds(
        query="쿠로미 배경화면 고화질",
        provider="duckduckgo",
        images=(),
        results_seen=results,
        passed_filters=0,
    )


def _graded(image_id: str, authenticity: str, persona_fit: str, background_fit: str) -> JsonObject:
    return {
        "id": image_id,
        "gated": False,
        "grades": {
            "authenticity": authenticity,
            "persona_fit": persona_fit,
            "background_fit": background_fit,
        },
        "note": f"{image_id} 근거",
    }


def _gated(image_id: str, reason: str) -> JsonObject:
    return {"id": image_id, "gated": True, "gate_reason": reason, "note": f"{image_id} 근거"}


def _answer(*verdicts: JsonObject) -> str:
    return json.dumps(list(verdicts), ensure_ascii=False)


def _selector(client: _FakeClient, fetcher: _FakeFetcher) -> JudgedBackgroundSelector:
    return JudgedBackgroundSelector(
        fetcher=fetcher,  # pyright: ignore[reportArgumentType]
        judge=BackgroundJudge(client=client, shuffle=_fixed_order),
        model="gpt-5.5",
    )


def _fixed_order(images: Sequence[CollectedBackground]) -> Sequence[CollectedBackground]:
    return tuple(reversed(list(images)))


def _slot(history: tuple[JsonObject, ...], position: int) -> bytes:
    """The preview bytes that sat in one position of one pairwise call."""
    content = history[0]["content"]
    assert isinstance(content, list)
    previews = [
        part["image_url"]
        for part in content
        if isinstance(part, dict) and part.get("type") == "input_image"
    ]
    value = previews[position]
    assert isinstance(value, str)
    return value.encode()


def test_the_highest_graded_image_wins_and_the_gated_ones_are_recorded(tmp_path: Path) -> None:
    # Given three collected images, one of which is a watermarked stock shot
    client = _FakeClient(
        answers=[
            _answer(
                _gated("img-a", "워터마크"),
                _graded("img-b", "중", "중", "중"),
                _graded("img-c", "상", "상", "상"),
            )
        ]
    )
    fetcher = _FakeFetcher(rounds=[_collected("img-a", "img-b", "img-c")])

    # When the judged selector picks one
    judged = _selector(client, fetcher).select(_PERSONA, tmp_path / "background.png")

    # Then the highest total wins and the bytes on disk are the ones that were judged
    assert judged.judgment.chosen_id == "img-c"
    assert judged.background.image_url == "https://images.example.com/img-c.jpg"
    assert (tmp_path / "background.png").read_bytes() == b"img-c-full"

    # And every image the judge saw is recorded, gated ones with their reason
    reviews = {review.image_id: review for review in judged.judgment.reviews}
    assert reviews["img-a"].gated is True
    assert reviews["img-a"].gate_reason == "워터마크"
    assert reviews["img-a"].grades is None
    assert reviews["img-c"].score == 9
    assert reviews["img-b"].score == 6
    assert judged.judgment.model == "gpt-5.5"
    assert judged.judgment.tie_broken is False


def test_the_judge_is_sent_the_images_it_is_judging(tmp_path: Path) -> None:
    # Given one collected image
    client = _FakeClient(answers=[_answer(_graded("img-a", "상", "상", "상"))])
    fetcher = _FakeFetcher(rounds=[_collected("img-a")])

    # When the selector runs
    _ = _selector(client, fetcher).select(_PERSONA, tmp_path / "background.png")

    # Then the call carried the downscaled preview as an image content part
    content = client.histories[0][0]["content"]
    assert isinstance(content, list)
    images = [part for part in content if isinstance(part, dict) and part["type"] == "input_image"]
    assert len(images) == 1
    assert images[0]["image_url"] == "data:image/jpeg;base64,aW1nLWEtcHJldmlldw=="

    # And the persona the caption came from is in the prompt the judge read
    prompt = content[0]
    assert isinstance(prompt, dict)
    text = prompt["text"]
    assert isinstance(text, str)
    assert "시험기간 일정 관리" in text
    assert "늦은 밤 책상 위 스탠드 불빛" in text
    assert "쿠로미 배경화면 고화질" in text


def test_the_presentation_order_comes_from_the_injected_shuffle(tmp_path: Path) -> None:
    # Given two collected images and a shuffle that reverses them
    client = _FakeClient(
        answers=[_answer(_graded("img-a", "상", "상", "상"), _gated("img-b", "굿즈 사진"))]
    )
    fetcher = _FakeFetcher(rounds=[_collected("img-a", "img-b")])

    # When the selector runs
    _ = _selector(client, fetcher).select(_PERSONA, tmp_path / "background.png")

    # Then the second image was presented first, so rank cannot stand in for quality
    content = client.histories[0][0]["content"]
    assert isinstance(content, list)
    labels = [
        part["text"]
        for part in content
        if isinstance(part, dict) and part.get("text") in {"[img-a]", "[img-b]"}
    ]
    assert labels == ["[img-b]", "[img-a]"]


def test_a_tie_is_broken_only_when_both_orders_name_the_same_image(tmp_path: Path) -> None:
    # Given two images within one point of each other, and a judge that agrees with itself
    client = _FakeClient(
        answers=[
            _answer(_graded("img-a", "상", "상", "상"), _graded("img-b", "상", "상", "중")),
            "B가 훨씬 자연스럽습니다. [[B]]",
            "이번에는 A쪽이 자연스럽습니다. [[A]]",
        ]
    )
    fetcher = _FakeFetcher(rounds=[_collected("img-a", "img-b")])

    # When the judged selector picks one
    judged = _selector(client, fetcher).select(_PERSONA, tmp_path / "background.png")

    # Then the pairwise verdict overrides the one-point lead
    assert judged.judgment.chosen_id == "img-b"
    assert judged.judgment.tie_broken is True
    assert judged.judgment.tie_break_inconsistent is False
    assert "자연스럽습니다" in judged.judgment.reason

    # And the pair was shown twice, positionally, never by its ids
    assert len(client.histories) == 3
    for history in client.histories[1:]:
        content = history[0]["content"]
        assert isinstance(content, list)
        labels = [
            part["text"]
            for part in content
            if isinstance(part, dict) and part.get("text") in {"[A]", "[B]", "[img-a]", "[img-b]"}
        ]
        assert labels == ["[A]", "[B]"]


def test_the_second_pairwise_order_really_swaps_the_images(tmp_path: Path) -> None:
    # Given a tie
    client = _FakeClient(
        answers=[
            _answer(_graded("img-a", "상", "상", "상"), _graded("img-b", "상", "상", "중")),
            "[[A]]",
            "[[B]]",
        ]
    )
    fetcher = _FakeFetcher(rounds=[_collected("img-a", "img-b")])

    # When the selector runs
    judged = _selector(client, fetcher).select(_PERSONA, tmp_path / "background.png")

    # Then position A held a different image in each call, and the consistent winner is the
    # image that was named under both orders rather than the one that sat in one position
    assert _slot(client.histories[1], 0) != _slot(client.histories[2], 0)
    assert _slot(client.histories[1], 0) == _slot(client.histories[2], 1)
    assert judged.judgment.tie_broken is True
    assert judged.judgment.chosen_id == "img-a"


def test_a_pairwise_verdict_that_flips_with_the_order_does_not_override_the_scores(
    tmp_path: Path,
) -> None:
    # Given a judge that picks whichever image is shown first
    client = _FakeClient(
        answers=[
            _answer(_graded("img-a", "상", "상", "상"), _graded("img-b", "상", "상", "중")),
            "앞쪽이 낫습니다. [[A]]",
            "앞쪽이 낫습니다. [[A]]",
        ]
    )
    fetcher = _FakeFetcher(rounds=[_collected("img-a", "img-b")])

    # When the judged selector runs
    judged = _selector(client, fetcher).select(_PERSONA, tmp_path / "background.png")

    # Then the position-biased verdict is discarded and the graded total decides
    assert judged.judgment.chosen_id == "img-a"
    assert judged.judgment.tie_broken is False
    assert judged.judgment.tie_break_inconsistent is True
    assert "순서를 바꿔 물었을 때" in judged.judgment.reason


def test_a_pairwise_call_that_cannot_pick_a_side_leaves_the_scores_alone(
    tmp_path: Path,
) -> None:
    # Given a judge that calls the pair even
    client = _FakeClient(
        answers=[
            _answer(_graded("img-a", "상", "상", "중"), _graded("img-b", "상", "상", "상")),
            "우열을 가릴 수 없습니다. [[C]]",
            "우열을 가릴 수 없습니다. [[C]]",
        ]
    )
    fetcher = _FakeFetcher(rounds=[_collected("img-a", "img-b")])

    # When the judged selector runs
    judged = _selector(client, fetcher).select(_PERSONA, tmp_path / "background.png")

    # Then the higher graded total keeps the win
    assert judged.judgment.chosen_id == "img-b"
    assert judged.judgment.tie_broken is False
    assert judged.judgment.tie_break_inconsistent is True


def test_a_pairwise_answer_without_a_verdict_token_is_retried_once(tmp_path: Path) -> None:
    # Given a first comparison that never emits the literal token
    client = _FakeClient(
        answers=[
            _answer(_graded("img-a", "상", "상", "상"), _graded("img-b", "상", "상", "중")),
            "둘 다 좋아 보입니다.",
            "다시 보니 B입니다. [[B]]",
            "이쪽도 B입니다. [[A]]",
        ]
    )
    fetcher = _FakeFetcher(rounds=[_collected("img-a", "img-b")])

    # When the judged selector runs
    judged = _selector(client, fetcher).select(_PERSONA, tmp_path / "background.png")

    # Then the retry turn quoted the format failure back and the pair still resolved
    retry = client.histories[2][-1]["content"]
    assert isinstance(retry, str)
    assert "판정 토큰" in retry
    assert judged.judgment.chosen_id == "img-b"
    assert judged.judgment.tie_broken is True


def test_all_survivors_failing_authenticity_triggers_one_rewritten_retry(tmp_path: Path) -> None:
    # Given a first round where every survivor reads as a staged promo shot
    client = _FakeClient(
        answers=[
            _answer(_graded("img-a", "하", "상", "상"), _gated("img-b", "뉴스 자막")),
            "쿠로미 인형 책상 사진",
            _answer(_graded("img-c", "상", "중", "상")),
        ]
    )
    fetcher = _FakeFetcher(rounds=[_collected("img-a", "img-b"), _collected("img-c")])

    # When the judged selector runs
    judged = _selector(client, fetcher).select(_PERSONA, tmp_path / "background.png")

    # Then the rewritten query was searched and both queries are on the record
    assert fetcher.queries == ["쿠로미 배경화면 고화질", "쿠로미 인형 책상 사진"]
    assert judged.judgment.chosen_id == "img-c"
    assert judged.judgment.query == "쿠로미 배경화면 고화질"
    assert judged.judgment.rewritten_query == "쿠로미 인형 책상 사진"


def test_every_round_judged_out_fails_instead_of_taking_the_first_result(
    tmp_path: Path,
) -> None:
    # Given three rounds whose images the judge threw out
    client = _FakeClient(
        answers=[
            _answer(_gated("img-a", "워터마크")),
            "쿠로미 인형 책상 사진",
            _answer(_graded("img-b", "하", "상", "상")),
            "쿠로미 스티커 노트북 사진",
            _answer(_gated("img-c", "굿즈 사진")),
        ]
    )
    fetcher = _FakeFetcher(rounds=[_collected("img-a"), _collected("img-b"), _collected("img-c")])

    # When the judged selector runs, then it refuses rather than falling back
    with pytest.raises(BackgroundSearchError) as failure:
        _ = _selector(client, fetcher).select(_PERSONA, tmp_path / "background.png")
    assert failure.value.code == EXHAUSTED_CODE
    assert "배경 심사에서 탈락" in failure.value.message
    assert not (tmp_path / "background.png").exists()

    # And the ladder stopped at three queries even though two stages wanted a retry
    assert len(fetcher.queries) == 3


def test_an_empty_search_is_retried_with_a_broadened_query_before_any_rewrite(
    tmp_path: Path,
) -> None:
    # Given a first query that finds nothing and a broadened one that works
    client = _FakeClient(answers=[_answer(_graded("img-a", "상", "상", "상"))])
    fetcher = _FakeFetcher(rounds=[_empty(), _collected("img-a")])

    # When the judged selector runs
    judged = _selector(client, fetcher).select(_PERSONA, tmp_path / "background.png")

    # Then the trailing qualifiers were dropped without spending a provider call
    assert fetcher.queries == ["쿠로미 배경화면 고화질", "쿠로미 배경화면"]
    assert len(client.histories) == 1
    assert judged.judgment.chosen_id == "img-a"

    # And both rungs are on the record with what each one returned
    assert [attempt.source for attempt in judged.judgment.attempts] == [
        CandidateQuerySource.ORIGINAL,
        CandidateQuerySource.BROADENED,
    ]
    assert judged.judgment.attempts[0].results == 0
    assert judged.judgment.attempts[1].passed_filters == 1


def test_a_broadened_query_that_also_finds_nothing_falls_back_to_a_rewrite(
    tmp_path: Path,
) -> None:
    # Given two empty rounds and a rewritten query that finds something
    client = _FakeClient(
        answers=["쿠로미 인형 책상 사진", _answer(_graded("img-a", "상", "중", "상"))]
    )
    fetcher = _FakeFetcher(rounds=[_empty(), _empty(), _collected("img-a")])

    # When the judged selector runs
    judged = _selector(client, fetcher).select(_PERSONA, tmp_path / "background.png")

    # Then the ladder climbed original → broadened → rewritten
    assert fetcher.queries == [
        "쿠로미 배경화면 고화질",
        "쿠로미 배경화면",
        "쿠로미 인형 책상 사진",
    ]
    assert [attempt.source for attempt in judged.judgment.attempts] == [
        CandidateQuerySource.ORIGINAL,
        CandidateQuerySource.BROADENED,
        CandidateQuerySource.REWRITTEN,
    ]
    assert judged.judgment.rewritten_query == "쿠로미 인형 책상 사진"


def test_three_empty_searches_report_that_nothing_was_found_with_every_query_tried(
    tmp_path: Path,
) -> None:
    # Given a subject nothing on the open web has published
    client = _FakeClient(answers=["쿠로미 인형 사진"])
    fetcher = _FakeFetcher(rounds=[_empty(), _empty(), _empty()])

    # When the judged selector runs
    with pytest.raises(BackgroundSearchError) as failure:
        _ = _selector(client, fetcher).select(_PERSONA, tmp_path / "background.png")

    # Then the reviewer is told it was the search, not the images, and which queries ran
    message = failure.value.message
    assert failure.value.code == EXHAUSTED_CODE
    assert "검색 결과가 없었습니다" in message
    assert "쿠로미 배경화면 고화질" in message
    assert "쿠로미 인형 사진" in message
    assert len(fetcher.queries) == 3


def test_results_that_all_fail_the_physical_checks_say_so_instead_of_no_results(
    tmp_path: Path,
) -> None:
    # Given searches that answer every time but whose images never pass the checks
    client = _FakeClient(answers=["쿠로미 인형 사진"])
    fetcher = _FakeFetcher(rounds=[_empty(results=7), _empty(results=5), _empty(results=6)])

    # When the judged selector runs
    with pytest.raises(BackgroundSearchError) as failure:
        _ = _selector(client, fetcher).select(_PERSONA, tmp_path / "background.png")

    # Then the diagnosis names the verification stage, not an empty search
    message = failure.value.message
    assert "검증(크기·형식·중복)에서 탈락" in message
    assert "18건" in message
    assert "검색 결과가 없었습니다" not in message


def test_the_rewrite_prompt_is_told_which_kind_of_empty_pool_it_is_fixing(
    tmp_path: Path,
) -> None:
    # Given a short query that cannot be broadened, whose results all failed verification
    persona = JudgePersona(
        topic=_PERSONA.topic,
        subject=_PERSONA.subject,
        mood=_PERSONA.mood,
        query="쿠로미",
    )
    client = _FakeClient(
        answers=["쿠로미 인형 책상 사진", _answer(_graded("img-a", "상", "상", "상"))]
    )
    fetcher = _FakeFetcher(rounds=[_empty(results=4), _collected("img-a")])

    # When the judged selector runs
    _ = _selector(client, fetcher).select(persona, tmp_path / "background.png")

    # Then it skipped straight to the rewrite and said why the pool was empty
    assert fetcher.queries == ["쿠로미", "쿠로미 인형 책상 사진"]
    prompt = client.histories[0][0]["content"]
    assert isinstance(prompt, str)
    assert "결과 4건이 모두 크기·형식 검증에서 탈락" in prompt


def test_a_malformed_verdict_is_retried_once_with_the_detail_quoted_back(tmp_path: Path) -> None:
    # Given a first answer that is not the JSON the judge was asked for
    client = _FakeClient(
        answers=[
            "가장 좋은 건 img-a 입니다.",
            "```json\n" + _answer(_graded("img-a", "상", "중", "상")) + "\n```",
        ]
    )
    fetcher = _FakeFetcher(rounds=[_collected("img-a")])

    # When the judged selector runs
    judged = _selector(client, fetcher).select(_PERSONA, tmp_path / "background.png")

    # Then the fenced retry is accepted and the retry turn quoted the failure back
    assert judged.judgment.chosen_id == "img-a"
    retry = client.histories[1][-1]["content"]
    assert isinstance(retry, str)
    assert "직전 응답을 읽을 수 없었습니다" in retry


def test_the_grade_scale_is_three_two_one() -> None:
    # Given the rubric grades, then they are worth what the scoring rule says
    assert CandidateBackgroundGrade.HIGH.value == "상"
    assert CandidateBackgroundGrade.MID.value == "중"
    assert CandidateBackgroundGrade.LOW.value == "하"


def test_the_grading_prompt_carries_the_rubric_steps_and_the_bias_rules(tmp_path: Path) -> None:
    # Given one collected image
    client = _FakeClient(answers=[_answer(_graded("img-a", "상", "상", "상"))])
    fetcher = _FakeFetcher(rounds=[_collected("img-a")])

    # When the selector runs
    _ = _selector(client, fetcher).select(_PERSONA, tmp_path / "background.png")

    # Then each criterion states its scale and the steps to walk before grading
    content = client.histories[0][0]["content"]
    assert isinstance(content, list)
    prompt = content[0]
    assert isinstance(prompt, dict)
    text = prompt["text"]
    assert isinstance(text, str)
    assert "① 진정성 (상/중/하)" in text
    assert "② 페르소나 적합 (상/중/하)" in text
    assert "③ 배경 적성 (상/중/하)" in text
    assert text.count("   1. ") == 3
    assert text.count("   3. ") == 3

    # And the judge is told which things must not sway it
    assert "순서가 판단에 영향을 주지 않게" in text
    assert "해상도·파일 크기·선명도는 품질 판단 근거가 아닙니다" in text


def test_a_long_query_drops_two_qualifiers_and_a_short_one_is_not_broadened(
    tmp_path: Path,
) -> None:
    # Given a four-token query that finds nothing
    persona = JudgePersona(
        topic=_PERSONA.topic,
        subject=_PERSONA.subject,
        mood=_PERSONA.mood,
        query="쿠로미 잠금화면 배경화면 고화질",
    )
    client = _FakeClient(answers=[_answer(_graded("img-a", "상", "상", "상"))])
    fetcher = _FakeFetcher(rounds=[_empty(), _collected("img-a")])

    # When the selector broadens it, then both trailing qualifiers go
    _ = _selector(client, fetcher).select(persona, tmp_path / "background.png")
    assert fetcher.queries == ["쿠로미 잠금화면 배경화면 고화질", "쿠로미 잠금화면"]


def test_a_two_token_query_skips_broadening_because_it_would_lose_the_subject(
    tmp_path: Path,
) -> None:
    # Given a query with nothing to strip but the subject itself
    persona = JudgePersona(
        topic=_PERSONA.topic,
        subject=_PERSONA.subject,
        mood=_PERSONA.mood,
        query="쿠로미 배경화면",
    )
    client = _FakeClient(
        answers=["쿠로미 인형 책상 사진", _answer(_graded("img-a", "상", "상", "상"))]
    )
    fetcher = _FakeFetcher(rounds=[_empty(), _collected("img-a")])

    # When the selector runs, then it goes straight to the rewrite
    judged = _selector(client, fetcher).select(persona, tmp_path / "background.png")
    assert fetcher.queries == ["쿠로미 배경화면", "쿠로미 인형 책상 사진"]
    assert [attempt.source for attempt in judged.judgment.attempts] == [
        CandidateQuerySource.ORIGINAL,
        CandidateQuerySource.REWRITTEN,
    ]


def test_a_whole_pool_rejection_tells_the_rewrite_what_the_judge_saw(tmp_path: Path) -> None:
    # Given a query whose four images were all judged staged or stock-looking
    client = _FakeClient(
        answers=[
            _answer(
                _graded("img-a", "하", "상", "상"),
                _graded("img-b", "하", "중", "상"),
                _gated("img-c", "스톡 워터마크"),
                _gated("img-d", "홍보용 연출 컷"),
            ),
            "쿠로미 인형 책상 사진",
            _answer(_graded("img-e", "상", "상", "상")),
        ]
    )
    fetcher = _FakeFetcher(
        rounds=[_collected("img-a", "img-b", "img-c", "img-d"), _collected("img-e")]
    )

    # When the ladder reaches the rewrite rung
    _ = _selector(client, fetcher).select(_PERSONA, tmp_path / "background.png")

    # Then the prompt leads with the pattern, not with four separate notes
    prompt = client.histories[1][0]["content"]
    assert isinstance(prompt, str)
    assert "직전 검색어의 이미지 4장이 전부 배경 심사에서 탈락" in prompt
    assert "스톡 워터마크" in prompt
    assert "홍보용 연출 컷" in prompt

    # And it is told what kind of photo to aim for instead
    assert "실제 유저가 자기 폰 배경화면으로 저장했을 법한 사진" in prompt
    assert "직업 소품·작업 공간·물건 나열형 검색어는 스톡 사진만 부르니 피하고" in prompt


def test_a_pool_lost_entirely_to_stock_sites_says_so_in_the_rewrite_prompt(
    tmp_path: Path,
) -> None:
    # Given a short query whose every result was a stock library
    persona = JudgePersona(
        topic=_PERSONA.topic,
        subject=_PERSONA.subject,
        mood=_PERSONA.mood,
        query="쿠로미",
    )
    client = _FakeClient(
        answers=["쿠로미 인형 책상 사진", _answer(_graded("img-a", "상", "상", "상"))]
    )
    fetcher = _FakeFetcher(rounds=[_stocked(results=5, filtered=5), _collected("img-a")])

    # When the ladder runs
    judged = _selector(client, fetcher).select(persona, tmp_path / "background.png")

    # Then the rewrite is told the results were stock, not that they failed verification
    prompt = client.histories[0][0]["content"]
    assert isinstance(prompt, str)
    assert "결과 5건이 모두 스톡 사진 사이트" in prompt

    # And the count is kept on the attempt record
    assert judged.judgment.attempts[0].filtered_stock == 5


def test_the_stock_count_is_named_when_the_judge_rejected_what_survived_it(
    tmp_path: Path,
) -> None:
    # Given a round where stock was filtered out and the rest was judged out
    stocked = CollectedBackgrounds(
        query="쿠로미 배경화면 고화질",
        provider="duckduckgo",
        images=(_image("img-a"),),
        results_seen=4,
        passed_filters=1,
        filtered_stock=3,
    )
    client = _FakeClient(
        answers=[
            _answer(_graded("img-a", "하", "상", "상")),
            "쿠로미 인형 책상 사진",
            _answer(_graded("img-b", "상", "상", "상")),
        ]
    )
    fetcher = _FakeFetcher(rounds=[stocked, _collected("img-b")])

    # When the ladder reaches the rewrite rung
    _ = _selector(client, fetcher).select(_PERSONA, tmp_path / "background.png")

    # Then the rewrite learns the pool was thin because stock was dropped first
    prompt = client.histories[1][0]["content"]
    assert isinstance(prompt, str)
    assert "스톡 사이트 결과 3건은 수집 전에 제외" in prompt
