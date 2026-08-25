from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Protocol, final

from pydantic import BaseModel, ConfigDict

from trace_capture.agent.context import CompactionSummary, ContextTrigger

if TYPE_CHECKING:
    from pathlib import Path


@final
class MemoryStoreError(RuntimeError):
    operation: str
    path: str

    def __init__(self, operation: str, path: str) -> None:
        self.operation = operation
        self.path = path
        super().__init__(f"memory {operation} failed: {path}")


class MemoryStore(Protocol):
    def flush(self, summary: CompactionSummary) -> None: ...

    def latest(self) -> CompactionSummary | None: ...


class NullMemoryStore:
    def flush(self, summary: CompactionSummary) -> None:
        _ = summary

    def latest(self) -> CompactionSummary | None:
        return None


class _MemoryRecord(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    summary_id: str
    source_start: int
    source_end: int
    source_digest: str
    text: str
    trigger: str

    @classmethod
    def from_summary(cls, summary: CompactionSummary) -> _MemoryRecord:
        return cls(
            summary_id=summary.summary_id,
            source_start=summary.source_start,
            source_end=summary.source_end,
            source_digest=summary.source_digest,
            text=summary.text,
            trigger=summary.trigger.value,
        )

    def to_summary(self) -> CompactionSummary:
        return CompactionSummary(
            summary_id=self.summary_id,
            source_start=self.source_start,
            source_end=self.source_end,
            source_digest=self.source_digest,
            text=self.text,
            trigger=ContextTrigger(self.trigger),
        )


@final
class JsonlMemoryStore:
    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path

    def flush(self, summary: CompactionSummary) -> None:
        record = _MemoryRecord.from_summary(summary)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                _ = stream.write(record.model_dump_json() + "\n")
        except OSError as error:
            operation = "flush"
            raise MemoryStoreError(operation, str(self.path)) from error

    def latest(self) -> CompactionSummary | None:
        if not self.path.is_file():
            return None
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            if not lines:
                return None
            return _MemoryRecord.model_validate_json(lines[-1]).to_summary()
        except (OSError, UnicodeError, ValueError) as error:
            operation = "read"
            raise MemoryStoreError(operation, str(self.path)) from error
