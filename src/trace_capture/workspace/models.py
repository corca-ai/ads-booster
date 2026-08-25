from __future__ import annotations

from enum import StrEnum, unique
from pathlib import PurePosixPath
from typing import Annotated, ClassVar, NewType

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic_core import PydanticCustomError

from trace_capture.transport.json_types import JsonObject
from trace_capture.transport.json_types import JsonObject as _JsonObject

WorkspaceId = NewType("WorkspaceId", str)
MemberId = NewType("MemberId", str)
ContextId = NewType("ContextId", str)
AssetId = NewType("AssetId", str)
CandidateId = NewType("CandidateId", str)
PrivateSessionId = NewType("PrivateSessionId", str)


class FrozenModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


@unique
class ContextKind(StrEnum):
    PERSONA = "persona"
    PROMOTION = "promotion"
    REFERENCE = "reference"
    RULE = "rule"


class WorkspaceRecord(FrozenModel):
    workspace_id: WorkspaceId
    name: str = Field(min_length=1, max_length=80)
    code_version: int = Field(ge=1)
    created_at: float
    updated_at: float


class ProvisionedWorkspace(FrozenModel):
    workspace: WorkspaceRecord
    access_code: str


class MemberRecord(FrozenModel):
    workspace_id: WorkspaceId
    member_id: MemberId
    display_name: str = Field(min_length=1, max_length=80)
    code_version: int = Field(ge=1)
    created_at: float
    updated_at: float


class ProvisionedMember(FrozenModel):
    member: MemberRecord
    invite_code: str


class ContextCreate(FrozenModel):
    kind: ContextKind
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=100_000)


class ContextRecord(FrozenModel):
    workspace_id: WorkspaceId
    context_id: ContextId
    kind: ContextKind
    title: str
    body: str
    revision: int = Field(ge=1)
    created_at: float
    updated_at: float


def _require_asset_relative_path(value: str) -> str:
    error_code = "unsafe_asset_path"
    error_message = "asset paths must be normalized relative paths below assets/"
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value != value.strip()
        or "\\" in value
        or not value.startswith("assets/")
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise PydanticCustomError(
            error_code,
            error_message,
        )
    return value


AssetRelativePath = Annotated[
    str,
    Field(min_length=1, max_length=1024),
    AfterValidator(_require_asset_relative_path),
]


class AssetCreate(FrozenModel):
    context_id: ContextId | None = None
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=120)
    relative_path: AssetRelativePath
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)


class AssetRecord(FrozenModel):
    workspace_id: WorkspaceId
    asset_id: AssetId
    context_id: ContextId | None
    filename: str
    media_type: str
    relative_path: str
    sha256: str
    size_bytes: int
    created_at: float


@unique
class CandidateSource(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


@unique
class CandidateStatus(StrEnum):
    """Position of a candidate on the three-stage approval journey.

    Only stage one is implemented: reviewing a candidate moves it from
    `AWAITING_REVIEW` to `CAPTION_APPROVED` or `REJECTED`. `IMAGE_APPROVED` and
    `SUBMITTED` name planned stages two and three; no code path reaches them yet.
    """

    AWAITING_REVIEW = "awaiting_review"
    CAPTION_APPROVED = "caption_approved"
    REJECTED = "rejected"
    IMAGE_APPROVED = "image_approved"
    SUBMITTED = "submitted"


CandidateCountry = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
CandidateTopic = Annotated[str, Field(min_length=1, max_length=200)]
CandidateCaption = Annotated[str, Field(min_length=1, max_length=10_000)]
CandidateHypothesis = Annotated[str, Field(min_length=1, max_length=2_000)]
CandidateReference = Annotated[str, Field(min_length=1, max_length=80)]
CandidatePrinciple = Annotated[int, Field(ge=1)]
CandidateShootingOrder = Annotated[str, Field(max_length=20_000)]
CandidateVerdict = Annotated[str, Field(min_length=1, max_length=2_000)]
CandidateImagePath = Annotated[str, Field(min_length=1, max_length=1_024)]
CandidateReviewNote = Annotated[str, Field(min_length=1, max_length=2_000)]


class CandidateCreate(FrozenModel):
    workspace_id: WorkspaceId
    source: CandidateSource
    country: CandidateCountry
    topic: CandidateTopic
    caption: CandidateCaption
    hypothesis: CandidateHypothesis
    refs_used: Annotated[tuple[CandidateReference, ...], Field(max_length=16)] = ()
    principles_applied: Annotated[tuple[CandidatePrinciple, ...], Field(max_length=32)] = ()
    shooting_order: CandidateShootingOrder = ""
    ai_verdict: CandidateVerdict | None = None
    image_path: CandidateImagePath | None = None


class CandidateRecord(FrozenModel):
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
    revision: int = Field(ge=1)
    created_at: float
    updated_at: float


class PrivateSessionCreate(FrozenModel):
    title: str = Field(min_length=1, max_length=80)
    history: tuple[JsonObject, ...]


class PrivateSessionRecord(FrozenModel):
    workspace_id: WorkspaceId
    member_id: MemberId
    session_id: PrivateSessionId
    title: str
    history: tuple[JsonObject, ...]
    revision: int = Field(ge=1)
    created_at: float
    updated_at: float


_ = PrivateSessionCreate.model_rebuild(_types_namespace={"JsonObject": _JsonObject})
_ = PrivateSessionRecord.model_rebuild(_types_namespace={"JsonObject": _JsonObject})
