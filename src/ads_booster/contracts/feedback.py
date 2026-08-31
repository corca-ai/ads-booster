"""Versioned feedback input carried from the hosted control plane to a Mac worker."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


class FeedbackModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class FeedbackScope(FeedbackModel):
    account_id: Annotated[str, Field(min_length=1, max_length=128)]
    context_profile_id: Annotated[str | None, Field(max_length=128)] = None


class FeedbackRule(FeedbackModel):
    rule_id: Annotated[str, Field(min_length=1, max_length=128)]
    definition_version: Literal["1"]
    dimension: Annotated[str, Field(min_length=1, max_length=40)]
    instruction: Annotated[str, Field(min_length=1, max_length=1000)]
    stage: Literal["caption", "image"]
    tag: Annotated[str, Field(min_length=1, max_length=40)]
    evidence_count: Annotated[int, Field(ge=3)]
    targets: Annotated[tuple[str, ...], Field(min_length=1, max_length=4)]


class ImmediateCorrection(FeedbackModel):
    source_event_id: Annotated[str, Field(min_length=1, max_length=128)]
    source_candidate_id: Annotated[str, Field(min_length=1, max_length=128)]
    source_candidate_revision: Annotated[int, Field(ge=1)]
    source_capture_task_id: Annotated[str, Field(min_length=1, max_length=128)]
    source_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    rating: Annotated[int, Field(ge=1, le=3)]
    tags: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]
    note: Annotated[str | None, Field(max_length=2000)] = None


class FeedbackContext(FeedbackModel):
    schema_version: Literal["trace.feedback-context.v1"]
    stage: Literal["caption", "image"]
    scope: FeedbackScope
    rules: Annotated[tuple[FeedbackRule, ...], Field(max_length=32)] = ()
    immediate_correction: ImmediateCorrection | None = None


def feedback_context_sha256(context: FeedbackContext) -> str:
    """Digest the cross-language envelope using the control plane's canonical JSON form."""
    encoded = json.dumps(
        context.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256(encoded).hexdigest()
