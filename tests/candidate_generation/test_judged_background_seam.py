from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ads_booster.candidate_generation.background_factory import persona_from_bundle
from ads_booster.candidate_generation.background_judge import BackgroundJudge, JudgePersona
from ads_booster.candidate_generation.background_selection import (
    JudgedBackgroundFetcher,
    JudgedBackgroundSelector,
)
from ads_booster.contracts.generation import (
    MarketingContextBundle,
    PersonaProfile,
    PromotionMaterial,
)
from ads_booster.contracts.models import DeviceKind, DeviceTarget
from ads_booster.providers.codex import ModelTurn
from ads_booster.runtime.generate_one import BackgroundFetcher  # noqa: TC001 — asserted at runtime
from ads_booster.search.image.open_background import CollectedBackground, CollectedBackgrounds

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ads_booster.contracts.tools import ToolDescriptor
    from ads_booster.transport.json_types import JsonObject

_PERSONA = JudgePersona(
    topic="시험기간 일정 관리",
    subject="scenery",
    mood="늦은 밤 책상 위 스탠드 불빛",
    query="",
)


@dataclass(slots=True)
class _FakeClient:
    answers: list[str]
    histories: list[tuple[JsonObject, ...]] = field(default_factory=list)

    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn:
        del tools
        self.histories.append(history)
        return ModelTurn(self.answers.pop(0), ())


@dataclass(slots=True)
class _FakeFetcher:
    rounds: list[CollectedBackgrounds]
    queries: list[str] = field(default_factory=list)

    def collect(self, query: str, limit: int = 6) -> CollectedBackgrounds:
        del limit
        self.queries.append(query)
        return self.rounds.pop(0)


def _image(image_id: str) -> CollectedBackground:
    return CollectedBackground(
        image_id=image_id,
        content=f"{image_id}-full".encode(),
        preview=f"{image_id}-preview".encode(),
        image_url=f"https://cdn.example/{image_id}.jpg",
        source_url=f"https://blog.example/{image_id}",
    )


def _collected(*image_ids: str) -> CollectedBackgrounds:
    images = tuple(_image(image_id) for image_id in image_ids)
    return CollectedBackgrounds(
        query="제주 바다 노을 배경화면",
        provider="ddgs",
        images=images,
        results_seen=len(images),
        passed_filters=len(images),
    )


def _graded(image_id: str, authenticity: str, persona_fit: str, background_fit: str) -> JsonObject:
    return {
        "id": image_id,
        "gated": False,
        "grades": {
            "authenticity": authenticity,
            "persona_fit": persona_fit,
            "background_fit": background_fit,
        },
        "note": f"{image_id} 근거",
    }


def _identity(images: Sequence[CollectedBackground]) -> Sequence[CollectedBackground]:
    return tuple(images)


def _fetcher(client: _FakeClient, fetcher: _FakeFetcher) -> JudgedBackgroundFetcher:
    return JudgedBackgroundFetcher(
        selector=JudgedBackgroundSelector(
            fetcher=fetcher,  # pyright: ignore[reportArgumentType]
            judge=BackgroundJudge(client=client, shuffle=_identity),
            model="gpt-5.5",
        ),
        persona=_PERSONA,
    )


def _bundle(background_intent: str | None) -> MarketingContextBundle:
    return MarketingContextBundle(
        schema_version="trace.marketing-context.v1",
        request_id="candidate-1",
        persona=PersonaProfile(persona_id="candidate-1", country="KR", locale="ko-KR"),
        promotion_material=PromotionMaterial(
            promotion_material_id="candidate-1",
            concept="시험기간 일정 관리",
            background_intent=background_intent,
        ),
        reference_date=datetime(2026, 8, 27, 7, 20, tzinfo=UTC),
        device=DeviceTarget(
            kind=DeviceKind.SIMULATOR,
            udid="00000000-0000-4000-8000-000000000000",
            platform_version="26.5",
            device_name="iPhone 17 Pro",
        ),
    )


def test_the_seam_receives_the_judged_winner_and_its_provenance(tmp_path: Path) -> None:
    # Given a pool whose second image grades highest
    client = _FakeClient(
        [
            json.dumps(
                [
                    _graded("img-a", "중", "중", "중"),
                    _graded("img-b", "상", "상", "상"),
                ],
                ensure_ascii=False,
            )
        ]
    )
    destination = tmp_path / "inputs" / "background.png"
    judged: BackgroundFetcher = _fetcher(client, _FakeFetcher([_collected("img-a", "img-b")]))

    # When the Trace runner asks for a background by query, as it always has
    background = judged.fetch("제주 바다 노을 배경화면", destination)
    background.write_provenance(tmp_path / "inputs" / "background-source.json")

    # Then the artifact on disk is the image the judge chose
    assert destination.read_bytes() == b"img-b-full"
    assert background.source_url == "https://blog.example/img-b"

    # And the provenance file keeps the stock fetcher's keys and adds the judgment
    payload = json.loads((tmp_path / "inputs" / "background-source.json").read_text("utf-8"))
    assert payload["schema_version"] == "trace.background-search.v1"
    assert payload["artifact_sha256"] == background.sha256
    assert payload["selection"] == "ai_judged"
    assert payload["chosen_image_id"] == "img-b"
    assert payload["reviewed_images"] == 2
    assert payload["gated_images"] == 0
    assert payload["queries_tried"] == [
        {"query": "제주 바다 노을 배경화면", "source": "original", "results": 2}
    ]


def test_the_query_the_runner_asks_for_is_the_query_that_is_searched(tmp_path: Path) -> None:
    # Given a scene plan query that differs from the persona's own phrasing
    collection = _FakeFetcher([_collected("img-a")])
    client = _FakeClient([json.dumps([_graded("img-a", "상", "상", "상")], ensure_ascii=False)])

    # When the runner asks for it
    _ = _fetcher(client, collection).fetch("김도영 직캠", tmp_path / "background.png")

    # Then that is what the open-web search ran, not the persona's stored query
    assert collection.queries == ["김도영 직캠"]


def test_the_persona_is_recovered_from_the_bundle_background_intent() -> None:
    # Given the "subject: mood" intent the candidate image inputs compose
    persona = persona_from_bundle(_bundle("scenery: 늦은 밤 책상 위 스탠드 불빛"))

    # Then both halves reach the judge
    assert persona.topic == "시험기간 일정 관리"
    assert persona.subject == "scenery"
    assert persona.mood == "늦은 밤 책상 위 스탠드 불빛"


def test_a_free_text_intent_stands_as_the_mood_with_no_invented_subject() -> None:
    # Given an intent written by a caller that never had the vocabulary
    persona = persona_from_bundle(_bundle("한적한 해변의 이른 아침"))

    # Then nothing is invented for the subject
    assert persona.subject == ""
    assert persona.mood == "한적한 해변의 이른 아침"
