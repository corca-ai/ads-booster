from __future__ import annotations

import io
import json
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, override
from urllib.parse import unquote, urlsplit

import httpx2
from PIL import Image, UnidentifiedImageError

from ads_booster.search.image.contracts import (
    BackgroundBrief,
    BackgroundJudge,
    ImageSearchError,
    ImageSearchProvider,
    ImageSearchResponse,
    ImageSearchResult,
    JudgeCandidate,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from ads_booster.transport.http import HttpClient
    from ads_booster.transport.json_types import JsonValue

_HTTP_OK: Final = 200
# The general floor, which exists to drop the small landscape article photography that open
# web image search returns by default.
_MINIMUM_EDGE: Final = 640
# A real phone wallpaper is authored at phone width, so it is often narrower than the general
# floor while being exactly the right shape. Measured against live results, the images the
# flat 640 floor was discarding included 474x1026, 555x1200 and 576x1280 - all within a
# hundredth of the lock screen ratio. Anything already shaped like the target screen is
# judged on the screen's terms instead.
_PHONE_MINIMUM_SHORT_EDGE: Final = 450
_PHONE_MINIMUM_LONG_EDGE: Final = 950
_PHONE_ASPECT_TOLERANCE: Final = 0.08
_PREFERRED_WIDTH: Final = 1080
_PREFERRED_HEIGHT: Final = 1920
_LOCK_SCREEN_ASPECT_RATIO: Final = 9 / 19.5
_WRITE_FAILED_CODE: Final = "background_artifact_write_failed"
_WRITE_FAILED_MESSAGE: Final = "searched background could not be written"
_NO_USABLE_IMAGE_CODE: Final = "background_search_no_usable_image"
_NO_USABLE_IMAGE_MESSAGE: Final = "image search returned no usable approved background image"
_INVALID_IMAGE_CODE: Final = "background_search_invalid_image"
_INVALID_IMAGE_MESSAGE: Final = "image search returned an unreadable background image"
_JUDGE_REJECTED_CODE: Final = "background_search_judge_rejected_all"
_JUDGE_REJECTED_MESSAGE: Final = "no searched background belonged on this persona's screen"
_JUDGE_FAILED_CODE: Final = "background_search_judge_unavailable"
_JUDGE_FAILED_MESSAGE: Final = "the background judge could not be reached"
_IMAGE_TOO_SMALL_CODE: Final = "background_search_image_too_small"
_IMAGE_TOO_SMALL_MESSAGE: Final = (
    "image search returned a background image below the minimum resolution"
)
# A read-only mapping rather than a dict, so one shared empty default cannot be mutated
# by a holder and cannot be rejected as a mutable dataclass default.
_NO_DETAILS: Final[Mapping[str, JsonValue]] = MappingProxyType({})
# Only hosts that serve something unusable rather than merely unfamiliar. There is no source
# allowlist: restricting sources to the free stock sites was measured against live searches
# and discarded 65% of all candidates, leaving seven of ten queries with no background at
# all. It also selected against the target - stock hero photography is landscape desktop
# material, while the portrait wallpapers a lock screen needs live on the open web.
_BLOCKED_IMAGE_HOSTS: Final = frozenset({"plus.unsplash.com"})
# Sources that serve something nobody sets as a wallpaper. Each entry was measured against
# the 211 distinct candidates a live run produced, and only the groups that rejected
# wallpapers at a rate of zero are here: a wrong reject costs a usable background, while a
# wrong accept only costs the ranking one row.
#
# Stock farms serve watermarked previews - every hit carried a visible CrowdPic, LovePik or
# Shutterstock stamp across the image.
_WATERMARKED_STOCK_HOSTS: Final = (
    "lovepik", "shutterstock", "freepik", "123rf", "dreamstime",
    "istockphoto", "gettyimages", "crowdpic", "vecteezy",
)
# Press photography: a real event, usually a real private person, and often a masthead
# burned into the corner.
_NEWS_HOSTS: Final = (
    "news", "chosun", "donga", "joins", "joongang", "hani", "khan", "yna.co.kr",
    "mk.co.kr", "hankyung", "sedaily", "topstarnews", "osen", "spotv", "mbn",
    "sbs.co.kr", "kbs.co.kr", "imbc", "ytn", "nate.com", "newsis", "edaily",
)
_COMMERCE_HOSTS: Final = (
    "ohou.se", "smartstore", "coupang", "11st", "gmarket", "auction.co.kr",
    "wemakeprice", "tmon",
)
# Video thumbnails carry play affordances and burnt-in titles.
_VIDEO_HOSTS: Final = ("youtube", "youtu.be", "douyin", "tiktok", "vimeo")
# A listing rather than one photo: the image behind it answers the site's own search, not
# ours. This is how a Vietnamese "remove limit" banner became a KIA Tigers background.
_LISTING_PATHS: Final = ("/search", "/s/photos", "/ideas/", "/images/search", "/tag/", "/tags/")
# Titles that name the desktop the image was cut for. Measured, these are the 1920x1080
# cohort that "고화질" style queries pull in, and none of them is shaped for a phone.
_DESKTOP_TITLE_TOKENS: Final = (
    "1920x1080", "2560x1440", "1366x768", "바탕 화면", "바탕화면", "듀얼", "모니터",
)
# A brand asset, not a photograph. Kept to these two words: the wider list this started as
# also matched "무료 다운로드" and "스톡", which ordinary Korean wallpaper sites put in
# their titles, and it threw away cherry blossom and sunset wallpapers to catch one logo.
_ASSET_TITLE_TOKENS: Final = ("로고", "엠블럼")
_REJECTED_SOURCE_HOSTS: Final = (
    *_WATERMARKED_STOCK_HOSTS,
    *_NEWS_HOSTS,
    *_COMMERCE_HOSTS,
    *_VIDEO_HOSTS,
)
_SEARCH_HEADERS: Final = {
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "User-Agent": "trace-agent/0.2.1",
}


@dataclass(frozen=True, slots=True)
class BackgroundSearchError(Exception):
    code: str
    message: str

    @override
    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class SearchedBackground:
    path: Path
    sha256: str
    query: str
    provider: str
    image_url: str
    source_url: str
    # Extra facts the fetcher that produced this background wants on the artifact record,
    # merged into the provenance file under their own keys. The stock-allowlist fetcher has
    # none; the judged open-web fetcher records why this image beat the ones it was shown.
    details: Mapping[str, JsonValue] = _NO_DETAILS

    def write_provenance(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, JsonValue] = {
            "schema_version": "trace.background-search.v1",
            "query": self.query,
            "provider": self.provider,
            "image_url": self.image_url,
            "source_url": self.source_url,
            "artifact_sha256": self.sha256,
            **self.details,
        }
        _ = destination.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )


@dataclass(frozen=True, slots=True)
class _BackgroundCandidate:
    response: ImageSearchResponse
    result: ImageSearchResult
    png: bytes
    width: int
    height: int

    @property
    def score(self) -> tuple[bool, bool, float, int]:
        return (
            self.height > self.width,
            self.width >= _PREFERRED_WIDTH and self.height >= _PREFERRED_HEIGHT,
            -abs((self.width / self.height) - _LOCK_SCREEN_ASPECT_RATIO),
            self.width * self.height,
        )


@dataclass(frozen=True, slots=True)
class ImageSearchBackgroundFetcher:
    image_search: ImageSearchProvider
    http: HttpClient
    # The ranking can only be as good as the pool it sees. With one search rather than three,
    # a wider page is what keeps the number of candidates reaching the ranking up.
    max_results: int = 25
    # Optional so the fetcher keeps working where no judge is wired. Without one the choice
    # is geometry alone, and geometry cannot see what an image is: measured on a live pool,
    # it picked a KIA championship poster over eighteen alternatives and a cricket
    # photograph for a query about a plain minimal wallpaper, because posters and cropped
    # press photography are cut to exactly the phone's proportions.
    judge: BackgroundJudge | None = None

    def fetch(
        self,
        query: str,
        destination: Path,
        brief: BackgroundBrief | None = None,
    ) -> SearchedBackground:
        shortlist = tuple(self._candidates(query))
        if not shortlist:
            raise BackgroundSearchError(
                _NO_USABLE_IMAGE_CODE,
                _NO_USABLE_IMAGE_MESSAGE,
            )
        shortlist = self._judged(shortlist, brief or BackgroundBrief(query=query))
        selected = max(shortlist, key=lambda candidate: candidate.score, default=None)
        if selected is None:
            raise BackgroundSearchError(
                _NO_USABLE_IMAGE_CODE,
                _NO_USABLE_IMAGE_MESSAGE,
            )
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            _ = destination.write_bytes(selected.png)
        except OSError as error:
            raise BackgroundSearchError(
                _WRITE_FAILED_CODE,
                _WRITE_FAILED_MESSAGE,
            ) from error
        return SearchedBackground(
            path=destination,
            sha256=sha256(selected.png).hexdigest(),
            query=selected.response.query,
            provider=selected.response.provider,
            image_url=selected.result.image_url,
            source_url=selected.result.source_url,
        )

    def _judged(
        self,
        shortlist: tuple[_BackgroundCandidate, ...],
        brief: BackgroundBrief,
    ) -> tuple[_BackgroundCandidate, ...]:
        """Narrow the shortlist to what the judge says belongs on this person's screen.

        Judging happens after the resolution gate so the call carries the rows that could
        actually be used, and it is one call for the whole shortlist.

        A judge that rejects everything fails the fetch rather than falling back to
        geometry. Falling back would hand the job the very row the judge just refused,
        since the poster it rejects is usually the one geometry likes best. A judge that
        cannot be reached fails too, under its own code so an operator can tell an outage
        from a verdict.
        """
        if self.judge is None:
            return shortlist
        candidates = tuple(
            JudgeCandidate(
                image_url=candidate.result.image_url,
                thumbnail_url=candidate.result.thumbnail_url,
                title=candidate.result.title,
                width=candidate.width,
                height=candidate.height,
            )
            for candidate in shortlist
        )
        try:
            accepted = self.judge.choose(brief, candidates)
        except Exception as error:  # any judge failure is one outcome here
            raise BackgroundSearchError(_JUDGE_FAILED_CODE, _JUDGE_FAILED_MESSAGE) from error
        approved = set(accepted)
        kept = tuple(c for c in shortlist if c.result.image_url in approved)
        if not kept:
            raise BackgroundSearchError(_JUDGE_REJECTED_CODE, _JUDGE_REJECTED_MESSAGE)
        return kept

    def _candidates(self, query: str) -> Iterator[_BackgroundCandidate]:
        # One search on the query as written. The previous shape ran the query three times
        # with a "site:" operator appended, which measured against live results restricted
        # nothing - 27 of 30 such searches returned no result from the requested domain -
        # while the extra tokens distorted the query badly enough to collapse some result
        # pages from ten rows to one.
        try:
            response = self.image_search.search(query, self.max_results)
        except ImageSearchError:
            return
        for result in response.results:
            if not _is_usable_result(result):
                continue
            try:
                http_response = self.http.get(result.image_url, _SEARCH_HEADERS)
            except httpx2.HTTPError:
                continue
            if http_response.status_code != _HTTP_OK:
                continue
            try:
                candidate = _background_candidate(response, result, http_response.content)
            except BackgroundSearchError:
                continue
            yield candidate


def _background_candidate(
    response: ImageSearchResponse,
    result: ImageSearchResult,
    content: bytes,
) -> _BackgroundCandidate:
    try:
        with Image.open(io.BytesIO(content)) as image:
            _ = image.load()
            width, height = image.size
            normalized = image.convert("RGB")
    except (OSError, UnidentifiedImageError, SyntaxError) as error:
        raise BackgroundSearchError(
            _INVALID_IMAGE_CODE,
            _INVALID_IMAGE_MESSAGE,
        ) from error
    if not _is_large_enough(width, height):
        raise BackgroundSearchError(
            _IMAGE_TOO_SMALL_CODE,
            _IMAGE_TOO_SMALL_MESSAGE,
        )
    buffer = io.BytesIO()
    normalized.save(buffer, format="PNG")
    return _BackgroundCandidate(
        response=response,
        result=result,
        png=buffer.getvalue(),
        width=width,
        height=height,
    )


def _is_lock_screen_shaped(width: int, height: int) -> bool:
    return (
        height > width
        and abs((width / height) - _LOCK_SCREEN_ASPECT_RATIO) <= _PHONE_ASPECT_TOLERANCE
    )


def _is_large_enough(width: int, height: int) -> bool:
    """Whether the image carries enough pixels to fill a lock screen.

    An image already shaped like the target screen only has to be big enough for that screen,
    which is a lower bar on the short edge than the general floor: phone wallpapers are
    authored at phone width and would otherwise be rejected for being exactly right.
    """
    if _is_lock_screen_shaped(width, height):
        return width >= _PHONE_MINIMUM_SHORT_EDGE and height >= _PHONE_MINIMUM_LONG_EDGE
    return min(width, height) >= _MINIMUM_EDGE


def _is_usable_result(result: ImageSearchResult) -> bool:
    """Whether the row could be a wallpaper at all, before anything is downloaded.

    This is a reject rather than a preference: the ranking below chooses among wallpapers,
    and handing it a press photo or a watermarked stock preview only lets one win when the
    pool is thin. When every row is rejected the fetch fails, which is the intended
    outcome - a job with no background is a job a person looks at, while a background that
    is somebody's press photo goes out silently.
    """
    if urlsplit(result.image_url).hostname in _BLOCKED_IMAGE_HOSTS:
        return False
    source = urlsplit(result.source_url)
    host = (source.hostname or "").lower()
    if any(token in host for token in _REJECTED_SOURCE_HOSTS):
        return False
    if any(token in unquote(source.path or "").lower() for token in _LISTING_PATHS):
        return False
    lowered = result.title.lower()
    if any(token.lower() in lowered for token in _DESKTOP_TITLE_TOKENS):
        return False
    return not any(token in result.title for token in _ASSET_TITLE_TOKENS)
