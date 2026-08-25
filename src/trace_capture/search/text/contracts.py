from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol, override
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SearchResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    title: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=4_096)
    snippet: str = Field(default="", max_length=4_000)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            message = "search result URL must use http or https"
            raise ValueError(message)
        return value


class SearchResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    provider: str = Field(min_length=1, max_length=64)
    query: str = Field(min_length=1, max_length=1_000)
    results: tuple[SearchResult, ...] = Field(max_length=10)


class WebSearchProvider(Protocol):
    def search(self, query: str, max_results: int) -> SearchResponse: ...


@dataclass(frozen=True, slots=True)
class WebSearchError(Exception):
    code: str
    message: str

    @override
    def __str__(self) -> str:
        return self.message
