from __future__ import annotations

import fcntl
import threading
from typing import TYPE_CHECKING, Final, TextIO, final

from trace_capture.contracts.errors import TraceRunStoreIOError

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

JOURNAL_FILENAME: Final = "transitions.jsonl"
_THREAD_LOCKS: Final[dict[str, threading.Lock]] = {}
_THREAD_LOCKS_GUARD: Final = threading.Lock()


def _thread_lock_for(lock_path: Path) -> threading.Lock:
    key = str(lock_path.resolve())
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock


@final
class RunLock:
    __slots__ = ("_acquired", "_file", "_root", "_run_id", "_thread_lock", "lock_path")
    lock_path: Path
    _root: Path
    _run_id: str
    _thread_lock: threading.Lock
    _acquired: bool

    def __init__(self, root: Path, run_id: str) -> None:
        """Initialize a thread and process lock for one run journal."""
        self._root = root
        self.lock_path = root / run_id / ".lock"
        self._run_id = run_id
        self._thread_lock = _thread_lock_for(self.lock_path)
        self._acquired = False
        self._file: TextIO | None = None

    def __enter__(self) -> RunLock:
        """Acquire the thread and advisory file locks."""
        try:
            root = self._root.resolve()
        except OSError as error:
            raise TraceRunStoreIOError(run_id=self._run_id, operation="lock") from error
        if self._root.is_symlink() or (self._root.exists() and not self._root.is_dir()):
            raise TraceRunStoreIOError(run_id=self._run_id, operation="lock")
        run_path = self._root / self._run_id
        if run_path.is_symlink():
            raise TraceRunStoreIOError(run_id=self._run_id, operation="lock")
        run_dir = run_path.resolve()
        if not run_dir.is_relative_to(root):
            raise TraceRunStoreIOError(run_id=self._run_id, operation="lock")
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise TraceRunStoreIOError(run_id=self._run_id, operation="lock") from error
        if self.lock_path.is_symlink():
            raise TraceRunStoreIOError(run_id=self._run_id, operation="lock")
        try:
            _ = self._thread_lock.acquire()
            self._acquired = True
            self._file = self.lock_path.open("a+", encoding="utf-8")
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX)
        except OSError as error:
            self._release_after_failure()
            raise TraceRunStoreIOError(run_id=self._run_id, operation="lock") from error
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """Release the lock resources after a run operation."""
        _ = (exc_type, exc_value, traceback)
        try:
            if self._file is not None:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
                self._file.close()
        finally:
            self._file = None
            if self._acquired:
                self._thread_lock.release()
                self._acquired = False
        return False

    def _release_after_failure(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
        if self._acquired:
            self._thread_lock.release()
            self._acquired = False


@final
class StoreLock:
    __slots__ = ("_acquired", "_file", "_root", "_thread_lock", "lock_path")
    lock_path: Path
    _root: Path
    _thread_lock: threading.Lock
    _acquired: bool

    def __init__(self, root: Path) -> None:
        """Initialize the store-wide lock used for idempotency admission."""
        self._root = root
        self.lock_path = root / ".store.lock"
        self._thread_lock = _thread_lock_for(self.lock_path)
        self._acquired = False
        self._file: TextIO | None = None

    def __enter__(self) -> StoreLock:
        """Acquire the store-wide thread and advisory file locks."""
        if self._root.is_symlink() or (self._root.exists() and not self._root.is_dir()):
            raise TraceRunStoreIOError(run_id="<store>", operation="lock")
        try:
            self._root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise TraceRunStoreIOError(run_id="<store>", operation="lock") from error
        if self.lock_path.is_symlink():
            raise TraceRunStoreIOError(run_id="<store>", operation="lock")
        try:
            _ = self._thread_lock.acquire()
            self._acquired = True
            self._file = self.lock_path.open("a+", encoding="utf-8")
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX)
        except OSError as error:
            self._release_after_failure()
            raise TraceRunStoreIOError(run_id="<store>", operation="lock") from error
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """Release the store-wide lock resources after an operation."""
        _ = (exc_type, exc_value, traceback)
        try:
            if self._file is not None:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
                self._file.close()
        finally:
            self._file = None
            if self._acquired:
                self._thread_lock.release()
                self._acquired = False
        return False

    def _release_after_failure(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
        if self._acquired:
            self._thread_lock.release()
            self._acquired = False


__all__ = ["JOURNAL_FILENAME", "RunLock", "StoreLock"]
