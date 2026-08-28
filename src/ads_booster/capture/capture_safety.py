from __future__ import annotations

import fcntl
import os
import tempfile
import time
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, override

from ads_booster.contracts import ErrorCode

if TYPE_CHECKING:
    from collections.abc import Generator


class CaptureClock(Protocol):
    def monotonic(self) -> float: ...

    def time_ns(self) -> int: ...


@dataclass(frozen=True, slots=True)
class SystemCaptureClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def time_ns(self) -> int:
        return time.time_ns()


SYSTEM_CAPTURE_CLOCK: Final = SystemCaptureClock()


class CaptureSleeper(Protocol):
    def sleep(self, seconds: float) -> None: ...


@dataclass(frozen=True, slots=True)
class SystemCaptureSleeper:
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


SYSTEM_CAPTURE_SLEEPER: Final = SystemCaptureSleeper()
SYSTEM_PATH_ALIASES: Final = (
    (Path(os.sep, "var"), Path(os.sep, "private", "var")),
    (Path(os.sep, "tmp"), Path(os.sep, "private", "tmp")),
)


class CaptureAdapterError(Exception):
    """Typed capture failure raised at an adapter boundary."""

    code: ErrorCode
    message: str
    cleanup_error: str | None

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        cleanup_error: str | None = None,
    ) -> None:
        """Store the machine-readable failure and optional cleanup evidence."""
        super().__init__(message)
        self.code = code
        self.message = message
        self.cleanup_error = cleanup_error

    @override
    def __str__(self) -> str:
        return self.message

    def with_cleanup_error(self, cleanup_error: str) -> CaptureAdapterError:
        return CaptureAdapterError(
            code=self.code,
            message=self.message,
            cleanup_error=cleanup_error,
        )


@dataclass(frozen=True, slots=True)
class CaptureControl:
    expires_at: float
    cancel_file: Path | None
    clock: CaptureClock
    sleeper: CaptureSleeper = SYSTEM_CAPTURE_SLEEPER

    @classmethod
    def start(
        cls,
        timeout_seconds: float,
        cancel_file: Path | None = None,
        clock: CaptureClock = SYSTEM_CAPTURE_CLOCK,
    ) -> CaptureControl:
        if timeout_seconds <= 0:
            raise CaptureAdapterError(
                code=ErrorCode.CAPTURE_TIMED_OUT,
                message="capture timeout must be greater than zero",
            )
        return cls(
            expires_at=clock.monotonic() + timeout_seconds,
            cancel_file=cancel_file,
            clock=clock,
        )

    def remaining_seconds(self) -> float:
        if self.cancel_file is not None and self.cancel_file.exists():
            raise CaptureAdapterError(
                code=ErrorCode.CAPTURE_CANCELLED,
                message=f"capture cancelled by marker: {self.cancel_file}",
            )
        remaining = self.expires_at - self.clock.monotonic()
        if remaining <= 0:
            raise CaptureAdapterError(
                code=ErrorCode.CAPTURE_TIMED_OUT,
                message="capture deadline expired",
            )
        return remaining

    def checkpoint(self) -> None:
        _ = self.remaining_seconds()

    def wait(self, seconds: float) -> None:
        remaining = self.remaining_seconds()
        self.sleeper.sleep(min(seconds, remaining))
        self.checkpoint()


def path_has_symlink_component(path: Path) -> bool:
    current = path
    while True:
        try:
            if current.is_symlink() and not _is_system_path_alias(current):
                return True
        except OSError:
            return True
        if current.parent == current:
            return False
        current = current.parent


def _is_system_path_alias(path: Path) -> bool:
    for alias, canonical in SYSTEM_PATH_ALIASES:
        if path != alias:
            continue
        try:
            return path.resolve() == canonical
        except OSError:
            return False
    return False


class CaptureLeaseFactory(Protocol):
    def acquire(self, udid: str) -> AbstractContextManager[None]: ...


DEFAULT_CAPTURE_LEASE_ROOT: Final = Path(tempfile.gettempdir()) / "trace-marketing-udid-leases"


@dataclass(frozen=True, slots=True)
class UdidCaptureLeaseFactory:
    root: Path | None = None
    clock: CaptureClock = SYSTEM_CAPTURE_CLOCK

    def acquire(self, udid: str) -> AbstractContextManager[None]:
        return self._acquire(udid)

    @contextmanager
    def _acquire(self, udid: str) -> Generator[None]:
        root = self.root if self.root is not None else DEFAULT_CAPTURE_LEASE_ROOT
        path = root / f"{sha256(udid.encode()).hexdigest()}.lock"
        try:
            root.mkdir(parents=True, exist_ok=True)
            with path.open("a+", encoding="utf-8") as lease_file:
                try:
                    _ = fcntl.flock(lease_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise CaptureAdapterError(
                        code=ErrorCode.CAPTURE_LEASE_UNAVAILABLE,
                        message=f"capture lease is already held for simulator {udid}",
                    ) from error
                except OSError as error:
                    raise CaptureAdapterError(
                        code=ErrorCode.CAPTURE_LEASE_UNAVAILABLE,
                        message="simulator capture lease could not be acquired",
                    ) from error
                _ = lease_file.seek(0)
                _ = lease_file.write(
                    f"pid={os.getpid()}\nstarted_at_ns={self.clock.time_ns()}\n",
                )
                _ = lease_file.truncate()
                lease_file.flush()
                try:
                    yield
                finally:
                    _ = fcntl.flock(lease_file.fileno(), fcntl.LOCK_UN)
        except CaptureAdapterError:
            raise
        except OSError as error:
            raise CaptureAdapterError(
                code=ErrorCode.CAPTURE_LEASE_UNAVAILABLE,
                message="simulator capture lease storage is unavailable",
            ) from error


SimulatorLeaseManager = UdidCaptureLeaseFactory
