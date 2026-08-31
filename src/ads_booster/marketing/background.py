from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ads_booster.contracts.generation import MarketingContextBundle
from ads_booster.contracts.native_export import PreparedBackground, TraceBackgroundSearchProvenance
from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.search.image.background import BackgroundSearchError, ImageSearchBackgroundFetcher
from ads_booster.search.image.contracts import BackgroundBrief

if TYPE_CHECKING:
    from pathlib import Path

_BACKGROUND_PATH = "inputs/background.png"
_BACKGROUND_PROVENANCE_PATH = "inputs/background-source.json"


@dataclass(frozen=True, slots=True)
class HostedBackgroundPreparer:
    fetcher: ImageSearchBackgroundFetcher

    def prepare(
        self,
        bundle: MarketingContextBundle,
        job_root: Path,
    ) -> PreparedBackground:
        intent = bundle.promotion_material.background_intent
        if intent is None or not intent.strip():
            raise MarketingExecutionError("hosted_background_intent_missing")
        query = intent.strip()
        background_path = job_root / _BACKGROUND_PATH
        provenance_path = job_root / _BACKGROUND_PROVENANCE_PATH
        try:
            background = self.fetcher.fetch(query, background_path, _brief(bundle, query))
        except BackgroundSearchError as error:
            raise MarketingExecutionError(error.code) from error
        if background.path != background_path or not background_path.is_file():
            raise MarketingExecutionError("hosted_background_artifact_missing")
        try:
            digest = sha256(background_path.read_bytes()).hexdigest()
        except OSError as error:
            raise MarketingExecutionError("hosted_background_artifact_missing") from error
        if background.sha256 != digest:
            raise MarketingExecutionError("hosted_background_digest_mismatch")
        provenance = TraceBackgroundSearchProvenance(
            schema_version="trace.background-search.v1",
            artifact_path=_BACKGROUND_PATH,
            artifact_sha256=digest,
            query=background.query,
            provider=background.provider,
            image_url=background.image_url,
            source_url=background.source_url,
        )
        try:
            _ = provenance_path.write_text(provenance.model_dump_json(), encoding="utf-8")
            loaded = TraceBackgroundSearchProvenance.model_validate_json(
                provenance_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as error:
            raise MarketingExecutionError("hosted_background_provenance_invalid") from error
        return PreparedBackground(
            path=_BACKGROUND_PATH,
            sha256=digest,
            provenance=loaded,
        )


def _brief(bundle: MarketingContextBundle, query: str) -> BackgroundBrief:
    """Describe whose lock screen this is, for the judge downstream.

    Only what a picture can be checked against: the country, so a background does not put
    somebody else's street in a Korean persona's phone, and what this person is into, so a
    row belonging to a different life is visible as such. The subject vocabulary term would
    belong here too, but it never reaches this contract - the worker composes the intent
    from it and sends only that.
    """
    persona = bundle.persona
    about = [persona.occupation or "", *persona.interests, *persona.traits]
    return BackgroundBrief(
        query=query,
        country=persona.country,
        persona=", ".join(part for part in about if part)[:500],
    )
