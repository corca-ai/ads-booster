from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Final, Protocol, override
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

# One ceiling for the whole image-search path. The response contract and every provider read
# it from here: when they disagreed, asking a provider for more rows than the response could
# hold raised a bare ValidationError that no caller was catching.
MAX_IMAGE_SEARCH_RESULTS: Final = 50


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
    results: tuple[ImageSearchResult, ...] = Field(max_length=MAX_IMAGE_SEARCH_RESULTS)


class ImageSearchProvider(Protocol):
    def search(self, query: str, max_results: int) -> ImageSearchResponse: ...


@dataclass(frozen=True, slots=True)
class ImageSearchError(Exception):
    code: str
    message: str

    @override
    def __str__(self) -> str:
        return self.message
