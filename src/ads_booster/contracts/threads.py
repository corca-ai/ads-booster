from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum, unique
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.agent_run import BoundedId
from ads_booster.contracts.models import ContractModel, CountryCode, Identifier, Sha256Digest

ThreadsProviderId = Annotated[str, Field(min_length=1, max_length=160)]
ThreadsUsername = Annotated[str, Field(min_length=1, max_length=80)]


class ThreadsAccountReference(ContractModel):
    reference_id: Identifier
    revision_id: Identifier


@unique
class ThreadsAccountStatus(StrEnum):
    ACTIVE = "active"
    REAUTH_REQUIRED = "reauth_required"
    REVOKED = "revoked"


class ThreadsAccount(ContractModel):
    schema_version: Literal["trace.threads-account.v1"] = "trace.threads-account.v1"
    connection_id: Identifier
    workspace_id: Identifier
    owner_member_id: Identifier
    provider_account_id: ThreadsProviderId
    username: ThreadsUsername
    country: CountryCode | None = None
    concept: Annotated[str, Field(max_length=2_000)] = ""
    tone: Annotated[str, Field(max_length=2_000)] = ""
    references: Annotated[tuple[ThreadsAccountReference, ...], Field(max_length=100)] = ()
    granted_scopes: Annotated[tuple[str, ...], Field(min_length=1, max_length=32)]
    token_ref: Identifier
    status: ThreadsAccountStatus = ThreadsAccountStatus.ACTIVE
    connected_at: datetime
    updated_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def require_valid_account(self) -> Self:
        for value in (self.connected_at, self.updated_at, self.expires_at):
            _require_utc(value)
        if not self.connected_at <= self.updated_at or self.expires_at <= self.connected_at:
            raise PydanticCustomError(
                "threads_account_lifetime_invalid", "account token lifetime is invalid"
            )
        if self.status is ThreadsAccountStatus.ACTIVE and self.updated_at >= self.expires_at:
            raise PydanticCustomError(
                "threads_active_account_expired", "active account metadata is expired"
            )
        if len(set(self.granted_scopes)) != len(self.granted_scopes):
            raise PydanticCustomError(
                "threads_account_scope_duplicate", "granted scopes must be unique"
            )
        if len({item.reference_id for item in self.references}) != len(self.references):
            raise PydanticCustomError(
                "threads_account_reference_duplicate", "reference ids must be unique"
            )
        return self


class ThreadsActor(ContractModel):
    workspace_id: Identifier
    member_id: Identifier
    conversation_id: Identifier
    source_event_id: Identifier
    scheduled: bool = False
    allowed_connection_ids: Annotated[tuple[Identifier, ...], Field(max_length=128)] = ()


class ThreadsPost(ContractModel):
    schema_version: Literal["trace.threads-post.v1"] = "trace.threads-post.v1"
    post_id: ThreadsProviderId
    account_id: ThreadsProviderId
    username: ThreadsUsername
    text: Annotated[str, Field(max_length=20_000)]
    permalink: Annotated[str, Field(min_length=1, max_length=2_000)]
    media_type: Annotated[str, Field(min_length=1, max_length=80)]
    timestamp: datetime
    root_post_id: ThreadsProviderId | None = None
    replied_to_id: ThreadsProviderId | None = None
    has_replies: bool = False

    @model_validator(mode="after")
    def require_timestamp(self) -> Self:
        if self.timestamp.tzinfo is None:
            raise PydanticCustomError(
                "threads_post_timestamp_requires_timezone", "post timestamp must be aware"
            )
        return self


class ThreadsMetric(ContractModel):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    period: Annotated[str, Field(min_length=1, max_length=80)]
    value: Annotated[int, Field(ge=0)] | None
    available: bool

    @model_validator(mode="after")
    def require_availability(self) -> Self:
        if self.available != (self.value is not None):
            raise PydanticCustomError(
                "threads_metric_availability_mismatch",
                "available metrics require values and unavailable metrics cannot have values",
            )
        return self


class ThreadsPublicationReceipt(ContractModel):
    schema_version: Literal["trace.threads-publication-receipt.v1"] = (
        "trace.threads-publication-receipt.v1"
    )
    operation_id: Identifier
    workspace_id: Identifier
    owner_member_id: Identifier
    run_id: BoundedId
    invocation_sha256: Sha256Digest
    connection_id: Identifier
    batch_id: Identifier
    item_id: Identifier
    draft_revision: Annotated[int, Field(ge=1)]
    ordered_asset_sha256: Annotated[tuple[Sha256Digest, ...], Field(max_length=20)]
    reply_to_id: ThreadsProviderId | None = None
    creation_ids: Annotated[tuple[ThreadsProviderId, ...], Field(max_length=21)] = ()
    published_post_id: ThreadsProviderId | None = None
    permalink: Annotated[str, Field(min_length=1, max_length=2_000)] | None = None
    pending_step: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    error_code: Annotated[str, Field(min_length=1, max_length=160)] | None = None
    provider_status: int | None = None
    provider_error_code: int | None = None
    retry_after_seconds: Annotated[int, Field(ge=0)] | None = None
    state: Literal["prepared", "publishing", "published", "uncertain", "failed"]
    updated_at: datetime

    @model_validator(mode="after")
    def require_published_identity(self) -> Self:
        _require_utc(self.updated_at)
        if self.state == "published" and (
            self.published_post_id is None or self.permalink is None
        ):
            raise PydanticCustomError(
                "threads_publication_identity_mismatch",
                "published receipts require both post id and permalink",
            )
        if self.state != "published" and self.permalink is not None:
            raise PydanticCustomError(
                "threads_publication_permalink_before_readback",
                "only published receipts may contain a permalink",
            )
        if self.state == "published" and self.error_code is not None:
            raise PydanticCustomError(
                "threads_publication_error_after_success",
                "published receipts cannot retain an error",
            )
        return self


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise PydanticCustomError("threads_datetime_requires_utc", "datetime must be UTC")


__all__ = [
    "ThreadsAccount",
    "ThreadsAccountReference",
    "ThreadsAccountStatus",
    "ThreadsActor",
    "ThreadsMetric",
    "ThreadsPost",
    "ThreadsProviderId",
    "ThreadsPublicationReceipt",
    "ThreadsUsername",
]
