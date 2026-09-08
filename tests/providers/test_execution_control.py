from __future__ import annotations

import subprocess
import sys
import time
from threading import Event, Thread
from typing import TYPE_CHECKING

import pytest

from ads_booster.execution_control import (
    ExecutionCancelledError,
    ExecutionControl,
    controlled_process,
    execution_scope,
)
from ads_booster.providers.codex_cli import CodexCli
from ads_booster.providers.codex_reasoning import CodexReasoningProvider
from tests.providers.test_codex_reasoning import _request  # pyright: ignore[reportPrivateUsage]

if TYPE_CHECKING:
    from pathlib import Path


def test_cancellation_kills_and_reaps_only_the_owned_subprocess(tmp_path: Path) -> None:
    marker = tmp_path / "started"
    cancelled = Event()
    outcome: list[str] = []

    executable = tmp_path / "codex"
    _ = executable.write_text(
        "\n".join(
            (
                f"#!{sys.executable}",
                "import pathlib,time",
                f"pathlib.Path({str(marker)!r}).write_text('started')",
                "time.sleep(60)",
                "",
            )
        )
    )
    executable.chmod(0o700)
    provider = CodexReasoningProvider(CodexCli(executable), tmp_path / "reasoning", "fixture")

    def run() -> None:
        with execution_scope(ExecutionControl(cancelled.is_set)):
            try:
                _ = provider.plan(_request())
            except ExecutionCancelledError:
                outcome.append("cancelled")

    worker = Thread(target=run)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        cancelled.set()
        worker.join(3)
        assert not worker.is_alive()
        assert outcome == ["cancelled"]
        # Another invocation remains usable after cancellation scope is removed.
        completed = controlled_process([sys.executable, "-c", "print('next')"], "", 3)
        assert completed.returncode == 0
        assert completed.stdout.strip() == "next"
    finally:
        cancelled.set()
        worker.join(3)


def test_controlled_process_retains_timeout_behavior() -> None:
    with execution_scope(ExecutionControl(lambda: False)), pytest.raises(subprocess.TimeoutExpired):
        _ = controlled_process([sys.executable, "-c", "import time; time.sleep(60)"], "", 0.1)
