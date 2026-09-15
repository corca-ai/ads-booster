from __future__ import annotations

from typing import Annotated, ClassVar

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ThreadsApiModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)


class TokenResponse(ThreadsApiModel):
    access_token: Annotated[str, Field(min_length=1)]
    user_id: str | int | None = None
    token_type: str | None = None
    expires_in: Annotated[int, Field(gt=0)] | None = None


class ThreadsUserResponse(ThreadsApiModel):
    id: str
    username: str


class IdResponse(ThreadsApiModel):
    id: str


class ContainerStatusResponse(ThreadsApiModel):
    id: str
    status: str = Field(validation_alias=AliasChoices("status", "status_code"))
    error_message: str | None = None


class ThreadsApiPost(ThreadsApiModel):
    id: str
    username: str = ""
    text: str = ""
    permalink: str = ""
    media_type: str = "TEXT_POST"
    timestamp: str
    has_replies: bool = False
    root_post: IdResponse | None = None
    replied_to: IdResponse | None = None


class PagingCursor(ThreadsApiModel):
    before: str | None = None
    after: str | None = None


class Paging(ThreadsApiModel):
    cursors: PagingCursor = Field(default_factory=PagingCursor)
    next: str | None = None
    previous: str | None = None


class PostsResponse(ThreadsApiModel):
    data: tuple[ThreadsApiPost, ...] = ()
    paging: Paging | None = None


class InsightValue(ThreadsApiModel):
    value: int | None = None


class ThreadsApiInsight(ThreadsApiModel):
    name: str
    period: str = "lifetime"
    values: tuple[InsightValue, ...] = ()
    total_value: InsightValue | None = None

    def scalar(self) -> int | None:
        if self.total_value is not None:
            return self.total_value.value
        if not self.values:
            return None
        return self.values[-1].value


class InsightsResponse(ThreadsApiModel):
    data: tuple[ThreadsApiInsight, ...] = ()


class ApiErrorBody(ThreadsApiModel):
    message: str = "Threads API request failed"
    type: str | None = None
    code: int | None = None
    error_subcode: int | None = None
    fbtrace_id: str | None = None


class ApiErrorResponse(ThreadsApiModel):
    error: ApiErrorBody


class GranularScope(ThreadsApiModel):
    scope: str
    target_ids: tuple[str, ...] = ()


class DebugTokenData(ThreadsApiModel):
    is_valid: bool
    user_id: str | None = None
    expires_at: int | None = None
    data_access_expires_at: int | None = None
    scopes: tuple[str, ...] = ()
    granular_scopes: tuple[GranularScope, ...] = ()

    def scope_names(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.scopes, *(item.scope for item in self.granular_scopes))))


class DebugTokenResponse(ThreadsApiModel):
    data: DebugTokenData


__all__ = [
    "ApiErrorResponse",
    "DebugTokenResponse",
    "GranularScope",
    "ContainerStatusResponse",
    "IdResponse",
    "InsightsResponse",
    "PostsResponse",
    "ThreadsApiInsight",
    "ThreadsApiPost",
    "ThreadsUserResponse",
    "TokenResponse",
]
