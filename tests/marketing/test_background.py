from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, NoReturn

import pytest
from PIL import Image

from ads_booster.contracts.generation import (
    MarketingContextBundle,
    PersonaProfile,
    PromotionMaterial,
)
from ads_booster.contracts.models import DeviceKind, DeviceTarget
from ads_booster.marketing.background import HostedBackgroundPreparer
from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.search.image.background import ImageSearchBackgroundFetcher
from ads_booster.search.image.contracts import ImageSearchResponse, ImageSearchResult
from ads_booster.transport.http import HttpResponse

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject

_POST_FORBIDDEN = "hosted background preparation must not post data"
_EXPECTED_MAX_RESULTS = 25


@dataclass(frozen=True, slots=True)
class _BackgroundSearchFixture:
    response: ImageSearchResponse

    def search(self, query: str, max_results: int) -> ImageSearchResponse:
        # The intent reaches the provider as written: the fetcher no longer appends a
        # "site:" operator, which restricted nothing and distorted the query.
        assert "site:" not in query
        assert max_results == _EXPECTED_MAX_RESULTS
        return self.response


@dataclass(frozen=True, slots=True)
class _BackgroundHttpFixture:
    content: bytes

    def get(self, url: str, headers: Mapping[str, str]) -> HttpResponse:
        assert url == "https://images.example/hosted-background.jpg"
        assert "image" in headers["Accept"]
        return HttpResponse(200, self.content, {})

    def post_json(
        self,
        url: str,
        payload: JsonObject,
        headers: Mapping[str, str],
    ) -> NoReturn:
        del url, payload, headers
        raise AssertionError(_POST_FORBIDDEN)

    def post_form(
        self,
        url: str,
        form: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> NoReturn:
        del url, form, headers
        raise AssertionError(_POST_FORBIDDEN)


def _background_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (720, 1280), (24, 48, 96)).save(buffer, format="PNG")
    return buffer.getvalue()


def _background_preparer() -> HostedBackgroundPreparer:
    image_url = "https://images.example/hosted-background.jpg"
    return HostedBackgroundPreparer(
        fetcher=ImageSearchBackgroundFetcher(
            image_search=_BackgroundSearchFixture(
                ImageSearchResponse(
                    provider="fixture-search",
                    query="early morning campus site:pexels.com",
                    results=(
                        ImageSearchResult(
                            title="campus",
                            image_url=image_url,
                            thumbnail_url=image_url,
                            source_url="https://www.pexels.com/photo/campus",
                        ),
                    ),
                )
            ),
            http=_BackgroundHttpFixture(_background_png()),
        )
    )


def _background_bundle(intent: str | None) -> MarketingContextBundle:
    return MarketingContextBundle(
        schema_version="trace.marketing-context.v1",
        request_id="hosted-background-request",
        persona=PersonaProfile(
            persona_id="kr-student",
            country="KR",
            locale="ko-KR",
        ),
        promotion_material=PromotionMaterial(
            promotion_material_id="hosted-background",
            concept="study",
            background_intent=intent,
        ),
        reference_date=datetime.now(UTC),
        device=DeviceTarget(
            kind=DeviceKind.SIMULATOR,
            udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
            platform_version="26.5",
            device_name="iPhone 17 Pro",
        ),
    )


def test_hosted_background_preparer_when_intent_is_trimmed_then_provenance_is_digest_bound(
    tmp_path: Path,
) -> None:
    # Given a hosted bundle and the retained allowlisted image-search fetcher
    preparer = _background_preparer()

    # When the worker prepares the bundle before execution admission
    prepared = preparer.prepare(_background_bundle("  early morning campus  "), tmp_path)

    # Then its normalized PNG and reloaded provenance agree on the same digest
    assert prepared.path == "inputs/background.png"
    assert prepared.sha256 == sha256((tmp_path / prepared.path).read_bytes()).hexdigest()
    assert prepared.provenance.artifact_sha256 == prepared.sha256


def test_hosted_background_preparer_when_intent_is_blank_then_it_fails_before_search(
    tmp_path: Path,
) -> None:
    # Given a bundle without a usable hosted background intent
    preparer = _background_preparer()

    # When the worker attempts pre-admission preparation
    with pytest.raises(MarketingExecutionError, match="hosted_background_intent_missing"):
        _ = preparer.prepare(_background_bundle("  "), tmp_path)

    # Then no background artifact is admitted
    assert not (tmp_path / "inputs" / "background.png").exists()
