from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Never, override

from pydantic import TypeAdapter, ValidationError

from ads_booster.contracts.agent_run import BoundedId
from ads_booster.contracts.models import RelativePath, Sha256Digest

_ID: TypeAdapter[str] = TypeAdapter(BoundedId)
_DIGEST: TypeAdapter[str] = TypeAdapter(Sha256Digest)


@unique
class SourceFileKind(StrEnum):
    ORIGINAL = "original.bin"
    EXTRACTED = "extracted.md"
    MANIFEST = "manifest.json"
    MESSAGE = "message.json"


@dataclass(frozen=True, slots=True)
class SourceRevisionTarget:
    source_id: BoundedId
    revision_id: BoundedId
    file_kind: SourceFileKind


@dataclass(frozen=True, slots=True)
class KnowledgeRevisionTarget:
    page_id: BoundedId
    revision_id: BoundedId


@dataclass(frozen=True, slots=True)
class MemoryRevisionTarget:
    workspace_id: BoundedId
    document_id: BoundedId
    revision_id: BoundedId


@dataclass(frozen=True, slots=True)
class SkillRevisionTarget:
    workspace_id: BoundedId
    skill_id: BoundedId
    revision_id: BoundedId


type RevisionTarget = (
    SourceRevisionTarget | KnowledgeRevisionTarget | MemoryRevisionTarget | SkillRevisionTarget
)


@dataclass(frozen=True, slots=True)
class RevisionFileDraft:
    operation_id: BoundedId
    target: RevisionTarget
    content: bytes
    sha256: Sha256Digest


@dataclass(frozen=True, slots=True)
class PreparedRevisionFile:
    operation_id: BoundedId
    target: RevisionTarget
    sha256: Sha256Digest
    byte_length: int


@dataclass(frozen=True, slots=True)
class PublishedRevisionFile:
    target: RevisionTarget
    relative_path: RelativePath
    sha256: Sha256Digest
    byte_length: int


@dataclass(slots=True)
class KnowledgeFileStoreError(Exception):
    code: str
    target: str

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.target}"


def store_error(code: str, target: str) -> KnowledgeFileStoreError:
    return KnowledgeFileStoreError(code=code, target=target)


def fail(code: str, target: str) -> Never:
    raise KnowledgeFileStoreError(code=code, target=target)


def bounded_id(field: str, value: str) -> str:
    try:
        return _ID.validate_python(value)
    except ValidationError as error:
        error_code = f"{field}_invalid"
        raise store_error(error_code, value) from error


def digest(value: str) -> str:
    try:
        return _DIGEST.validate_python(value)
    except ValidationError as error:
        error_code = "sha256_invalid"
        raise store_error(error_code, value) from error


def revision_relative_path(target: RevisionTarget) -> RelativePath:
    match target:
        case SourceRevisionTarget(source_id=source_id, revision_id=revision_id, file_kind=kind):
            source_id = bounded_id("source_id", source_id)
            revision_id = bounded_id("revision_id", revision_id)
            return f"sources/{source_id}/{revision_id}/{kind.value}"
        case KnowledgeRevisionTarget(page_id=page_id, revision_id=revision_id):
            page_id = bounded_id("page_id", page_id)
            revision_id = bounded_id("revision_id", revision_id)
            return f"knowledge/{page_id}/{revision_id}.md"
        case MemoryRevisionTarget(
            workspace_id=workspace_id,
            document_id=document_id,
            revision_id=revision_id,
        ):
            workspace_id = bounded_id("workspace_id", workspace_id)
            document_id = bounded_id("document_id", document_id)
            revision_id = bounded_id("revision_id", revision_id)
            return f"teams/{workspace_id}/revisions/{document_id}/{revision_id}.md"
        case SkillRevisionTarget(
            workspace_id=workspace_id,
            skill_id=skill_id,
            revision_id=revision_id,
        ):
            workspace_id = bounded_id("workspace_id", workspace_id)
            skill_id = bounded_id("skill_id", skill_id)
            revision_id = bounded_id("revision_id", revision_id)
            return f"teams/{workspace_id}/skills/{skill_id}/{revision_id}.md"


def skill_display_relative_path(workspace_id: str, skill_id: str) -> RelativePath:
    workspace_id = bounded_id("workspace_id", workspace_id)
    skill_id = bounded_id("skill_id", skill_id)
    return f"teams/{workspace_id}/skills/{skill_id}/SKILL.md"


__all__ = [
    "KnowledgeFileStoreError",
    "KnowledgeRevisionTarget",
    "MemoryRevisionTarget",
    "PreparedRevisionFile",
    "PublishedRevisionFile",
    "RevisionFileDraft",
    "RevisionTarget",
    "SkillRevisionTarget",
    "SourceFileKind",
    "SourceRevisionTarget",
    "bounded_id",
    "digest",
    "fail",
    "revision_relative_path",
    "skill_display_relative_path",
    "store_error",
]
