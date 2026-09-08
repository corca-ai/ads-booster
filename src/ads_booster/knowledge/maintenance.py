from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import IO, Literal


class KnowledgeOwnerBusyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OwnerStatus:
    state: Literal["unowned", "live", "stale", "invalid"]
    owner_id: str | None = None
    pid: int | None = None
    heartbeat_at: datetime | None = None


@dataclass(slots=True)
class KnowledgeOwner:
    root: Path
    owner_id: str
    stale_after: timedelta = timedelta(seconds=15)
    _stream: IO[str] | None = field(default=None, init=False, repr=False)

    @property
    def lock_path(self) -> Path:
        return self.root / ".knowledge-owner.lock"

    @property
    def heartbeat_path(self) -> Path:
        return self.root / ".knowledge-owner.json"

    def acquire(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)
        stream = self.lock_path.open("a+", encoding="utf-8")
        os.chmod(self.lock_path, 0o600)
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            stream.close()
            raise KnowledgeOwnerBusyError("owner_busy") from error
        self._stream = stream
        self.heartbeat()

    def heartbeat(self, *, now: datetime | None = None) -> None:
        if self._stream is None:
            raise RuntimeError("owner_not_acquired")
        payload = {
            "schema": "trace.knowledge-owner.v1",
            "owner_id": self.owner_id,
            "pid": os.getpid(),
            "heartbeat_at": (now or datetime.now(UTC)).isoformat(),
        }
        temporary = self.heartbeat_path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, sort_keys=True, separators=(",", ":"))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, self.heartbeat_path)
        os.chmod(self.heartbeat_path, 0o600)

    def release(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        self.heartbeat_path.unlink(missing_ok=True)
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()

    def __enter__(self) -> KnowledgeOwner:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


@dataclass(slots=True)
class KnowledgeActivity:
    accepting: bool = True
    active: dict[str, int] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock, repr=False)

    def claim(self, kind: str) -> bool:
        with self.lock:
            if not self.accepting:
                return False
            self.active[kind] = self.active.get(kind, 0) + 1
            return True

    def finish(self, kind: str) -> None:
        with self.lock:
            current = self.active.get(kind, 0)
            if current <= 1:
                self.active.pop(kind, None)
            else:
                self.active[kind] = current - 1

    def begin_maintenance(self) -> None:
        with self.lock:
            self.accepting = False

    def begin_exclusive(self, kind: str) -> bool:
        with self.lock:
            self.accepting = False
            if self.active:
                return False
            self.active[kind] = 1
            return True

    def finish_exclusive(self, kind: str) -> None:
        with self.lock:
            self.active.pop(kind, None)
            self.accepting = True

    def resume(self) -> None:
        with self.lock:
            self.accepting = True

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            return {"accepting": self.accepting, "active": dict(sorted(self.active.items()))}

    def has_active_work(self) -> bool:
        with self.lock:
            return bool(self.active)


def inspect_owner(root: Path, *, now: datetime | None = None) -> OwnerStatus:
    path = root / ".knowledge-owner.json"
    if not path.exists():
        return OwnerStatus("unowned")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        heartbeat = datetime.fromisoformat(str(payload["heartbeat_at"]))
        owner_id = str(payload["owner_id"])
        pid = int(payload["pid"])
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return OwnerStatus("invalid")
    instant = now or datetime.now(UTC)
    state: Literal["live", "stale"] = "live" if instant - heartbeat <= timedelta(seconds=15) else "stale"
    return OwnerStatus(state, owner_id, pid, heartbeat)


__all__ = [
    "KnowledgeActivity",
    "KnowledgeOwner",
    "KnowledgeOwnerBusyError",
    "OwnerStatus",
    "inspect_owner",
]
