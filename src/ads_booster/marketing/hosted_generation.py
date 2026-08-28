"""Running the hosted control plane's caption generation on this Mac.

The hosted surface used to write its own captions with Workers AI and a prompt of its own.
That made two generators for one product: the local one reads the reference corpus, assigns
a domain and a caption form per candidate and samples reference bodies, and the hosted one
read none of it. The captions differed accordingly, and only one of them was the one the
team had been tuning.

So the hosted surface now publishes a job instead, exactly the way it publishes an image
capture, and this executor is what a Mac worker runs when it leases one. The generation
itself is `candidate_generation`'s — untouched — and the drafts go back over the same
callback the capture path uses. The rows are written in D1 by the Worker that published
the job, which is why nothing here has a store.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from pydantic import ValidationError

from ads_booster.candidate_generation.errors import (
    CandidateAuthRequiredError,
    CandidateContextMissingError,
    CandidateGenerationError,
    CandidateReferencesMissingError,
)
from ads_booster.candidate_generation.script_generator import assign_domains
from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind, TaskResult, TaskStatus
from ads_booster.workspace import (
    CandidateAccountBrief,
    CandidatePersonaDomain,
    MarketingAccountIdentity,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ads_booster.candidate_generation.draft_engine import (
        CandidateDraftBatch,
        GeneratedCandidate,
    )
    from ads_booster.candidate_generation.ports import CandidateDraftPort
    from ads_booster.marketing.bridge import TaskExecutor
    from ads_booster.transport.json_types import JsonObject, JsonValue

PIPELINE: Final = "hosted_workspace_generation_v1"
_MIN_COUNT: Final = 1
_MAX_COUNT: Final = 8
_DEFAULT_COUNT: Final = 4
_COUNTRY = re.compile(r"^[A-Z]{2}$")
# The country the instruction template names. The corpus follows the persona's country, but
# the drafts are still held to this one, exactly as the local generator holds them, because
# the template says "KR 게시물 후보" in so many words.
_DRAFT_COUNTRY: Final = "KR"


def _no_barrier(task_id: str) -> None:
    del task_id


@dataclass(frozen=True, slots=True)
class HostedWorkspaceGenerationExecutor:
    """Turns one hosted generation job into drafts, without storing any of them."""

    engine: CandidateDraftPort
    # Crossed once, just before the provider calls go out. The control plane will not accept
    # a callback for a task that never crossed it, and it stops re-leasing the task once it
    # has: a batch that spent four provider calls must not be handed to a second Mac.
    before_execution: Callable[[str], None] = _no_barrier

    def execute(self, task: MarketingTask) -> TaskResult:
        if task.payload.get("pipeline") != PIPELINE:
            raise MarketingExecutionError("unsupported_hosted_generation_pipeline")
        request = _GenerationRequest.of(task)
        self.before_execution(task.task_id)
        batch = self._draft(request)
        return TaskResult(
            status=TaskStatus.SUCCEEDED,
            output={
                "pipeline": PIPELINE,
                "persona_id": request.persona_id,
                "requested": request.count,
                "failures": batch.failures,
                "candidates": [_candidate(generated) for generated in batch.drafts],
            },
        )

    def _draft(self, request: _GenerationRequest) -> CandidateDraftBatch:
        try:
            return self.engine.draft(
                corpus_country=request.country,
                draft_country=_DRAFT_COUNTRY,
                domains=request.domains,
                brief=request.brief,
            )
        except CandidateAuthRequiredError as error:
            # The operator's fix is on this Mac, not in the browser, so it gets its own code
            # rather than being folded into "generation failed".
            raise MarketingExecutionError("hosted_generation_ai_login_required") from error
        except (CandidateContextMissingError, CandidateReferencesMissingError) as error:
            raise MarketingExecutionError("hosted_generation_context_missing") from error
        except CandidateGenerationError as error:
            raise MarketingExecutionError("hosted_generation_failed") from error


@dataclass(frozen=True, slots=True)
class HostedGenerationRoutingExecutor:
    """Sends generation jobs to the engine and leaves every other job where it was."""

    generation: HostedWorkspaceGenerationExecutor
    fallback: TaskExecutor

    def execute(self, task: MarketingTask) -> TaskResult:
        if task.kind is TaskKind.GENERATE_CANDIDATES and task.payload.get("pipeline") == PIPELINE:
            return self.generation.execute(task)
        return self.fallback.execute(task)


@dataclass(frozen=True, slots=True)
class _GenerationRequest:
    """One hosted job, read out of the payload and validated before any call goes out."""

    persona_id: str | None
    country: str
    count: int
    brief: CandidateAccountBrief | None
    domains: tuple[CandidatePersonaDomain, ...]

    @classmethod
    def of(cls, task: MarketingTask) -> _GenerationRequest:
        country = _required_text(task.payload, "country", 2).upper()
        if not _COUNTRY.fullmatch(country):
            raise MarketingExecutionError("hosted_generation_country_invalid")
        count = _count(task.payload.get("count"))
        brief = _brief(task.payload.get("persona"))
        persona_id = task.payload.get("persona_id")
        return cls(
            persona_id=persona_id if isinstance(persona_id, str) and persona_id else None,
            country=country,
            count=count,
            brief=brief,
            # With a persona the batch is that one person writing about different things, so
            # one domain covers all of it. Without one there is nobody to ask and no local
            # coverage counts to read — the hosted candidate rows do not carry a domain — so
            # the assignment falls back to the cold-workspace behaviour and picks at random.
            domains=(brief.domain,) * count if brief is not None else assign_domains({}, count),
        )


def _brief(value: JsonValue) -> CandidateAccountBrief | None:
    """Rebuild the generation brief from the persona identity the control plane stored.

    The hosted persona's `identity_json` is the local `MarketingAccountIdentity` shape on
    purpose, so it is validated by that model rather than by a second reader that could
    drift from it.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise MarketingExecutionError("hosted_generation_persona_invalid")
    try:
        identity = MarketingAccountIdentity.model_validate(value)
    except ValidationError as error:
        raise MarketingExecutionError("hosted_generation_persona_invalid") from error
    return CandidateAccountBrief(
        display_name=identity.display_name,
        age=identity.age,
        region=identity.region,
        occupation=identity.occupation,
        concept=identity.concept,
        domain=identity.domain,
        interests=identity.interests,
        life_rhythm=identity.life_rhythm,
        background_subject=identity.taste.background_subject,
        background_mood=identity.taste.background_mood,
    )


def _candidate(generated: GeneratedCandidate) -> JsonObject:
    """One draft in the shape the hosted candidate table stores.

    The provenance travels whole rather than flattened: the hosted surface renders the same
    "생성 근거" panel the local one does, and it reads these exact keys.
    """
    draft = generated.draft
    provenance = generated.provenance
    return {
        "topic": draft.topic,
        "country": draft.country,
        "posting_slot": draft.posting_slot.value,
        "persona_domain": None if draft.persona_domain is None else draft.persona_domain.value,
        "caption": draft.caption,
        "hypothesis": draft.hypothesis,
        "refs_used": list(draft.refs_used),
        "principles_applied": list(draft.principles_applied),
        "appium_prompt": draft.appium_prompt,
        "image_inputs": draft.image_inputs.model_dump(mode="json"),
        "provenance": {
            **provenance.model_dump(mode="json"),
            "caption_form": generated.caption_form.value,
        },
    }


def _count(value: JsonValue) -> int:
    if value is None:
        return _DEFAULT_COUNT
    if isinstance(value, bool) or not isinstance(value, int):
        raise MarketingExecutionError("hosted_generation_count_invalid")
    if not _MIN_COUNT <= value <= _MAX_COUNT:
        raise MarketingExecutionError("hosted_generation_count_invalid")
    return value


def _required_text(payload: JsonObject, key: str, max_length: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise MarketingExecutionError(f"hosted_generation_{key}_invalid")
    return value.strip()
