from __future__ import annotations

import re
from enum import StrEnum, unique
from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Imported at runtime: pydantic resolves the "before" validator's annotation while it
# builds the model, so a type-checking-only import would fail there.
from ads_booster.transport.json_types import JsonValue  # noqa: TC001


class FrozenModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


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


CandidateCountry = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
CandidateTopic = Annotated[str, Field(min_length=1, max_length=200)]
CandidateCaption = Annotated[str, Field(min_length=1, max_length=10_000)]
CandidateHypothesis = Annotated[str, Field(min_length=1, max_length=2_000)]
CandidateReference = Annotated[str, Field(min_length=1, max_length=80)]
CandidatePrinciple = Annotated[int, Field(ge=1)]
CandidateShootingOrder = Annotated[str, Field(max_length=10_000)]
# A lock-screen row renders as "HH:MM 제목", and the hosted control plane refuses anything
# else. Holding the draft to the same shape is what keeps a caption the batch already paid
# for from failing at the delivery boundary instead of at the model.
CandidateScheduleItem = Annotated[
    str,
    Field(min_length=7, max_length=80, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d\s+.+$"),
]
# The fifteen colours Trace assigns to an event. Changing an event's colour is a paid
# feature, so a free capture account renders every row in the default blue no matter what
# is written here — the mixed palette that fills a screen is only reachable on Pro.
CandidateEventColor = Literal[
    "6E86F7",
    "3D73DD",
    "8A2BE2",
    "9B5DE5",
    "F9C74F",
    "F26419",
    "D62246",
    "DA4C93",
    "B598F9",
    "00B4D8",
    "5FBDB0",
    "2D936C",
    "FF9E00",
    "FF6B6B",
    "AF3B6E",
]
_WEEK_DAYS: Final = 7
_CLOCK: Final = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
CandidateScheduleTitle = Annotated[str, Field(min_length=1, max_length=40)]
CandidateTodoTitle = Annotated[str, Field(min_length=1, max_length=60)]
CandidateDeviceTime = Annotated[str, Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")]
CandidateBackgroundIntent = Annotated[str, Field(min_length=1, max_length=500)]
CandidateBackgroundMood = Annotated[str, Field(min_length=1, max_length=40)]
CandidateBackgroundSearchQuery = Annotated[str, Field(min_length=1, max_length=200)]
CandidateLanguage = Annotated[str, Field(pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")]
CandidateContextRelativePath = Annotated[str, Field(min_length=1, max_length=1_024)]
CandidateGenerationModel = Annotated[str, Field(min_length=1, max_length=200)]


class CandidateContextDocument(FrozenModel):
    """One context document a generation run read, with the byte size it contributed."""

    relative_path: CandidateContextRelativePath
    size_bytes: int = Field(ge=0)


class CandidateGenerationProvenance(FrozenModel):
    """What one auto-generation call actually read and asked for, recorded while it ran.

    Every field is a fact the run observed: the context documents assembled into the
    instruction with their UTF-8 byte sizes, the model id the run requested, the total
    instruction length, and the moment the provider call was made. This record is the
    difference between a candidate a reviewer can audit and one that merely asserts it was
    grounded in something.
    """

    documents: Annotated[tuple[CandidateContextDocument, ...], Field(max_length=64)]
    model: CandidateGenerationModel
    instruction_chars: int = Field(ge=0)
    generated_at: float
    # The domains this batch was told to write, one per candidate, assigned before any call
    # goes out.
    assigned_domains: Annotated[tuple[CandidatePersonaDomain, ...], Field(max_length=16)] = ()
    # The reference bodies this call was shown. Recorded so "which references produce
    # candidates worth approving" is a question the stored rows can answer later.
    reference_ids: Annotated[tuple[CandidateReference, ...], Field(max_length=16)] = ()
    # How many candidates this one call was asked for. Every candidate it produced carries
    # the same provenance, so without this a reviewer cannot tell one call that wrote four
    # from four calls that wrote one — and that is the difference the batching decision has
    # to be judged on later.
    batch_size: int = Field(default=1, ge=1)


class CandidateHistoryEntry(FrozenModel):
    """One recent candidate, reduced to what the next batch needs to avoid repeating it."""

    persona_domain: CandidatePersonaDomain | None
    topic: str


class CandidateAccountBrief(FrozenModel):
    """An account flattened to what the generation instruction has to state.

    Generation never needs the revision, the status, or the posting schedule, so it is
    handed this instead of the record: the instruction is easier to read, and a change to
    how accounts are stored does not reach into how they are described to the model.
    """

    display_name: str
    age: int
    region: str
    occupation: str
    concept: str
    domain: CandidatePersonaDomain
    interests: tuple[str, ...]
    life_rhythm: str
    background_subject: CandidateBackgroundSubject
    background_mood: str


class CandidateScheduleEntry(FrozenModel):
    """One row of the week the lock screen renders, placed on a day rather than a clock.

    The old contract was a `"HH:MM 제목"` string, which can only describe today. A screen
    that fills its week needs three things a string cannot carry: which day the row sits on,
    how many days it spans, and what colour it draws in. The spanning bars are what actually
    fill the strip — our own best-performing screen was mostly all-day rows, not timed ones.

    A plain string is still accepted and read as an untimed-or-timed row on day zero, so
    every draft written against the old contract keeps validating.
    """

    title: CandidateScheduleTitle
    # Offset from the captured day. Zero is the day the screen shows.
    day: Annotated[int, Field(ge=0, le=6)] = 0
    # One is a single-day row; anything larger draws the multi-day bar that fills the strip.
    days: Annotated[int, Field(ge=1, le=7)] = 1
    # `None` renders as an all-day row, which is what most rows on a full screen are.
    time: CandidateDeviceTime | None = None
    color: CandidateEventColor | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_string(cls, value: JsonValue) -> JsonValue:
        """Read the old `"HH:MM 제목"` row as a timed entry on the captured day."""
        if not isinstance(value, str):
            return value
        head, separator, tail = value.partition(" ")
        if separator and _CLOCK.fullmatch(head) and tail.strip():
            return {"title": tail.strip(), "time": head}
        return {"title": value.strip()}

    @model_validator(mode="after")
    def keep_the_span_inside_the_week(self) -> CandidateScheduleEntry:
        """A bar that runs past the seventh day has nowhere to draw its remainder."""
        if self.day + self.days > _WEEK_DAYS:
            message = "a schedule entry may not span past the seventh day"
            raise ValueError(message)
        return self


class CandidateImageInputs(FrozenModel):
    """Machine inputs the image stage needs to compose a lock-screen image.

    `background_subject` and `background_mood` are the canonical pair: the subject is drawn
    from a closed vocabulary so the judge and the search can both reason about it, and the
    mood is the concrete phrase the generating model wrote. `background_intent` is the
    single free-text field the native capture path reads; it is derived from the pair when a
    writer does not supply it, so both halves of the contract stay satisfiable.
    """

    trace_items: Annotated[tuple[CandidateScheduleEntry, ...], Field(min_length=5, max_length=24)]
    # Undated chores. Trace keeps them in a separate list, and the screen draws them in
    # their own column beside the schedule — the pair is what our fullest screen showed.
    trace_todos: Annotated[tuple[CandidateTodoTitle, ...], Field(max_length=20)] = ()
    device_time: CandidateDeviceTime
    background_subject: CandidateBackgroundSubject
    background_mood: CandidateBackgroundMood
    # Composed from the pair above when a writer does not supply it, so a caller that only
    # knows the vocabulary never has to spell it out and one that only has free text still fits.
    background_intent: CandidateBackgroundIntent | None = None
    language: CandidateLanguage
    # Authored by the generating model as a concrete scene phrase, and the query the open-web
    # background search actually runs.
    background_search_query: CandidateBackgroundSearchQuery | None = None

    @model_validator(mode="before")
    @classmethod
    def reconcile_background_fields(cls, value: JsonValue) -> JsonValue:
        """Accept either half of the background contract and fill in the other.

        A draft carries the subject/mood pair; a caller holding only free text carries
        `background_intent`. Neither is discarded: the pair composes an intent for the
        native path, and a lone intent is kept verbatim as the mood under the `none`
        subject, which is exactly what "no vocabulary term was recorded" means.
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
