from __future__ import annotations

import contextlib
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Final, Protocol, final, override
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trace_capture.transport.json_types import JsonObject as _JsonObject

if TYPE_CHECKING:
    from collections.abc import Sequence

    from trace_capture.agent.session import AgentSession
    from trace_capture.transport.json_types import JsonObject

_SESSION_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DEFAULT_TITLE: Final = "New session"


@dataclass(frozen=True, slots=True)
class SessionInfo:
    session_id: str
    title: str
    created_at: float
    updated_at: float
    message_count: int


class SessionRecord(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    session_id: str = Field(min_length=1, max_length=64, pattern=_SESSION_ID_PATTERN.pattern)
    title: str = Field(min_length=1, max_length=80)
    created_at: float
    updated_at: float
    history: tuple[JsonObject, ...]

    @classmethod
    def create(
        cls,
        session_id: str,
        history: Sequence[JsonObject],
        *,
        created_at: float | None = None,
        updated_at: float | None = None,
    ) -> SessionRecord:
        now = time.time()
        created = now if created_at is None else created_at
        updated = now if updated_at is None else updated_at
        return cls(
            session_id=session_id,
            title=_title_from_history(history),
            created_at=created,
            updated_at=updated,
            history=tuple(history),
        )

    @property
    def info(self) -> SessionInfo:
        return SessionInfo(
            session_id=self.session_id,
            title=self.title,
            created_at=self.created_at,
            updated_at=self.updated_at,
            message_count=sum(
                1 for entry in self.history if entry.get("role") in {"user", "assistant"}
            ),
        )


_ = SessionRecord.model_rebuild(_types_namespace={"JsonObject": _JsonObject})


class SessionStore(Protocol):
    def list_sessions(self) -> tuple[SessionInfo, ...]: ...

    def load(self, session_id: str) -> SessionRecord | None: ...

    def save(self, record: SessionRecord) -> None: ...

    def delete(self, session_id: str) -> None: ...


@final
class SessionStoreError(RuntimeError):
    operation: str
    path: str

    def __init__(self, operation: str, path: str) -> None:
        """Create an error for a failed session-store operation."""
        self.operation = operation
        self.path = path
        super().__init__(operation, path)

    @override
    def __str__(self) -> str:
        return f"session store {self.operation} failed: {self.path}"


@final
class SessionNotFoundError(RuntimeError):
    session_id: str

    def __init__(self, session_id: str) -> None:
        """Create an error for a missing persisted session."""
        self.session_id = session_id
        super().__init__(session_id)

    @override
    def __str__(self) -> str:
        return f"session not found: {self.session_id}"


@final
class NullSessionStore:
    def list_sessions(self) -> tuple[SessionInfo, ...]:
        return ()

    def load(self, session_id: str) -> SessionRecord | None:
        _ = session_id
        return None

    def save(self, record: SessionRecord) -> None:
        _ = record

    def delete(self, session_id: str) -> None:
        _ = session_id


@dataclass(frozen=True, slots=True)
class JsonSessionStore:
    root: Path

    def list_sessions(self) -> tuple[SessionInfo, ...]:
        if not self.root.is_dir():
            return ()
        records: list[SessionInfo] = []
        try:
            paths = tuple(self.root.glob("*.json"))
        except OSError as error:
            operation = "list"
            raise SessionStoreError(operation, str(self.root)) from error
        for path in paths:
            record = self._read(path)
            if record is not None:
                records.append(record.info)
        records.sort(key=lambda item: (item.updated_at, item.session_id), reverse=True)
        return tuple(records)

    def load(self, session_id: str) -> SessionRecord | None:
        path = self._path_for(session_id)
        return self._read(path)

    def save(self, record: SessionRecord) -> None:
        path = self._path_for(record.session_id)
        temporary_path: Path | None = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.root,
                prefix=f".{record.session_id}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                _ = stream.write(record.model_dump_json())
                _ = stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            _ = temporary_path.replace(path)
            temporary_path = None
            path.chmod(0o600)
        except OSError as error:
            if temporary_path is not None:
                with contextlib.suppress(OSError):
                    temporary_path.unlink()
            operation = "save"
            raise SessionStoreError(operation, str(path)) from error

    def delete(self, session_id: str) -> None:
        path = self._path_for(session_id)
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            operation = "delete"
            raise SessionStoreError(operation, str(path)) from error

    def _path_for(self, session_id: str) -> Path:
        if not _SESSION_ID_PATTERN.fullmatch(session_id):
            operation = "validate"
            raise SessionStoreError(operation, session_id)
        return self.root / f"{session_id}.json"

    def _read(self, path: Path) -> SessionRecord | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError) as error:
            operation = "read"
            raise SessionStoreError(operation, str(path)) from error
        try:
            return SessionRecord.model_validate_json(raw)
        except ValidationError as error:
            operation = "validate"
            raise SessionStoreError(operation, str(path)) from error


def new_session_id() -> str:
    return uuid4().hex[:12]


@dataclass(slots=True)  # noqa: MUTABLE_OK
class SessionManager:
    session: AgentSession
    store: SessionStore
    session_id: str = field(default_factory=new_session_id)
    created_at: float = field(default_factory=time.time)

    def save(self) -> None:
        if not self.session.history:
            return
        current = SessionRecord.create(
            self.session_id,
            self.session.history,
            created_at=self.created_at,
        )
        self.store.save(current)

    def new(self) -> SessionInfo:
        self.save()
        self._replace(new_session_id(), time.time(), ())
        return self.info()

    def clear(self) -> SessionInfo:
        self.store.delete(self.session_id)
        self._replace(new_session_id(), time.time(), ())
        return self.info()

    def available(self) -> tuple[SessionInfo, ...]:
        return tuple(
            info for info in self.store.list_sessions() if info.session_id != self.session_id
        )

    def resume(self, session_id: str) -> SessionInfo:
        self.save()
        record = self.store.load(session_id)
        if record is None:
            raise SessionNotFoundError(session_id)
        self._replace(record.session_id, record.created_at, record.history)
        return record.info

    def info(self) -> SessionInfo:
        return SessionRecord.create(
            self.session_id,
            self.session.history,
            created_at=self.created_at,
            updated_at=time.time(),
        ).info

    def _replace(
        self,
        session_id: str,
        created_at: float,
        history: Sequence[JsonObject],
    ) -> None:
        self.session = self.session.fork(history)
        self.session_id = session_id
        self.created_at = created_at


def _title_from_history(history: Sequence[JsonObject]) -> str:
    for entry in history:
        if entry.get("role") != "user":
            continue
        content = entry.get("content")
        if not isinstance(content, str):
            continue
        title = " ".join(content.split())
        if title:
            return title[:80]
    return _DEFAULT_TITLE
