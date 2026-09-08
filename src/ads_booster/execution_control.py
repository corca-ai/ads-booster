"""Thread-scoped cooperative execution control, independent of channels and providers."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Generator


class ExecutionCancelledError(RuntimeError):
    """The owner requested cancellation before the next execution boundary."""


@dataclass(slots=True)
class ExecutionControl:
    cancelled: Callable[[], bool]
    stage: str = "답변을 준비하고 있습니다"

    def check(self) -> None:
        if self.cancelled():
            message = "execution_cancelled"
            raise ExecutionCancelledError(message)


_CURRENT: ContextVar[ExecutionControl | None] = ContextVar("trace_execution_control", default=None)


@contextmanager
def execution_scope(control: ExecutionControl) -> Generator[None]:
    token = _CURRENT.set(control)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def checkpoint(stage: str) -> None:
    control = _CURRENT.get()
    if control is not None:
        control.check()
        control.stage = stage


def controlled_process(
    command: list[str], prompt: str, timeout: float
) -> subprocess.CompletedProcess[str]:
    control = _CURRENT.get()
    if control is None:
        return subprocess.run(  # noqa: S603 - caller-owned argv, no shell.
            command, input=prompt, check=False, capture_output=True, text=True, timeout=timeout
        )
    control.check()
    started = time.monotonic()
    with subprocess.Popen(  # noqa: S603 - caller-owned argv, no shell.
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name == "posix",
    ) as process:
        pending_input: str | None = prompt
        try:
            while True:
                control.check()
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)  # noqa: TRY301 - reap in common cleanup.
                try:
                    stdout, stderr = process.communicate(
                        input=pending_input, timeout=min(0.25, remaining)
                    )
                    control.check()
                    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    pending_input = None
        except BaseException:
            # Reap only our own child; never kill a service or another user's process.
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            _ = process.communicate()
            raise
