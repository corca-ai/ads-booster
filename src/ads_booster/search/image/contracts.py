from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol, override
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ImageSearchResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    title: str = Field(min_length=1, max_length=500)
    image_url: str = Field(min_length=1, max_length=4_096)
    thumbnail_url: str = Field(min_length=1, max_length=4_096)
    source_url: str = Field(min_length=1, max_length=4_096)
    width: int | None = Field(default=None, ge=1, le=100_000)
    height: int | None = Field(default=None, ge=1, le=100_000)
    source: str = Field(default="", max_length=200)

    @field_validator("image_url", "thumbnail_url", "source_url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            message = "image search URLs must use http or https"
            raise ValueError(message)
        return value


class ImageSearchResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    provider: str = Field(min_length=1, max_length=64)
    query: str = Field(min_length=1, max_length=1_000)
    results: tuple[ImageSearchResult, ...] = Field(max_length=10)


class ImageSearchProvider(Protocol):
    def search(self, query: str, max_results: int) -> ImageSearchResponse: ...


@dataclass(frozen=True, slots=True)
class ImageSearchError(Exception):
    code: str
    message: str

    @override
    def __str__(self) -> str:
        return self.message
