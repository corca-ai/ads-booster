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


class BackgroundBrief(BaseModel):
    """What the judge needs to know about the person whose lock screen this is.

    Small on purpose. The judge is deciding two things a picture answers - is this a
    wallpaper at all, and is it this person's - so it gets the query that was searched, the
    vocabulary term the query was meant to satisfy, and enough of the persona to catch a
    background that belongs to somebody else's life.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    query: str = Field(min_length=1, max_length=200)
    subject: str = Field(default="", max_length=40)
    country: str = Field(default="", max_length=8)
    persona: str = Field(default="", max_length=500)


class JudgeCandidate(BaseModel):
    """One row put to the judge.

    `thumbnail_url` rather than the full image: the judge is looking for text burned into
    the picture and for a subject that does not belong to this persona, and a thumbnail
    carries both. Sending originals multiplies the payload for no extra signal.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    image_url: str = Field(min_length=1, max_length=4_096)
    thumbnail_url: str = Field(min_length=1, max_length=4_096)
    title: str = Field(default="", max_length=500)
    width: int = Field(ge=1, le=100_000)
    height: int = Field(ge=1, le=100_000)


class BackgroundJudge(Protocol):
    """Sees the shortlist and says which rows could be this person's wallpaper.

    One call for the whole shortlist rather than one per row: the judge is comparing
    candidates against each other as much as against the brief, and a single call is also
    the difference between one round trip per background and a dozen.

    Returns the accepted `image_url`s, best first. An empty result is a verdict, not an
    error - it means nothing in the shortlist belongs on this person's phone.
    """

    def choose(
        self,
        brief: BackgroundBrief,
        candidates: tuple[JudgeCandidate, ...],
    ) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class ImageSearchError(Exception):
    code: str
    message: str

    @override
    def __str__(self) -> str:
        return self.message
