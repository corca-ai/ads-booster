"""Deterministic, metadata-only ranking shared by procedural skill catalogs."""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


def rank_skills[T](
    entries: Iterable[T],
    query: str,
    metadata: Callable[[T], tuple[str, str]],
    *,
    include_unmatched: bool = False,
) -> tuple[T, ...]:
    """Rank exact identities first, then Unicode keyword overlap; preserve ties.

    This is lexical navigation, not semantic retrieval or an authority decision.
    Callers must apply scope and source-currentness checks before ranking.
    """
    normalized = unicodedata.normalize("NFKC", query).casefold().strip()
    tokens = frozenset(re.findall(r"\w+", normalized)[:64])

    def score(entry: T) -> int:
        identity, description = metadata(entry)
        identity = unicodedata.normalize("NFKC", identity).casefold()
        description = unicodedata.normalize("NFKC", description).casefold()
        if not normalized:
            return 1
        exact = re.search(r"(?<![\w.-])" + re.escape(identity) + r"(?![\w.-])", normalized)
        identity_tokens = frozenset(re.findall(r"\w+", identity))
        description_tokens = frozenset(re.findall(r"\w+", description))
        return (
            (1000 if exact else 0)
            + 4 * len(tokens & identity_tokens)
            + len(tokens & description_tokens)
        )

    scored = [(score(entry), entry) for entry in entries]
    scored.sort(key=lambda item: item[0], reverse=True)
    return tuple(entry for relevance, entry in scored if relevance or include_unmatched)
