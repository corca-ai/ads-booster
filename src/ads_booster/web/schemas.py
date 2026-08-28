from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from ads_booster.agent.tui_approval import PermissionMode
from ads_booster.candidate_generation.account_proposal import AccountProposal
from ads_booster.contracts.generation import MarketingContextBundle
from ads_booster.contracts.models import DeviceTarget
from ads_booster.providers.models import ProviderModel
from ads_booster.transport.json_types import JsonObject
from ads_booster.workspace import (
    AssetId,
    AssetRelativePath,
    CandidateBackgroundProvenance,
    CandidateCaption,
    CandidateCountry,
    CandidateGenerationProvenance,
    CandidateHypothesis,
    CandidateId,
    CandidateImageInputs,
    CandidatePersonaDomain,
    CandidatePostingSlot,
    CandidatePrinciple,
    CandidateReference,
    CandidateReviewNote,
    CandidateShootingOrder,
    CandidateSource,
    CandidateStatus,
    CandidateTopic,
    ContextId,
    ContextKind,
    MarketingAccountId,
    MarketingAccountIdentity,
    MarketingAccountRecord,
    MarketingAccountSchedule,
    MarketingAccountStatus,
    MemberId,
    PrivateSessionId,
    WorkspaceId,
)

_PRIVATE_SESSION_ID_PATTERN: Final = r"^[A-Za-z0-9_-]{1,64}$"


class WebModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


class HealthResponse(WebModel):
    status: str


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
    posting_slot: CandidatePostingSlot = CandidatePostingSlot.MANUAL
    persona_domain: CandidatePersonaDomain | None = None
    caption: CandidateCaption
    hypothesis: CandidateHypothesis
    image_inputs: CandidateImageInputs
    refs_used: Annotated[tuple[CandidateReference, ...], Field(max_length=16)] = ()
    principles_applied: Annotated[tuple[CandidatePrinciple, ...], Field(max_length=32)] = ()
    shooting_order: CandidateShootingOrder = ""


class CandidateReviewRequest(WebModel):
    accepted: bool
    note: CandidateReviewNote | None = None
    expected_revision: int = Field(ge=1)


class CandidateImageReviewRequest(CandidateReviewRequest):
    """Stage-two decision on the composed image."""


class CandidateResponse(WebModel):
    workspace_id: WorkspaceId
    candidate_id: CandidateId
    source: CandidateSource
    country: str
    posting_slot: CandidatePostingSlot
    topic: str
    persona_domain: CandidatePersonaDomain | None
    caption: str
    hypothesis: str
    refs_used: tuple[str, ...]
    principles_applied: tuple[int, ...]
    shooting_order: str
    image_inputs: CandidateImageInputs | None
    ai_verdict: str | None
    image_path: str | None
    image_sha256: str | None
    agent_run_id: str | None
    generation_provenance: CandidateGenerationProvenance | None
    background_provenance: CandidateBackgroundProvenance | None
    status: CandidateStatus
    review_note: str | None
    revision: int
    created_at: float
    updated_at: float


class MarketingAccountCreateRequest(WebModel):
    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    identity: MarketingAccountIdentity
    schedule: MarketingAccountSchedule
    status: MarketingAccountStatus = MarketingAccountStatus.OBSERVING
    note: Annotated[str, Field(max_length=400)] = ""


class MarketingAccountUpdateRequest(WebModel):
    identity: MarketingAccountIdentity
    schedule: MarketingAccountSchedule
    note: Annotated[str, Field(max_length=400)] = ""
    expected_revision: int = Field(ge=1)


class MarketingAccountStatusRequest(WebModel):
    status: MarketingAccountStatus
    expected_revision: int = Field(ge=1)


class AccountProposalRequest(WebModel):
    """Ask for accounts worth opening in one country."""

    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")] = "KR"


class AccountProposalResponse(WebModel):
    """One suggested account, in exactly the shape the create form submits.

    Proposals are not stored. What the browser does with this is fill the form, which the
    person then edits and submits down the ordinary creation route, so nothing here needs
    an id or a revision.
    """

    identity: MarketingAccountIdentity
    reason: str

    @classmethod
    def of(cls, proposal: AccountProposal) -> AccountProposalResponse:
        return cls(identity=proposal.identity, reason=proposal.reason)


class MarketingAccountResponse(WebModel):
    """One account, flattened into the field names the hosted control plane already emits.

    `display_name`, `language`, `timezone`, the two posting times and `generation_enabled`
    are lifted out of the record so a card rendered from this response and a card rendered
    from D1 read the same keys. `identity` carries the half the hosted table does not have
    yet, and is what the shared shell shows once it is there.
    """

    workspace_id: WorkspaceId
    account_id: MarketingAccountId
    display_name: str
    country: str
    language: str
    timezone: str
    morning_time: str
    evening_time: str
    generation_enabled: bool
    identity: MarketingAccountIdentity
    status: MarketingAccountStatus
    note: str
    revision: int
    created_at: float
    updated_at: float

    @classmethod
    def of(cls, record: MarketingAccountRecord) -> MarketingAccountResponse:
        return cls(
            workspace_id=record.workspace_id,
            account_id=record.account_id,
            display_name=record.identity.display_name,
            country=record.country,
            language=record.schedule.language,
            timezone=record.schedule.timezone,
            morning_time=record.schedule.morning_time,
            evening_time=record.schedule.evening_time,
            generation_enabled=record.schedule.generation_enabled,
            identity=record.identity,
            status=record.status,
            note=record.note,
            revision=record.revision,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
