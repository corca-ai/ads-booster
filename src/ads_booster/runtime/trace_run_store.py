from __future__ import annotations

# pyright: reportUnnecessaryComparison=false
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, NoReturn

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ads_booster.contracts.errors import (
    ArtifactIntegrityError,
    ConcurrentTransitionError,
    IdempotencyConflictError,
    InvalidRunJournalError,
    InvalidTransitionError,
    TraceRunStoreIOError,
)
from ads_booster.contracts.run import (
    TraceRunCapability,
    TraceRunEvent,
    TraceRunFailure,
    TraceRunRequest,
    TraceRunState,
)
from ads_booster.runtime.trace_run_artifacts import prepare_artifact, request_digest
from ads_booster.runtime.trace_run_lock import JOURNAL_FILENAME, RunLock, StoreLock
from ads_booster.runtime.trace_run_replay import (
    transition_is_allowed,
    validate_history,
    validate_replay,
)

if TYPE_CHECKING:
    from ads_booster.contracts import CaptureProvenance


class _RunIdentity(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    run_id: str
    idempotency_key: str
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class TraceRunRecord:
    run_id: str
    idempotency_key: str
    input_digest: str
    events: tuple[TraceRunEvent, ...]
    resumed: bool = False

    @property
    def state(self) -> TraceRunState:
        return self.events[-1].state

    @property
    def failure(self) -> TraceRunFailure | None:
        return self.events[-1].failure

    @property
    def awaiting_capability(self) -> TraceRunCapability | None:
        return self.events[-1].capability

    @property
    def captured_artifact(self) -> Path | None:
        for event in reversed(self.events):
            if event.component_artifact is not None:
                return Path(event.component_artifact)
        return None

    @property
    def captured_artifact_sha256(self) -> str | None:
        for event in reversed(self.events):
            if event.component_artifact_sha256 is not None:
                return event.component_artifact_sha256
        return None

    @property
    def capture_provenance(self) -> CaptureProvenance | None:
        for event in reversed(self.events):
            if event.capture_provenance is not None:
                return event.capture_provenance
        return None

    @property
    def completed_capabilities(self) -> tuple[TraceRunCapability, ...]:
        completed: list[TraceRunCapability] = []
        for earlier, later in zip(self.events, self.events[1:], strict=False):
            if (
                earlier.state is TraceRunState.AWAITING_TOOL
                and later.state is TraceRunState.RUNNING
                and earlier.capability is not None
            ):
                completed.append(earlier.capability)
        return tuple(completed)


@dataclass(frozen=True, slots=True)
class JsonlTraceRunStore:
    root: Path

    def begin(self, request: TraceRunRequest) -> TraceRunRecord:
        digest = request_digest(request)
        with StoreLock(root=self.root), RunLock(root=self.root, run_id=request.run_id):
            events = self._read_events(request.run_id)
            self._reject_idempotency_reuse(request, digest)
            if events:
                validate_replay(events, request, digest)
                return _record_for(request, digest, events, resumed=True)
            queued = TraceRunEvent(
                run_id=request.run_id,
                idempotency_key=request.idempotency_key,
                input_digest=digest,
                sequence=0,
                recorded_at=datetime.now(UTC),
                state=TraceRunState.QUEUED,
            )
            self._append(queued)
            return _record_for(request, digest, (queued,))
        raise AssertionError

    def transition(  # noqa: PLR0913, PLR0917
        self,
        record: TraceRunRecord,
        state: TraceRunState,
        capability: TraceRunCapability | None = None,
        failure: TraceRunFailure | None = None,
        component_artifact: Path | None = None,
        output_image: Path | None = None,
        capture_provenance: CaptureProvenance | None = None,
    ) -> TraceRunRecord:
        with RunLock(root=self.root, run_id=record.run_id):
            current_events = self._read_events(record.run_id)
            if current_events != record.events:
                raise ConcurrentTransitionError(run_id=record.run_id)
            validate_history(current_events, record.run_id)
            if not transition_is_allowed(record.state, state):
                raise InvalidTransitionError(current=record.state, next_state=state)
            component_path, component_digest = prepare_artifact(component_artifact)
            output_path, output_digest = prepare_artifact(output_image)
            event = TraceRunEvent(
                run_id=record.run_id,
                idempotency_key=record.idempotency_key,
                input_digest=record.input_digest,
                sequence=len(current_events),
                recorded_at=datetime.now(UTC),
                state=state,
                capability=capability,
                failure=failure,
                component_artifact=component_path,
                component_artifact_sha256=component_digest,
                output_image=output_path,
                output_image_sha256=output_digest,
                capture_provenance=capture_provenance,
            )
            validate_history((*current_events, event), record.run_id)
            self._append(event)
            return TraceRunRecord(
                run_id=record.run_id,
                idempotency_key=record.idempotency_key,
                input_digest=record.input_digest,
                events=(*current_events, event),
            )
        raise AssertionError

    def _read_events(self, run_id: str) -> tuple[TraceRunEvent, ...]:
        lines = self._read_journal_lines(run_id)
        try:
            return tuple(TraceRunEvent.model_validate_json(line) for line in lines)
        except ValidationError as error:
            raise InvalidRunJournalError(run_id=run_id) from error

    def _read_identity(self, run_id: str) -> _RunIdentity | None:
        lines = self._read_journal_lines(run_id)
        if not lines:
            return None
        try:
            return _RunIdentity.model_validate_json(lines[0])
        except ValidationError as error:
            raise InvalidRunJournalError(run_id=run_id) from error

    def _read_journal_lines(self, run_id: str) -> tuple[str, ...]:
        journal = self.root / run_id / JOURNAL_FILENAME
        if journal.is_symlink():
            raise InvalidRunJournalError(run_id=run_id)
        if not journal.is_file():
            return ()
        try:
            lines = tuple(journal.read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeError) as error:
            raise InvalidRunJournalError(run_id=run_id) from error
        if not lines:
            _raise_empty_journal(run_id)
        return lines

    def _append(self, event: TraceRunEvent) -> None:
        journal = self.root / event.run_id / JOURNAL_FILENAME
        try:
            if journal.is_symlink():
                raise TraceRunStoreIOError(run_id=event.run_id, operation="append")
            journal.parent.mkdir(parents=True, exist_ok=True)
            payload = f"{event.model_dump_json()}\n"
            with journal.open("a", encoding="utf-8") as output:
                _ = output.write(payload)
                output.flush()
                os.fsync(output.fileno())
        except OSError as error:
            raise TraceRunStoreIOError(run_id=event.run_id, operation="append") from error

    def _reject_idempotency_reuse(self, request: TraceRunRequest, digest: str) -> None:
        try:
            run_directories = tuple(self.root.iterdir())
        except OSError as error:
            raise TraceRunStoreIOError(run_id="<store>", operation="scan") from error
        for run_directory in run_directories:
            if run_directory.is_symlink():
                raise TraceRunStoreIOError(run_id=run_directory.name, operation="scan")
            if not run_directory.is_dir():
                continue
            identity = self._read_identity(run_directory.name)
            if identity is None:
                continue
            if identity.idempotency_key == request.idempotency_key and (
                identity.run_id != request.run_id or identity.input_digest != digest
            ):
                raise IdempotencyConflictError(run_id=request.run_id)


def _raise_empty_journal(run_id: str) -> NoReturn:
    raise InvalidRunJournalError(run_id=run_id)


def _record_for(
    request: TraceRunRequest,
    input_digest: str,
    events: tuple[TraceRunEvent, ...],
    resumed: bool = False,
) -> TraceRunRecord:
    return TraceRunRecord(
        run_id=request.run_id,
        idempotency_key=request.idempotency_key,
        input_digest=input_digest,
        events=events,
        resumed=resumed,
    )


__all__ = [
    "JOURNAL_FILENAME",
    "ArtifactIntegrityError",
    "ConcurrentTransitionError",
    "IdempotencyConflictError",
    "InvalidRunJournalError",
    "InvalidTransitionError",
    "JsonlTraceRunStore",
    "RunLock",
    "StoreLock",
    "TraceRunRecord",
    "TraceRunStoreIOError",
    "prepare_artifact",
    "request_digest",
    "transition_is_allowed",
]
