from __future__ import annotations

from ads_booster.candidate_generation import duplicate_indexes, normalize_topic


def _restates(first: str, second: str) -> bool:
    """The rule the batch actually applies: a match on either key is one topic."""
    return duplicate_indexes([first, second]) == (1,)


def test_word_order_does_not_make_a_second_topic() -> None:
    # Given the same subject written twice
    # When / Then
    assert _restates("야간 근무 전날 밤", "밤, 야간 근무 전날")
    assert _restates("아침 러닝", "러닝, 아침")


def test_spacing_does_not_make_a_second_topic() -> None:
    """No token comparison sees past a compound written with and without a space."""
    # Given / When / Then
    assert _restates("시험기간 일정 관리", "시험기간  일정관리!")


def test_a_trailing_particle_does_not_make_a_second_topic() -> None:
    # Given / When / Then
    assert _restates("직관을 앞둔 하루", "직관 앞둔 하루")


def test_two_different_topics_stay_different() -> None:
    """A shared word is not a shared subject, and the check must not pretend otherwise."""
    # Given / When / Then
    assert not _restates("퇴근 뒤 필라테스", "퇴근 전 장보기")
    assert not _restates("첫째 재우기", "둘째 재우기")
    assert not _restates("야간 근무 전날 밤", "야간 근무 다음 날 아침")


def test_a_short_word_is_not_eaten_by_the_particle_list() -> None:
    """Stripping a particle must never leave a fragment of a real word."""
    # Given / When / Then a two-letter word ending in a particle keeps both letters
    assert normalize_topic("포도") == ("포도", "포도")
    assert normalize_topic("바다") == ("바다", "바다")


def test_duplicates_are_reported_by_the_later_position() -> None:
    """The earliest occurrence keeps the topic, so the result follows the assigned order."""
    # Given three candidates where the third restates the first
    topics = ["야간 근무 전날 밤", "퇴근 뒤 필라테스", "밤, 야간 근무 전날"]

    # When / Then only the later position is named
    assert duplicate_indexes(topics) == (2,)
    assert duplicate_indexes(["아침 러닝", "저녁 산책", "주말 등산"]) == ()
    assert duplicate_indexes(["같은 주제", "같은 주제", "같은 주제"]) == (1, 2)
