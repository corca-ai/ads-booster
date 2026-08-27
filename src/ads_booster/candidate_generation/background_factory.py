"""Composition of the judged open-web background fetcher.

Candidate backgrounds are searched across the open web rather than the stock-photo
allowlist, because a persona's real lock screen holds specific people, characters, and
teams that the allowlist can never return. Search rank alone cannot tell a promo still from
a photo someone would actually keep, so the same provider that writes candidates also looks
at every collected image and picks one. Rights review happens at publish time from the
provenance both steps record; this module only supplies the transport and the persona.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from ads_booster.auth.codex import CodexOAuth
from ads_booster.auth.store import AuthStore
from ads_booster.candidate_generation.background_judge import BackgroundJudge, JudgePersona
from ads_booster.candidate_generation.background_selection import (
    JudgedBackgroundFetcher,
    JudgedBackgroundSelector,
)
from ads_booster.providers.codex import CodexResponsesClient
from ads_booster.search.image.open_background import OpenWebBackgroundFetcher
from ads_booster.search.image.providers import create_image_search_provider
from ads_booster.transport.http import create_http_client

if TYPE_CHECKING:
    from collections.abc import Generator

    from ads_booster.config.settings import AgentSettings
    from ads_booster.contracts.generation import MarketingContextBundle
    from ads_booster.transport.http import HttpClient
    from ads_booster.workspace import CandidateRecord

SEARCH_PROVIDER_ENVIRONMENT: Final = "TRACE_AGENT_WEB_SEARCH_PROVIDER"
SEARCH_TIMEOUT_ENVIRONMENT: Final = "TRACE_AGENT_WEB_SEARCH_TIMEOUT_SECONDS"
BACKGROUND_JUDGE_INSTRUCTION: Final = (
    "당신은 잠금화면 배경 사진을 고르는 심사위원입니다. 첨부된 이미지를 직접 보고 판단하고, "
    "요청받은 JSON만 출력합니다. 설명, 코드펜스, 사족을 붙이지 않습니다."
)
_INTENT_SEPARATOR: Final = ": "


def build_judged_selector(http: HttpClient, settings: AgentSettings) -> JudgedBackgroundSelector:
    """Compose the collect-judge-decide selector over one open HTTP client."""
    judge_client = CodexResponsesClient(
        http=http,
        oauth=CodexOAuth(http=http, store=AuthStore.default()),
        model=settings.model,
        reasoning_effort=settings.reasoning_effort,
    )
    judge_client.instructions = BACKGROUND_JUDGE_INSTRUCTION
    return JudgedBackgroundSelector(
        fetcher=OpenWebBackgroundFetcher(
            image_search=create_image_search_provider(
                http=http,
                provider_name=os.environ.get(SEARCH_PROVIDER_ENVIRONMENT, "auto"),
                timeout_seconds=float(os.environ.get(SEARCH_TIMEOUT_ENVIRONMENT, "30")),
            ),
            http=http,
        ),
        judge=BackgroundJudge(client=judge_client),
        model=settings.model,
    )


@dataclass(frozen=True, slots=True)
class JudgedBackgroundFetcherFactory:
    """Builds one judged fetcher per marketing bundle, told who it is choosing for."""

    http: HttpClient
    settings: AgentSettings

    def __call__(self, bundle: MarketingContextBundle) -> JudgedBackgroundFetcher:
        return JudgedBackgroundFetcher(
            selector=build_judged_selector(self.http, self.settings),
            persona=persona_from_bundle(bundle),
        )


def persona_from_bundle(bundle: MarketingContextBundle) -> JudgePersona:
    """Describe the bundle to the judge so "fits this persona" has something to mean.

    `background_intent` is written as "subject: mood" by the candidate image inputs, so the
    two halves are recovered here when they are there and the whole string stands as the
    mood when they are not. The query is filled in by the fetcher from whatever the scene
    plan actually asks for.
    """
    material = bundle.promotion_material
    intent = material.background_intent or ""
    subject, separator, mood = intent.partition(_INTENT_SEPARATOR)
    return JudgePersona(
        topic=material.concept,
        subject=subject if separator else "",
        mood=mood if separator else intent,
        query="",
    )


def persona_from_candidate(record: CandidateRecord, query: str) -> JudgePersona:
    """Describe one stored candidate to the judge, from the fields its generator wrote."""
    inputs = record.image_inputs
    if inputs is None:
        return JudgePersona(topic=record.topic, subject="", mood="", query=query)
    return JudgePersona(
        topic=record.topic,
        subject=inputs.background_subject.value,
        mood=inputs.background_mood,
        query=query,
    )


@dataclass(frozen=True, slots=True)
class ProductionCandidateBackgrounds:
    """Opens one judged selector per candidate image run, for the local fallback path."""

    settings: AgentSettings

    @contextmanager
    def open(self) -> Generator[JudgedBackgroundSelector]:
        with create_http_client(read_timeout=self.settings.candidate_timeout_seconds) as http:
            yield build_judged_selector(http, self.settings)
