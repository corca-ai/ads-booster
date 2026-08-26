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
class CandidateBackgroundSubject(StrEnum):
    """Background subject vocabulary the operator's AXES document defines."""

    CHARACTER_KITTY = "character_kitty"
    CHARACTER_OTHER = "character_other"
    FAMILY_PHOTO = "family_photo"
    PERSON = "person"
    PET = "pet"
    SCENERY = "scenery"
    MINIMAL = "minimal"
    SPORTS_TEAM = "sports_team"
    NONE = "none"


@unique
class CandidateStatus(StrEnum):
    """Position of a candidate on the three-stage approval journey.

    Stage one moves a candidate from `AWAITING_REVIEW` to `CAPTION_APPROVED` or
    `REJECTED`. Stage two composes an image, moving `CAPTION_APPROVED` to
    `IMAGE_AWAITING_REVIEW`; approving that image reaches `SUBMITTED`, and rejecting it
    returns the candidate to `CAPTION_APPROVED` so a new image can be composed.
    Publishing a submitted post stays a human action outside this runtime.
    """

    AWAITING_REVIEW = "awaiting_review"
    CAPTION_APPROVED = "caption_approved"
    REJECTED = "rejected"
    IMAGE_AWAITING_REVIEW = "image_awaiting_review"
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
CandidateScheduleItem = Annotated[str, Field(min_length=1, max_length=80)]
CandidateDeviceTime = Annotated[str, Field(pattern=r"^\d{2}:\d{2}$")]
CandidateBackgroundMood = Annotated[str, Field(min_length=1, max_length=40)]
CandidateLanguage = Annotated[str, Field(pattern=r"^[a-z]{2}$")]
CandidateImageDigest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
CandidateContextRelativePath = Annotated[str, Field(min_length=1, max_length=1_024)]
CandidateGenerationModel = Annotated[str, Field(min_length=1, max_length=200)]


class CandidateContextDocument(FrozenModel):
    """One context document a generation run read, with the byte size it contributed."""

    relative_path: CandidateContextRelativePath
    size_bytes: int = Field(ge=0)


class CandidateGenerationProvenance(FrozenModel):
    """What one auto-generation batch actually read and asked for, recorded while it ran.

    Every field is a fact the run observed: the context documents assembled into the
    instruction with their UTF-8 byte sizes, the model id the run requested, the total
    instruction length, and the moment the provider call was made. Manual candidates and
    rows written before this record existed carry `None`.
    """

    documents: Annotated[tuple[CandidateContextDocument, ...], Field(max_length=64)]
    model: CandidateGenerationModel
    instruction_chars: int = Field(ge=0)
    generated_at: float


class CandidateImageInputs(FrozenModel):
    """Machine inputs the image stage needs to compose a lock-screen image."""

    trace_items: Annotated[tuple[CandidateScheduleItem, ...], Field(min_length=1, max_length=8)]
    device_time: CandidateDeviceTime
    background_subject: CandidateBackgroundSubject
    background_mood: CandidateBackgroundMood
    language: CandidateLanguage


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
    image_inputs: CandidateImageInputs
    ai_verdict: CandidateVerdict | None = None
    image_path: CandidateImagePath | None = None
    generation_provenance: CandidateGenerationProvenance | None = None


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
    image_inputs: CandidateImageInputs | None
    ai_verdict: str | None
    image_path: str | None
    image_sha256: str | None
    generation_provenance: CandidateGenerationProvenance | None
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
