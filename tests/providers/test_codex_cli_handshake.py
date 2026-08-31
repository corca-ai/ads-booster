from __future__ import annotations

import json
import os
import stat
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from ads_booster.providers.codex_cli import (
    CodexAppiumJobCallbacks,
    CodexAppiumReadyState,
    CodexAppiumSavedState,
    CodexCli,
)
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonValue

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


def _write_ready_marker(
    workspace: Path,
    *,
    rendered_trace_item_titles: list[str],
    session_id: str = "appium-1",
) -> None:
    path = workspace / "codex-appium-ready.json"
    temporary = workspace / ".codex-appium-ready.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(
            {
                "schema": "trace.codex-appium-ready.v1",
                "session_id": session_id,
                "rendered_trace_item_titles": rendered_trace_item_titles,
            },
            stream,
        )
    _ = temporary.replace(path)


def _write_saved_marker(
    workspace: Path,
    *,
    mode: int = 0o600,
    session_id: str = "appium-1",
) -> None:
    path = workspace / "codex-appium-saved.json"
    temporary = workspace / ".codex-appium-saved.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    os.fchmod(descriptor, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(
            {
                "schema": "trace.codex-appium-saved.v1",
                "session_id": session_id,
            },
            stream,
        )
    _ = temporary.replace(path)


def _write_ready_and_wait_for_ack(workspace: Path) -> None:
    _write_ready_marker(workspace, rendered_trace_item_titles=["Focus block"])
    acknowledgement = workspace / "codex-appium-ready-verified.json"
    deadline = time.monotonic() + 1
    while not acknowledgement.exists() and time.monotonic() < deadline:
        time.sleep(0.001)
    assert acknowledgement.exists()


def test_codex_cli_acknowledges_failed_collection_before_process_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "job"
    workspace.mkdir()
    events: list[str] = []

    def run(command: list[str], **_kwargs: JsonValue) -> subprocess.CompletedProcess[str]:
        _write_ready_and_wait_for_ack(workspace)
        _write_saved_marker(workspace)
        acknowledgement = workspace / "codex-appium-collected.json"
        deadline = time.monotonic() + 1
        while not acknowledgement.exists() and time.monotonic() < deadline:
            time.sleep(0.001)
        assert acknowledgement.exists()
        events.append("cleanup")
        output_path = Path(command[command.index("--output-last-message") + 1])
        _ = output_path.write_text('{"status":"completed"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("ads_booster.providers.codex_cli.subprocess.run", run)

    def collect(_saved: CodexAppiumSavedState) -> bool:
        events.append("collect_failed")
        return False

    _ = CodexCli(tmp_path / "codex").run_appium_job(
        "Operate Trace",
        {"type": "object"},
        workspace=workspace,
        timeout_seconds=1,
        callbacks=CodexAppiumJobCallbacks(
            on_ready=lambda _ready: True,
            on_saved=collect,
        ),
    )

    assert events == ["collect_failed", "cleanup"]
    acknowledgement = _JSON_OBJECT.validate_json(
        (workspace / "codex-appium-collected.json").read_text(encoding="utf-8")
    )
    assert acknowledgement["collection_succeeded"] is False


def test_codex_cli_verifies_live_editor_before_save_and_collection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given one Codex subprocess configures the editor before saving
    workspace = tmp_path / "job"
    workspace.mkdir()
    events: list[str] = []

    def run(command: list[str], **_kwargs: JsonValue) -> subprocess.CompletedProcess[str]:
        events.append("ready")
        _write_ready_marker(workspace, rendered_trace_item_titles=["Focus block"])
        verified = workspace / "codex-appium-ready-verified.json"
        deadline = time.monotonic() + 1
        while not verified.exists() and time.monotonic() < deadline:
            time.sleep(0.001)
        assert verified.exists()
        acknowledgement = _JSON_OBJECT.validate_json(verified.read_text(encoding="utf-8"))
        assert acknowledgement["ready_verified"] is True
        events.append("save")
        _write_saved_marker(workspace)
        collected = workspace / "codex-appium-collected.json"
        while not collected.exists() and time.monotonic() < deadline:
            time.sleep(0.001)
        assert collected.exists()
        events.append("cleanup")
        output_path = Path(command[command.index("--output-last-message") + 1])
        _ = output_path.write_text('{"status":"completed"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("ads_booster.providers.codex_cli.subprocess.run", run)

    def verify_ready(ready: CodexAppiumReadyState) -> bool:
        assert ready.rendered_trace_item_titles == ("Focus block",)
        events.append("verified")
        return True

    def collect(_saved: CodexAppiumSavedState) -> bool:
        events.append("collect")
        return True

    _ = CodexCli(tmp_path / "codex").run_appium_job(
        "Operate Trace",
        {"type": "object"},
        workspace=workspace,
        timeout_seconds=1,
        callbacks=CodexAppiumJobCallbacks(
            on_ready=verify_ready,
            on_saved=collect,
        ),
    )

    assert events == ["ready", "verified", "save", "collect", "cleanup"]
    assert stat.S_IMODE((workspace / "codex-appium-ready-verified.json").stat().st_mode) == 0o600


def test_codex_cli_rejected_ready_state_never_accepts_a_saved_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the worker rejects the live editor state
    workspace = tmp_path / "job"
    workspace.mkdir()
    saved_callback_called = False

    def run(command: list[str], **_kwargs: JsonValue) -> subprocess.CompletedProcess[str]:
        verified = workspace / "codex-appium-ready-verified.json"

        for attempt, session_id in enumerate(("unbound-1", "unbound-2"), start=1):
            _write_ready_marker(
                workspace,
                rendered_trace_item_titles=[],
                session_id=session_id,
            )
            deadline = time.monotonic() + 1
            acknowledgement: JsonObject | None = None
            while time.monotonic() < deadline:
                if verified.exists():
                    candidate = _JSON_OBJECT.validate_json(verified.read_text(encoding="utf-8"))
                    if candidate.get("session_id") == session_id:
                        acknowledgement = candidate
                        break
                time.sleep(0.001)
            assert acknowledgement is not None
            assert acknowledgement["ready_verified"] is False
            assert acknowledgement["attempt"] == attempt
            assert acknowledgement["retry_allowed"] is (attempt == 1)
        assert not (workspace / "codex-appium-saved.json").exists()
        output_path = Path(command[command.index("--output-last-message") + 1])
        _ = output_path.write_text('{"status":"failed"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("ads_booster.providers.codex_cli.subprocess.run", run)

    def on_saved(_saved: CodexAppiumSavedState) -> bool:
        nonlocal saved_callback_called
        saved_callback_called = True
        return True

    _ = CodexCli(tmp_path / "codex").run_appium_job(
        "Operate Trace",
        {"type": "object"},
        workspace=workspace,
        timeout_seconds=1,
        callbacks=CodexAppiumJobCallbacks(
            on_ready=lambda _ready: False,
            on_saved=on_saved,
        ),
    )

    assert saved_callback_called is False


def test_codex_cli_retries_rejected_ready_with_new_bound_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the first Trace session lost its launch binding before Ready
    workspace = tmp_path / "job"
    workspace.mkdir()
    ready_sessions: list[str] = []
    collected_sessions: list[str] = []

    def wait_for_verified_session(session_id: str) -> JsonObject:
        verified = workspace / "codex-appium-ready-verified.json"
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if verified.exists():
                payload = _JSON_OBJECT.validate_json(verified.read_text(encoding="utf-8"))
                if payload.get("session_id") == session_id:
                    return payload
            time.sleep(0.001)
        message = f"missing ready verification for {session_id}"
        raise AssertionError(message)

    def run(command: list[str], **_kwargs: JsonValue) -> subprocess.CompletedProcess[str]:
        _write_ready_marker(
            workspace,
            rendered_trace_item_titles=["Focus block"],
            session_id="unbound-session",
        )
        first = wait_for_verified_session("unbound-session")
        assert first["ready_verified"] is False
        assert first["retry_allowed"] is True
        assert first["attempt"] == 1

        _write_ready_marker(
            workspace,
            rendered_trace_item_titles=["Focus block"],
            session_id="bound-session",
        )
        second = wait_for_verified_session("bound-session")
        assert second["ready_verified"] is True
        assert second["retry_allowed"] is False
        assert second["attempt"] == 2

        _write_saved_marker(
            workspace,
            session_id="bound-session",
        )
        collected = workspace / "codex-appium-collected.json"
        deadline = time.monotonic() + 2
        while not collected.exists() and time.monotonic() < deadline:
            time.sleep(0.001)
        assert collected.exists()
        output_path = Path(command[command.index("--output-last-message") + 1])
        _ = output_path.write_text('{"status":"completed"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("ads_booster.providers.codex_cli.subprocess.run", run)

    def verify_ready(ready: CodexAppiumReadyState) -> bool:
        ready_sessions.append(ready.session_id)
        return ready.session_id == "bound-session"

    def collect(saved: CodexAppiumSavedState) -> bool:
        collected_sessions.append(saved.session_id)
        return True

    # When Codex replaces the rejected marker with a newly bound session
    _ = CodexCli(tmp_path / "codex").run_appium_job(
        "Operate Trace",
        {"type": "object"},
        workspace=workspace,
        timeout_seconds=3,
        callbacks=CodexAppiumJobCallbacks(
            on_ready=verify_ready,
            on_saved=collect,
        ),
    )

    # Then only the second session reaches Save and native collection
    assert ready_sessions == ["unbound-session", "bound-session"]
    assert collected_sessions == ["bound-session"]


def test_codex_cli_rejects_insecure_saved_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "job"
    workspace.mkdir()

    def run(command: list[str], **_kwargs: JsonValue) -> subprocess.CompletedProcess[str]:
        _write_ready_and_wait_for_ack(workspace)
        _write_saved_marker(workspace, mode=0o644)
        time.sleep(0.05)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("ads_booster.providers.codex_cli.subprocess.run", run)

    with pytest.raises(RuntimeError, match="codex_appium_job_saved_marker_invalid"):
        _ = CodexCli(tmp_path / "codex").run_appium_job(
            "Operate Trace",
            {"type": "object"},
            workspace=workspace,
            timeout_seconds=1,
            callbacks=CodexAppiumJobCallbacks(
                on_ready=lambda _ready: True,
                on_saved=lambda _saved: True,
            ),
        )


def test_codex_cli_fails_when_process_exits_without_saved_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "job"
    workspace.mkdir()

    def run(command: list[str], **_kwargs: JsonValue) -> subprocess.CompletedProcess[str]:
        _write_ready_and_wait_for_ack(workspace)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("ads_booster.providers.codex_cli.subprocess.run", run)

    with pytest.raises(RuntimeError, match="codex_appium_job_saved_marker_missing"):
        _ = CodexCli(tmp_path / "codex").run_appium_job(
            "Operate Trace",
            {"type": "object"},
            workspace=workspace,
            timeout_seconds=1,
            callbacks=CodexAppiumJobCallbacks(
                on_ready=lambda _ready: True,
                on_saved=lambda _saved: True,
            ),
        )


def test_codex_cli_maps_subprocess_timeout_while_waiting_for_saved_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "job"
    workspace.mkdir()

    def run(command: list[str], **_kwargs: JsonValue) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 0.01)

    monkeypatch.setattr("ads_booster.providers.codex_cli.subprocess.run", run)

    with pytest.raises(RuntimeError, match="codex_appium_job_timed_out"):
        _ = CodexCli(tmp_path / "codex").run_appium_job(
            "Operate Trace",
            {"type": "object"},
            workspace=workspace,
            timeout_seconds=0.01,
            callbacks=CodexAppiumJobCallbacks(
                on_ready=lambda _ready: True,
                on_saved=lambda _saved: True,
            ),
        )


def test_codex_cli_rejects_process_exit_before_collection_acknowledgement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "job"
    workspace.mkdir()
    collection_started = threading.Event()
    process_finished = threading.Event()

    def run(command: list[str], **_kwargs: JsonValue) -> subprocess.CompletedProcess[str]:
        _write_ready_and_wait_for_ack(workspace)
        _write_saved_marker(workspace)
        assert collection_started.wait(timeout=1)
        process_finished.set()
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("ads_booster.providers.codex_cli.subprocess.run", run)

    def collect(_saved: CodexAppiumSavedState) -> bool:
        collection_started.set()
        assert process_finished.wait(timeout=1)
        time.sleep(0.01)
        return True

    with pytest.raises(RuntimeError, match="codex_appium_job_exited_before_collection_ack"):
        _ = CodexCli(tmp_path / "codex").run_appium_job(
            "Operate Trace",
            {"type": "object"},
            workspace=workspace,
            timeout_seconds=1,
            callbacks=CodexAppiumJobCallbacks(
                on_ready=lambda _ready: True,
                on_saved=collect,
            ),
        )

    assert not (workspace / "codex-appium-collected.json").exists()
