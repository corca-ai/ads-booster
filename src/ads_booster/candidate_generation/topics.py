"""Deciding whether two candidate topics are the same topic.

A batch written in parallel cannot show one call what the others wrote, so two candidates
can arrive saying the same thing in different words. This is the check that notices — and
it deliberately notices only restatement, not similarity: the same words in a different
order or under different particles are one topic, while two genuinely different subjects
that happen to share a word are two.

Nothing here is a threshold. A "close enough" score would be a number nobody can defend at
the moment it wrongly throws away a good candidate, so the rule is exact equality of a
normalized token set and nothing more.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

# Trailing particles, longest first so "에서" is stripped before "서" would be. Only the
# common ones: this is a normalizer, not a morphological analyser, and a particle list that
# reaches for completeness starts eating real word endings.
_PARTICLES: Final = (
    "에서",
    "으로",
    "에게",
    "까지",
    "부터",
    "이랑",
    "에",
    "의",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "로",
    "와",
    "과",
    "도",
    "만",
)
_WORD: Final = re.compile(r"[0-9a-z가-힣]+")
_MIN_STEM_CHARS: Final = 2


def normalize_topic(topic: str) -> tuple[str, str]:
    """Reduce a topic to the two keys a restatement of it would share.

    Two keys because there are two ways to write the same topic twice, and neither key
    catches the other's case. The first is the sorted set of stemmed words, which sees past
    word order: "야간 근무 전날 밤" and "밤, 야간 근무 전날" are one subject written twice.
    The second is those words run together, which sees past spacing: "일정 관리" and
    "일정관리" are one word written two ways, and no token comparison will ever say so.

    Matching on either key is a duplicate. Matching on neither is two topics.
    """
    tokens = [_stem(match.group()) for match in _WORD.finditer(topic.lower())]
    present = [token for token in tokens if token]
    return " ".join(sorted(set(present))), "".join(present)


def _stem(token: str) -> str:
    """Drop one trailing particle, but never enough of the token to leave a fragment."""
    for particle in _PARTICLES:
        stem = token.removesuffix(particle)
        if stem != token and len(stem) >= _MIN_STEM_CHARS:
            return stem
    return token


def duplicate_indexes(topics: Sequence[str]) -> tuple[int, ...]:
    """The positions that restate a topic an earlier position already claimed.

    The earliest occurrence keeps the topic and every later one is reported, so the result
    is a function of the order the batch was assigned rather than of which call finished
    first.
    """
    seen: set[str] = set()
    duplicates: list[int] = []
    for index, topic in enumerate(topics):
        keys = {key for key in normalize_topic(topic) if key}
        if keys & seen:
            duplicates.append(index)
        else:
            seen |= keys
    return tuple(duplicates)
