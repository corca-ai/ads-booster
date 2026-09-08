from __future__ import annotations

from typing import Protocol, cast

from ddgs import (
    DDGS,  # pyright: ignore[reportUnknownVariableType] - optional package uses a lazy facade.
)
from pydantic import TypeAdapter


class _SearchClient(Protocol):
    def text(self, query: str, *, max_results: int) -> object: ...


def public_search(query: str) -> list[dict[str, str]]:
    return TypeAdapter[list[dict[str, str]]](list[dict[str, str]]).validate_python(
        cast("_SearchClient", DDGS(timeout=15)).text(query, max_results=5)
    )
