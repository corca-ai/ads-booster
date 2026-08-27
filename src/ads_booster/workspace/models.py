from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import PurePosixPath
from typing import Annotated, ClassVar, Final, NewType

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.transport.json_types import JsonObject, JsonValue
from ads_booster.transport.json_types import JsonObject as _JsonObject

WorkspaceId = NewType("WorkspaceId", str)
MemberId = NewType("MemberId", str)
ContextId = NewType("ContextId", str)
AssetId = NewType("AssetId", str)
CandidateId = NewType("CandidateId", str)
MarketingAccountId = NewType("MarketingAccountId", str)
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
class CandidatePostingSlot(StrEnum):
    MORNING = "morning"
    EVENING = "evening"
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
class CandidatePersonaDomain(StrEnum):
    """The fixed vocabulary of persona domains one generated candidate can belong to.

    The domain is what coverage is counted over, so it has to be a closed set: a model free
    to invent its own labels would report perfect variety while writing the same three
    genres. Manual candidates and rows written before the field existed carry `None`.
    """

    SPORTS_FAN = "sports_fan"
    IDOL_FANDOM = "idol_fandom"
    EXAM_PREPPER = "exam_prepper"
    PARENTING = "parenting"
    OFFICE_WORKER = "office_worker"
    FITNESS_CREW = "fitness_crew"
    PET_OWNER = "pet_owner"
    CERT_STUDENT = "cert_student"
    SMALL_BUSINESS = "small_business"


PERSONA_DOMAIN_LABELS: Final = {
    CandidatePersonaDomain.SPORTS_FAN: "스포츠 팬",
    CandidatePersonaDomain.IDOL_FANDOM: "아이돌·밴드 팬덤",
    CandidatePersonaDomain.EXAM_PREPPER: "수험생",
    CandidatePersonaDomain.PARENTING: "육아",
    CandidatePersonaDomain.OFFICE_WORKER: "직군 직장인",
    CandidatePersonaDomain.FITNESS_CREW: "러닝·등산 크루",
    CandidatePersonaDomain.PET_OWNER: "반려동물 보호자",
    CandidatePersonaDomain.CERT_STUDENT: "자격증 준비생",
    CandidatePersonaDomain.SMALL_BUSINESS: "자영업",
}


@unique
class CandidateImagePipeline(StrEnum):
    """Which composition path actually produced a candidate's image.

    The native path drives a real device through Appium and exports the Trace wallpaper;
    the local fallback merges the packaged component fixture with the packaged iPhone UI
    on a host that has no capture environment. A reviewer has to be able to tell the two
    apart, because only the native path renders the candidate's own schedule and clock.
    """

    NATIVE = "native"
    LOCAL_FALLBACK = "local_fallback"


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
CandidateBackgroundIntent = Annotated[str, Field(min_length=1, max_length=500)]
CandidateBackgroundMood = Annotated[str, Field(min_length=1, max_length=40)]
CandidateBackgroundSearchQuery = Annotated[str, Field(min_length=1, max_length=200)]
CandidateSearchProvider = Annotated[str, Field(min_length=1, max_length=64)]
CandidateSearchedQuery = Annotated[str, Field(min_length=1, max_length=1_000)]
CandidateSourceUrl = Annotated[str, Field(min_length=1, max_length=4_096)]
CandidateLanguage = Annotated[str, Field(pattern=r"^[a-z]{2}$")]
CandidateImageDigest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
CandidateContextRelativePath = Annotated[str, Field(min_length=1, max_length=1_024)]
CandidateGenerationModel = Annotated[str, Field(min_length=1, max_length=200)]
CandidateBackgroundImageId = Annotated[str, Field(min_length=1, max_length=32)]
CandidateJudgeNote = Annotated[str, Field(max_length=500)]
CandidateJudgeReason = Annotated[str, Field(min_length=1, max_length=500)]


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
    # The domains this batch was told to write, one per candidate, chosen from the running
    # coverage counts. Batches generated before the assignment existed carry an empty tuple.
    assigned_domains: Annotated[tuple[CandidatePersonaDomain, ...], Field(max_length=16)] = ()
    # Set when the batch came from the Agent-kernel connector rather than the single-call
    # script engine, so a reviewer can tell which generator wrote the caption in front of them.
    agent_run_id: str | None = Field(default=None, max_length=200)


@unique
class CandidateBackgroundGrade(StrEnum):
    """One rubric grade the background judge gave a surviving image."""

    HIGH = "상"
    MID = "중"
    LOW = "하"


class CandidateBackgroundGrades(FrozenModel):
    """The three rubric grades one surviving background image was given."""

    authenticity: CandidateBackgroundGrade
    persona_fit: CandidateBackgroundGrade
    background_fit: CandidateBackgroundGrade


class CandidateBackgroundReview(FrozenModel):
    """One image the judge looked at, and what it decided about that image.

    A gated image carries `gate_reason` and no grades; a surviving image carries `grades`
    and the `score` they add up to. Both carry the source they came from, so a reviewer can
    open the page the judge was judging.
    """

    image_id: CandidateBackgroundImageId
    image_url: CandidateSourceUrl
    source_url: CandidateSourceUrl
    gated: bool
    gate_reason: CandidateJudgeReason | None = None
    grades: CandidateBackgroundGrades | None = None
    score: int | None = Field(default=None, ge=0, le=9)
    note: CandidateJudgeNote = ""


@unique
class CandidateQuerySource(StrEnum):
    """Where one query in the background search ladder came from."""

    ORIGINAL = "original"
    BROADENED = "broadened"
    REWRITTEN = "rewritten"


class CandidateBackgroundAttempt(FrozenModel):
    """One query the background search actually ran, and what came back for it.

    A model-authored query naming a specific person or character can legitimately return
    nothing, so the stage walks a short ladder of queries. Recording every rung is what
    lets a reviewer tell "nobody has published this photo" apart from "the search worked
    and the images were unusable".
    """

    query: CandidateSearchedQuery
    source: CandidateQuerySource
    results: int = Field(ge=0)
    passed_filters: int = Field(ge=0)
    filtered_stock: int = Field(default=0, ge=0)


class CandidateBackgroundJudgment(FrozenModel):
    """The full judgment behind the background that was actually used.

    Every image the collection step gathered appears in `reviews`, whether it was gated or
    graded, so the record shows what the winner beat rather than only that it won. A run
    that had to rewrite its query records both queries. Candidates composed before the
    judge existed carry `None`.
    """

    reviews: Annotated[tuple[CandidateBackgroundReview, ...], Field(min_length=1, max_length=16)]
    chosen_id: CandidateBackgroundImageId
    reason: CandidateJudgeReason
    model: CandidateGenerationModel
    query: CandidateSearchedQuery
    rewritten_query: CandidateSearchedQuery | None = None
    # Every query the ladder ran, in order. Rows written before the ladder existed carry an
    # empty tuple and render from `query`/`rewritten_query` alone.
    attempts: Annotated[tuple[CandidateBackgroundAttempt, ...], Field(max_length=8)] = ()
    tie_broken: bool = False
    # True when the tie-break was asked in both orders and they named different images, so
    # the graded totals decided instead. The pairwise call ran; it just did not agree with
    # itself, and a reviewer should be able to see that rather than infer a clean win.
    tie_break_inconsistent: bool = False


class CandidateBackgroundProvenance(FrozenModel):
    """Where the background actually behind one composed candidate image came from.

    Recorded by the image stage while it runs: the query that was searched, the provider
    that answered, the image file that was downloaded, the page that published it, the
    digest of the bytes written to disk, and which composition path consumed them.
    Candidates composed before this record existed, and candidates with no image yet,
    carry `None`.
    """

    query: CandidateSearchedQuery
    provider: CandidateSearchProvider
    image_url: CandidateSourceUrl
    source_url: CandidateSourceUrl
    sha256: CandidateImageDigest
    # Written by the AI background judge. Rows composed before the judge existed, and the
    # stock-allowlist fetcher that takes the first usable hit, carry `None`.
    judgment: CandidateBackgroundJudgment | None = None
    # Which composition path ran. Rows written before the fallback existed carry `NATIVE`,
    # which is what they were: the native path was the only one there was.
    pipeline: CandidateImagePipeline = CandidateImagePipeline.NATIVE


@dataclass(frozen=True, slots=True)
class CandidateImageAttachment:
    path: str
    sha256: str
    agent_run_id: str
    expected_revision: int
    background_provenance: CandidateBackgroundProvenance | None = None


class CandidateHistoryEntry(FrozenModel):
    """One recent candidate, reduced to what the next batch needs to avoid repeating it."""

    persona_domain: CandidatePersonaDomain | None
    topic: str


class CandidateImageInputs(FrozenModel):
    """Machine inputs the image stage needs to compose a lock-screen image.

    `background_subject` and `background_mood` are the canonical pair: the subject is drawn
    from a closed vocabulary so the judge and the search can both reason about it, and the
    mood is the concrete phrase the generating model wrote. `background_intent` is the
    single free-text field the Trace connector's native path reads; it is derived from the
    pair when a writer does not supply it, so both generators can feed the same downstream.
    """

    trace_items: Annotated[tuple[CandidateScheduleItem, ...], Field(min_length=1, max_length=8)]
    device_time: CandidateDeviceTime
    background_subject: CandidateBackgroundSubject
    background_mood: CandidateBackgroundMood
    # Composed from the pair above when a writer does not supply it, so a caller that only
    # knows the vocabulary never has to spell it out and one that only has free text still fits.
    background_intent: CandidateBackgroundIntent | None = None
    language: CandidateLanguage
    # Authored by the generating model as a concrete scene phrase, and the query the open-web
    # background search actually runs. Rows written before the field existed carry `None`, and
    # the image stage falls back to the mechanical query built from subject, mood, and topic.
    background_search_query: CandidateBackgroundSearchQuery | None = None

    @model_validator(mode="before")
    @classmethod
    def reconcile_background_fields(cls, value: JsonValue) -> JsonValue:
        """Accept either half of the background contract and fill in the other.

        Rows the script engine wrote carry the subject/mood pair; rows the Agent-kernel
        connector wrote carry only `background_intent`. Neither is discarded: the pair
        composes an intent for the native path, and a lone intent is kept verbatim as the
        mood under the `none` subject, which is exactly what "no vocabulary term was
        recorded" means.
        """
        if not isinstance(value, dict):
            return value
        subject = value.get("background_subject")
        mood = value.get("background_mood")
        intent = value.get("background_intent")
        has_pair = isinstance(subject, str) and isinstance(mood, str)
        if has_pair and isinstance(intent, str):
            return value
        reconciled = dict(value)
        if has_pair:
            reconciled["background_intent"] = f"{subject}: {mood}"
            return reconciled
        if not isinstance(intent, str):
            return value
        reconciled["background_subject"] = CandidateBackgroundSubject.NONE.value
        reconciled["background_mood"] = intent[:40]
        return reconciled


class CandidateCreate(FrozenModel):
    workspace_id: WorkspaceId
    source: CandidateSource
    country: CandidateCountry
    posting_slot: CandidatePostingSlot = CandidatePostingSlot.MANUAL
    topic: CandidateTopic
    persona_domain: CandidatePersonaDomain | None = None
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


@unique
class MarketingAccountStatus(StrEnum):
    """Where a marketing account stands in its life cycle.

    An account is a Threads identity a person has to open and tend, so the system may
    propose one but never creates one on its own. `PROPOSED` is that suggestion waiting for
    a human; `OBSERVING` is a freshly opened account whose posts have not earned a verdict
    yet; `ACTIVE` is one worth keeping in rotation; `RETIRED` stops selection without
    deleting the record, because its posting record is still evidence.
    """

    PROPOSED = "proposed"
    OBSERVING = "observing"
    ACTIVE = "active"
    RETIRED = "retired"


@unique
class LockScreenFont(StrEnum):
    """The lock-screen type faces iOS actually ships, as an account-level taste.

    A real person picks one and keeps it, so this belongs to the account rather than to a
    single post. Which of these the capture path can genuinely apply is a question for the
    Appium side; the field records the intent either way.
    """

    SF_PRO = "sf_pro"
    SF_PRO_ROUNDED = "sf_pro_rounded"
    SF_COMPACT = "sf_compact"
    NEW_YORK = "new_york"
    SF_MONO = "sf_mono"


class MarketingAccountTaste(FrozenModel):
    """The phone-shaped half of an account: what its wallpaper and clock look like."""

    background_subject: CandidateBackgroundSubject
    background_mood: Annotated[str, Field(min_length=1, max_length=60)]
    font: LockScreenFont


class MarketingAccountIdentity(FrozenModel):
    """Who the account is, in the terms every generated post has to stay inside.

    These fields are injected whole into generation, so each one is a constraint the
    caption, the schedule, and the background query all have to agree with. The concept is
    the single sentence a reader would recognise the account by; the voice is how that
    person writes, which is why it is stored here and not re-decided per post.
    """

    display_name: Annotated[str, Field(min_length=1, max_length=40)]
    age: int = Field(ge=13, le=99)
    region: Annotated[str, Field(min_length=1, max_length=40)]
    occupation: Annotated[str, Field(min_length=1, max_length=60)]
    concept: Annotated[str, Field(min_length=1, max_length=200)]
    domain: CandidatePersonaDomain
    interests: Annotated[tuple[str, ...], Field(min_length=1, max_length=8)]
    voice: Annotated[str, Field(min_length=1, max_length=400)]
    life_rhythm: Annotated[str, Field(min_length=1, max_length=200)]
    taste: MarketingAccountTaste


class MarketingAccountCreate(FrozenModel):
    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    identity: MarketingAccountIdentity
    status: MarketingAccountStatus = MarketingAccountStatus.OBSERVING
    note: Annotated[str, Field(max_length=400)] = ""


class MarketingAccountRecord(FrozenModel):
    workspace_id: WorkspaceId
    account_id: MarketingAccountId
    country: str
    identity: MarketingAccountIdentity
    status: MarketingAccountStatus
    note: str
    revision: int = Field(ge=1)
    created_at: float
    updated_at: float
