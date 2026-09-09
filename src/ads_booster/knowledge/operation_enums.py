from __future__ import annotations

from enum import StrEnum, unique


@unique
class BrandState(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


@unique
class BrandEventKind(StrEnum):
    REGISTERED = "registered"
    VOICE_ADOPTED = "voice_adopted"
    RETIRED = "retired"
    REACTIVATED = "reactivated"


@unique
class TaskBindingState(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


@unique
class ConstraintCompatibility(StrEnum):
    COMPATIBLE = "compatible"
    CONFLICT = "conflict"
    PENDING = "compatibility_pending"


@unique
class ChangeImpactKind(StrEnum):
    DISPLAY_ONLY = "display_only"
    SEMANTIC_OR_UNKNOWN = "semantic_or_unknown"
    ACCESS_OR_DELETION = "access_or_deletion"


@unique
class MemoryOperationKind(StrEnum):
    ADD = "add"
    UPDATE = "update"
    SUPERSEDE = "supersede"
    RETRACT = "retract"


@unique
class SkillOrigin(StrEnum):
    BUILTIN = "builtin"
    BUILTIN_OVERRIDE = "builtin_override"
    AGENT_CREATED = "agent_created"


@unique
class SkillTargetKind(StrEnum):
    LEARNED = "learned"
    BUILTIN_OVERRIDE = "builtin_override"


@unique
class SkillOperationKind(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    SUPERSEDE = "supersede"
    RETRACT = "retract"


@unique
class LearningPurpose(StrEnum):
    CONVERSATIONAL_FEEDBACK = "conversational_feedback"
    TERMINAL_EXPERIENCE_REVIEW = "terminal_experience_review"


@unique
class ExperienceOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    OBSERVED = "observed"
    FAILED = "failed"
    UNKNOWN_SIDE_EFFECT = "unknown_side_effect"
    INVALIDATED = "invalidated"


@unique
class KnowledgeOperationKind(StrEnum):
    CLAIM_ADD = "claim_add"
    CLAIM_UPDATE = "claim_update"
    CLAIM_MERGE = "claim_merge"
    CLAIM_SUPERSEDE = "claim_supersede"
    CLAIM_RETRACT = "claim_retract"
    PAGE_CREATE = "page_create"
    PAGE_EDIT = "page_edit"
    PAGE_RENAME = "page_rename"
    PAGE_LINK = "page_link"
    PAGE_UNLINK = "page_unlink"
    PAGE_MERGE = "page_merge"
    PAGE_SPLIT = "page_split"


@unique
class OperationStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    REPLAYED = "replayed"
    CONFLICT = "conflict"
    REJECTED = "rejected"
    FAILED = "failed"


@unique
class CorrectionScope(StrEnum):
    TASK_ONLY = "task_only"
    TEAM = "team"


@unique
class CorrectionStatus(StrEnum):
    PENDING = "correction_pending"
    APPLIED = "applied"
    TASK_ONLY = "task_only"
    REJECTED = "rejected"


@unique
class JobPriority(StrEnum):
    URGENT = "urgent"
    ROUTINE = "routine"


@unique
class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_DEPENDENCY = "waiting_dependency"
    AWAITING_ANSWER = "awaiting_answer"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@unique
class JobKind(StrEnum):
    EXTRACTION = "extraction"
    CURATION = "curation"
    INDEX = "index"
    MEMORY_CONSOLIDATE = "memory_consolidate"
    MEMORY_SUMMARY_REFRESH = "memory_summary_refresh"
    MEMORY_VIEW_REFRESH = "memory_view_refresh"
    SOURCE_REVIEW = "source_review"


@unique
class CurationTarget(StrEnum):
    NONE = "none"
    TEAM = "team"
    SOUL = "soul"
    CORE = "core"
    DAILY = "daily"
    WIKI = "wiki"


@unique
class BatchState(StrEnum):
    COLLECTING = "collecting"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
