from __future__ import annotations

from dataclasses import dataclass, field
from typing import override


@dataclass(slots=True)
class CurationSourceUnavailableError(ValueError):
    source_id: str
    revision_id: str
    code: str = field(init=False, default="curation_source_unavailable")

    @override
    def __str__(self) -> str:
        return self.code


@dataclass(slots=True)
class KnowledgePolicyError(Exception):
    code: str

    @override
    def __str__(self) -> str:
        return self.code


@dataclass(slots=True)
class AccessDeniedError(KnowledgePolicyError):
    actor_id: str
    target_workspace_id: str

    @override
    def __str__(self) -> str:
        return f"{self.code}: actor={self.actor_id} workspace={self.target_workspace_id}"


@dataclass(slots=True)
class PolicyEpochStaleError(KnowledgePolicyError):
    actor_epoch: int
    current_epoch: int

    @override
    def __str__(self) -> str:
        return f"{self.code}: actor_epoch={self.actor_epoch} current_epoch={self.current_epoch}"


@dataclass(slots=True)
class ScopeIntersectionError(KnowledgePolicyError):
    workspace_ids: tuple[str, ...]

    @override
    def __str__(self) -> str:
        return f"{self.code}: workspaces={','.join(self.workspace_ids)}"


@dataclass(slots=True)
class AuthorityViolationError(KnowledgePolicyError):
    target_id: str

    @override
    def __str__(self) -> str:
        return f"{self.code}: target={self.target_id}"


@dataclass(slots=True)
class EvidenceResolutionError(KnowledgePolicyError):
    evidence_id: str
    revision_id: str
    code: str = field(init=False, default="missing_evidence")

    @override
    def __str__(self) -> str:
        return f"{self.code}: evidence={self.evidence_id} revision={self.revision_id}"
