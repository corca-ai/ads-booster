"""Bounded input and output contracts for the installed Trace post workflow."""
# ruff: noqa: EM101, TC001

from __future__ import annotations

import datetime as dt
from typing import Literal, Self

from pydantic import Field, model_validator

from ads_booster.contracts.creative_work import AssetParent
from ads_booster.contracts.models import ContractModel, Sha256Digest

TracePostMotif = Literal["헬로키티", "미피", "치이카와", "도라에몽"]
TracePostPlace = Literal["카페 나무 테이블", "침대 가장자리", "소파", "창가 책상"]


class TracePostInput(ContractModel):
    schema_version: Literal["trace.trace-post-input.v1"]
    concept: Literal["cute"]
    device_date: str | None = None
    motif: TracePostMotif | None = None
    place: TracePostPlace | None = None

    @model_validator(mode="after")
    def valid_date(self) -> Self:
        if self.device_date is not None:
            try:
                _ = dt.date.fromisoformat(self.device_date)
            except ValueError as error:
                raise ValueError("trace_post_device_date_invalid") from error
        return self


class TracePostAsset(ContractModel):
    country: Literal["kr", "jp", "tw"]
    role: Literal["final", "scene"]
    asset: AssetParent


class TracePostCaption(ContractModel):
    country: Literal["kr", "jp", "tw"]
    text: str = Field(min_length=1, max_length=4000)
    reply_link: str = Field(min_length=1, max_length=1000)
    tutorial: str = Field(min_length=1, max_length=1000)


class TracePostSuccess(ContractModel):
    schema_version: Literal["trace.trace-post-success.v1"]
    assets: tuple[
        TracePostAsset,
        TracePostAsset,
        TracePostAsset,
        TracePostAsset,
        TracePostAsset,
        TracePostAsset,
    ]
    captions: tuple[TracePostCaption, TracePostCaption, TracePostCaption]
    run_summary_sha256: Sha256Digest
    bundle_sha256: Sha256Digest
    recorded_image_call_count: int = Field(ge=7, le=14)
    human_review_required: Literal[True]

    @model_validator(mode="after")
    def complete_country_roles(self) -> Self:
        expected = {
            (country, role) for country in ("kr", "jp", "tw") for role in ("final", "scene")
        }
        if {(item.country, item.role) for item in self.assets} != expected:
            raise ValueError("trace_post_assets_incomplete")
        if {item.country for item in self.captions} != {"kr", "jp", "tw"}:
            raise ValueError("trace_post_captions_incomplete")
        return self


class TracePostFailure(ContractModel):
    schema_version: Literal["trace.trace-post-failure.v1"]
    reason_code: Literal["trace_post_preflight_failed", "trace_post_result_validation_failed"]


__all__ = [
    "TracePostAsset",
    "TracePostCaption",
    "TracePostFailure",
    "TracePostInput",
    "TracePostMotif",
    "TracePostPlace",
    "TracePostSuccess",
]
