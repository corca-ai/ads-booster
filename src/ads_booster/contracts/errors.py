from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from ads_booster.contracts.run import TraceRunState


@dataclass(frozen=True, slots=True)
class IdempotencyConflictError(Exception):
    run_id: str

    @override
    def __str__(self) -> str:
        return f"run_id already exists with a different input digest: {self.run_id}"


@dataclass(frozen=True, slots=True)
class InvalidTransitionError(Exception):
    current: TraceRunState
    next_state: TraceRunState

    @override
    def __str__(self) -> str:
        return f"transition from {self.current} to {self.next_state} is not allowed"


@dataclass(frozen=True, slots=True)
class ConcurrentTransitionError(RuntimeError):
    run_id: str

    @override
    def __str__(self) -> str:
        return f"run transition lost its compare-and-swap race: {self.run_id}"


@dataclass(frozen=True, slots=True)
class TraceRunStoreIOError(Exception):
    run_id: str
    operation: str

    @override
    def __str__(self) -> str:
        return f"trace run store {self.operation} failed: {self.run_id}"


@dataclass(frozen=True, slots=True)
class ArtifactIntegrityError(Exception):
    path: str
    reason: str

    @override
    def __str__(self) -> str:
        return f"artifact integrity check failed for {self.path}: {self.reason}"


@dataclass(frozen=True, slots=True)
class InvalidRunJournalError(Exception):
    run_id: str

    @override
    def __str__(self) -> str:
        return f"run journal is unreadable: {self.run_id}"


__all__ = [
    "ArtifactIntegrityError",
    "ConcurrentTransitionError",
    "IdempotencyConflictError",
    "InvalidRunJournalError",
    "InvalidTransitionError",
    "TraceRunStoreIOError",
]
