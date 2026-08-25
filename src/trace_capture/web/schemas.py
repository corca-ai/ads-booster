from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

from trace_capture.agent.tui_approval import PermissionMode
from trace_capture.contracts.generation import MarketingContextBundle
from trace_capture.contracts.models import DeviceTarget
from trace_capture.providers.models import ProviderModel
from trace_capture.transport.json_types import JsonObject
from trace_capture.workspace import (
    AssetId,
    AssetRelativePath,
    CandidateCaption,
    CandidateCountry,
    CandidateHypothesis,
    CandidateId,
    CandidatePrinciple,
    CandidateReference,
    CandidateReviewNote,
    CandidateShootingOrder,
    CandidateSource,
    CandidateStatus,
    CandidateTopic,
    ContextId,
    ContextKind,
    MemberId,
    PrivateSessionId,
    WorkspaceId,
)

_PRIVATE_SESSION_ID_PATTERN: Final = r"^[A-Za-z0-9_-]{1,64}$"


class WebModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


class HealthResponse(WebModel):
    status: str


class EmptyRunListResponse(RootModel[tuple[()]]):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


class LoginRequest(WebModel):
    workspace_id: WorkspaceId = Field(min_length=1, max_length=128)
    member_id: MemberId = Field(min_length=1, max_length=128)
    workspace_code: str = Field(min_length=1, max_length=256)
    member_code: str = Field(min_length=1, max_length=256)


class MemberLoginRequest(WebModel):
    workspace_id: WorkspaceId = Field(min_length=1, max_length=128)
    member_id: MemberId = Field(min_length=1, max_length=128)
    member_code: str = Field(min_length=1, max_length=256)


class AuthenticatedMemberResponse(WebModel):
    workspace_id: WorkspaceId
    workspace_name: str
    member_id: MemberId
    display_name: str
    is_admin: bool


class MemberInviteRequest(WebModel):
    display_name: str = Field(min_length=1, max_length=80)


class MemberInviteResponse(WebModel):
    member_access_id: str
    display_name: str


class ContextCreateRequest(WebModel):
    kind: ContextKind
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=100_000)


class ContextUpdateRequest(ContextCreateRequest):
    expected_revision: int = Field(ge=1)


class ContextResponse(WebModel):
    workspace_id: WorkspaceId
    context_id: ContextId
    kind: ContextKind
    title: str
    body: str
    revision: int
    created_at: float
    updated_at: float


class AssetCreateRequest(WebModel):
    context_id: ContextId | None = None
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=120)
    relative_path: AssetRelativePath
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)


class AssetUpdateRequest(AssetCreateRequest):
    pass


class AssetUploadRequest(WebModel):
    context_id: ContextId | None = None
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=120)
    content_base64: str = Field(min_length=1)


class AssetResponse(WebModel):
    workspace_id: WorkspaceId
    asset_id: AssetId
    context_id: ContextId | None
    filename: str
    media_type: str
    relative_path: str
    sha256: str
    size_bytes: int
    created_at: float


class SessionSummaryResponse(WebModel):
    session_id: PrivateSessionId
    title: str
    revision: int
    created_at: float
    updated_at: float


class SessionResponse(SessionSummaryResponse):
    workspace_id: WorkspaceId
    member_id: MemberId
    history: tuple[JsonObject, ...]


class ChatRequest(WebModel):
    prompt: str = Field(min_length=1, max_length=100_000)
    session_id: (
        Annotated[
            PrivateSessionId,
            Field(pattern=_PRIVATE_SESSION_ID_PATTERN),
        ]
        | None
    ) = None


class ChatEvent(WebModel):
    role: Literal["system", "error"]
    content: str


class ChatSettingsResponse(WebModel):
    model: str
    reasoning: str | None
    permission_mode: PermissionMode


class ChatCommandResponse(WebModel):
    command: str
    description: str


class ChatResponse(WebModel):
    session_id: PrivateSessionId | None
    answer: str = ""
    history: tuple[JsonObject, ...]
    revision: int
    replace_history: bool = False
    events: tuple[ChatEvent, ...] = ()
    sessions: tuple[SessionSummaryResponse, ...] = ()
    models: tuple[ProviderModel, ...] = ()
    settings: ChatSettingsResponse


class ChatApprovalResponse(WebModel):
    request_id: str
    action: str
    detail: str


class ChatApprovalDecisionRequest(WebModel):
    request_id: str = Field(min_length=1, max_length=128)
    decision: Literal["approve", "deny"]


class ChatApprovalDecisionResponse(WebModel):
    resolved: bool


class ChatError(WebModel):
    code: str
    message: str


class ChatErrorEnvelope(WebModel):
    detail: ChatError


class QueueEnqueueRequest(WebModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    bundle: MarketingContextBundle
    due_at: datetime | None = None
    max_attempts: int = Field(default=3, ge=1, le=20)


class GenerationRequest(WebModel):
    bundle: MarketingContextBundle


class CampaignCreateRequest(WebModel):
    name: str = Field(min_length=1, max_length=120)
    persona_context_id: ContextId
    promotion_context_id: ContextId
    reference_asset_ids: tuple[AssetId, ...] = ()
    reference_date: datetime
    device: DeviceTarget
    variation_count: int | None = Field(default=None, ge=1)


class CampaignStopRequest(WebModel):
    expected_revision: int = Field(ge=1)


class QueueReviewRequest(WebModel):
    accepted: bool
    expected_revision: int = Field(ge=1)


class CandidateCreateRequest(WebModel):
    topic: CandidateTopic
    country: CandidateCountry
    caption: CandidateCaption
    hypothesis: CandidateHypothesis
    refs_used: Annotated[tuple[CandidateReference, ...], Field(max_length=16)] = ()
    principles_applied: Annotated[tuple[CandidatePrinciple, ...], Field(max_length=32)] = ()
    shooting_order: CandidateShootingOrder = ""


class CandidateReviewRequest(WebModel):
    accepted: bool
    note: CandidateReviewNote | None = None
    expected_revision: int = Field(ge=1)


class CandidateResponse(WebModel):
    workspace_id: WorkspaceId
    candidate_id: CandidateId
    source: CandidateSource
    country: str
    topic: str
    caption: str
    hypothesis: str
    refs_used: tuple[str, ...]
    principles_applied: tuple[int, ...]
    shooting_order: str
    ai_verdict: str | None
    image_path: str | None
    status: CandidateStatus
    review_note: str | None
    revision: int
    created_at: float
    updated_at: float
