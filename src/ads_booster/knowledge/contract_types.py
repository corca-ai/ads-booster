from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum, unique
from typing import Annotated, ClassVar, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, ConfigDict, Field
from pydantic_core import PydanticCustomError

from ads_booster.contracts.agent_run import BoundedId
from ads_booster.contracts.models import ContractModel

_UTC_ERROR_TYPE: Final = "knowledge_timestamp_not_utc"
_UTC_ERROR_MESSAGE: Final = "knowledge timestamps must use UTC"
_TIMEZONE_ERROR_TYPE: Final = "invalid_iana_timezone"
_TIMEZONE_ERROR_MESSAGE: Final = "memory timezone must be an IANA timezone"


class KnowledgeContractModel(ContractModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )


KnowledgeId = BoundedId
BoundedText = Annotated[str, Field(min_length=1, max_length=20_000)]
BoundedReason = Annotated[str, Field(min_length=1, max_length=2_000)]
BoundedLocator = Annotated[str, Field(min_length=1, max_length=2_000)]
MimeType = Annotated[str, Field(min_length=1, max_length=255, pattern=r"^[^\s/]+/[^\s/]+$")]


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
        raise PydanticCustomError(
            _UTC_ERROR_TYPE,
            _UTC_ERROR_MESSAGE,
        )
    return value


def require_iana_timezone(value: str) -> str:
    try:
        _ = ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise PydanticCustomError(
            _TIMEZONE_ERROR_TYPE,
            _TIMEZONE_ERROR_MESSAGE,
        ) from error
    return value


UtcDatetime = Annotated[datetime, AfterValidator(require_utc)]
IanaTimeZone = Annotated[
    str,
    Field(min_length=1, max_length=128),
    AfterValidator(require_iana_timezone),
]


@unique
class ScopeKind(StrEnum):
    WORKSPACE = "workspace"
    CHANNEL = "channel"
    CHANNEL_MEMBER = "channel_member"
    MEMBER = "member"


@unique
class GrantCapability(StrEnum):
    READ = "read"
    WRITE = "write"
    SCHEDULE = "schedule"
    BRAND_VOICE_EDIT = "brand_voice_edit"
    SHARE = "share"
    PURGE = "purge"


@unique
class InstructionAuthority(StrEnum):
    SYSTEM = "system"
    AUTHORIZED_USER = "authorized_user"
    DATA = "data"


@unique
class Provenance(StrEnum):
    HUMAN_DIRECT = "human_direct"
    EXTERNAL = "external"
    AGENT_DERIVED = "agent_derived"


@unique
class AuthorityClass(StrEnum):
    RUNTIME_POLICY = "runtime_policy"
    DELEGATED_TEAM_RULE = "delegated_team_rule"
    AUTHORIZED_TASK_INSTRUCTION = "authorized_task_instruction"


@unique
class SourceKind(StrEnum):
    FILE = "file"
    URL = "url"
    MESSAGE = "message"


@unique
class SourceExtractionStatus(StrEnum):
    PENDING = "pending"
    COMPLETE = "complete"
    PARTIAL = "partial"
    NEEDS_EXTRACTOR = "needs_extractor"
    FAILED = "failed"


@unique
class SourceCompleteness(StrEnum):
    FULL = "full"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


@unique
class SourceDisposition(StrEnum):
    PENDING = "pending"
    USE_ONLY = "use_only"
    REFERENCE = "reference"
    ADMIT = "admit"
    UPDATE = "update"
    INVESTIGATE = "investigate"
    IGNORE = "ignore"


@unique
class ConversationRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@unique
class ConversationEventKind(StrEnum):
    MESSAGE_FINALIZED = "message_finalized"
    MESSAGE_EDITED = "message_edited"
    MESSAGE_DELETED = "message_deleted"
    ATTACHMENT_RECEIVED = "attachment_received"


IngestEventKind = ConversationEventKind


@unique
class EvidenceKind(StrEnum):
    SOURCE_SEGMENT = "source_segment"
    CONVERSATION_EVENT = "conversation_event"
    MEMORY_ENTRY = "memory_entry"
    CLAIM = "claim"


@unique
class ClaimKind(StrEnum):
    FACT = "fact"
    DECISION = "decision"
    INFERENCE = "inference"


@unique
class ClaimStatus(StrEnum):
    ACTIVE = "active"
    CONTESTED = "contested"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


@unique
class WikiPageStatus(StrEnum):
    ACTIVE = "active"
    REDIRECT = "redirect"
    SPLIT = "split"
    RETRACTED = "retracted"


@unique
class PageRelationKind(StrEnum):
    RELATED_TO = "related_to"
    PART_OF = "part_of"
    SUMMARIZES = "summarizes"
    SPLIT_INTO = "split_into"


@unique
class MemoryKind(StrEnum):
    TEAM = "team"
    SOUL = "soul"
    CORE = "core"
    DAILY = "daily"


@unique
class MemoryEntryKind(StrEnum):
    FACT = "fact"
    DECISION = "decision"
    INFERENCE = "inference"
    OBSERVATION = "observation"


@unique
class MemoryStatus(StrEnum):
    ACTIVE = "active"
    CONTESTED = "contested"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


@unique
class DependencyState(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    RESTRICTED = "restricted"


@unique
class MemoryOrigin(StrEnum):
    DIRECT = "direct"
    WIKI_SUMMARY = "wiki_summary"


@unique
class SoulSection(StrEnum):
    READER_VIEW = "reader_view"
    VALUES = "values"
    PERSUASION = "persuasion"
    VOICE = "voice"
    TRADEOFFS = "tradeoffs"


@unique
class UsageRole(StrEnum):
    CONSTRAINT = "constraint"
    REFERENCE = "reference"
