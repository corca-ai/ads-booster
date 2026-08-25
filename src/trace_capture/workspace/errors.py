from __future__ import annotations

from typing import final, override


@final
class ScopedRecordNotFoundError(LookupError):
    def __init__(self, *, record_type: str, record_id: str) -> None:
        """Create an error that does not reveal which scope owns the record."""
        self.record_type = record_type
        self.record_id = record_id
        super().__init__(record_type, record_id)

    @override
    def __str__(self) -> str:
        return f"scoped {self.record_type} not found: {self.record_id}"


@final
class RevisionConflictError(RuntimeError):
    def __init__(self, *, record_type: str, record_id: str, expected_revision: int) -> None:
        """Create an optimistic-concurrency conflict with typed revision details."""
        self.record_type = record_type
        self.record_id = record_id
        self.expected_revision = expected_revision
        super().__init__(record_type, record_id, expected_revision)

    @override
    def __str__(self) -> str:
        return (
            f"stale {self.record_type} revision for {self.record_id}: "
            f"expected {self.expected_revision}"
        )


@final
class CandidateAlreadyReviewedError(RuntimeError):
    def __init__(self, *, record_id: str, status: str) -> None:
        """Create an error for a candidate that already left the review gate."""
        self.record_id = record_id
        self.status = status
        super().__init__(record_id, status)

    @override
    def __str__(self) -> str:
        return f"candidate is already reviewed: {self.record_id}"


@final
class WorkspaceStoreCorruptionError(RuntimeError):
    def __init__(self, *, record_type: str) -> None:
        """Create an error for a database row that fails its typed boundary."""
        self.record_type = record_type
        super().__init__(record_type)

    @override
    def __str__(self) -> str:
        return f"invalid {self.record_type} row in workspace store"


@final
class UnsafeAssetPathError(ValueError):
    def __init__(self, relative_path: str) -> None:
        """Create an error for a path that escapes the workspace asset root."""
        self.relative_path = relative_path
        super().__init__(relative_path)

    @override
    def __str__(self) -> str:
        return "asset path is outside the workspace assets directory"
