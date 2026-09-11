# pyright: reportPrivateUsage=false
"""Synthetic app-server and materialized-image checks; never call a live image model."""

from __future__ import annotations

import base64
import hashlib
import io
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pytest
from PIL import Image
from pydantic import TypeAdapter

from ads_booster.providers.codex_image_edit import (
    ImageEditProcessRequest,
    ImageEditProcessResult,
    _StreamState,
    image_edit_command,
)
from ads_booster.providers.codex_trace_post import CodexTracePostProvider
from ads_booster.transport.json_types import JsonObject

_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


def _png_bytes(color: str = "blue") -> bytes:
    output = io.BytesIO()
    with Image.new("RGB", (64, 64), color) as image:
        image.save(output, format="PNG")
    return output.getvalue()


def _item(event_id: str, path: Path, data: bytes) -> JsonObject:
    return {
        "type": "imageGeneration",
        "id": event_id,
        "status": "completed",
        "failure": None,
        "savedPath": str(path.absolute()),
        "materializedSha256": hashlib.sha256(data).hexdigest(),
        "result": "materialized",
    }


@dataclass(slots=True)
class _Runner:
    image_count: int = 7
    requests: list[ImageEditProcessRequest] = field(default_factory=list)

    def run(self, request: ImageEditProcessRequest) -> ImageEditProcessResult:
        self.requests.append(request)
        directory = request.workspace / "provider-images"
        directory.mkdir(mode=0o700)
        items: list[JsonObject] = []
        for index in range(self.image_count):
            data = _png_bytes("blue" if index % 2 == 0 else "green")
            path = directory / f"image-{index}.png"
            _ = path.write_bytes(data)
            items.append(_item(f"image-{index}", path, data))
        return ImageEditProcessResult("thread-fixture", "turn-fixture", items[0], tuple(items))


def test_trace_post_sends_the_restricted_app_server_request_and_returns_bound_images(
    tmp_path: Path,
) -> None:
    target = tmp_path / "installed" / "codex"
    target.parent.mkdir()
    _ = target.write_text("fixture", encoding="utf-8")
    executable = tmp_path / "bin" / "codex"
    executable.parent.mkdir()
    executable.symlink_to(target)
    workspace = tmp_path / "jobs" / "one"
    workspace.mkdir(parents=True)
    runner = _Runner()

    result = CodexTracePostProvider(executable, "gpt-6-astra", runner).run(
        workspace=workspace,
        instruction="run frozen fixture with /fixed/python3",
        timeout_seconds=901.0,
    )

    assert len(runner.requests) == 1
    request = runner.requests[0]
    assert request.executable == target.resolve()
    assert request.workspace == workspace.resolve()
    assert request.prompt == "run frozen fixture with /fixed/python3"
    assert request.model == "gpt-6-astra"
    assert request.timeout_seconds == 901.0
    assert request.image_paths == ()
    assert request.permission_profile == "trace-post-restricted"
    assert request.allow_shell is True
    assert request.read_paths == (Path(sys.base_prefix).resolve(),)
    assert request.reasoning_effort == "medium"
    assert request.materialize_image_results is True
    assert request.min_image_generations == 7
    assert request.max_image_generations == 14
    assert request.max_stream_bytes == 256 * 1024 * 1024
    assert set(request.allowed_item_types) == {
        "imageGeneration",
        "agentMessage",
        "reasoning",
        "userMessage",
        "commandExecution",
        "imageView",
        "fileChange",
        "plan",
        "contextCompaction",
    }
    assert "exact Python interpreter" in request.developer_instructions
    assert "./provider-images/<image item id>.png" in request.developer_instructions
    assert result.thread_id == "thread-fixture"
    assert result.turn_id == "turn-fixture"
    assert len(result.images) == 7
    for index, image in enumerate(result.images):
        assert image.event_id == f"image-{index}"
        assert image.path.parent == workspace / "provider-images"
        assert image.sha256 == hashlib.sha256(image.path.read_bytes()).hexdigest()


def test_app_server_materializes_native_base64_into_the_private_workspace_sink(
    tmp_path: Path,
) -> None:
    data = _png_bytes()
    request = ImageEditProcessRequest(
        Path("/fixture/codex"),
        "gpt-6-astra",
        tmp_path,
        "fixture",
        (),
        30,
        permission_profile="trace-post-restricted",
        allow_shell=True,
        read_paths=(Path(sys.base_prefix),),
        allowed_item_types=("imageGeneration",),
        min_image_generations=1,
        max_image_generations=1,
        reasoning_effort="medium",
        materialize_image_results=True,
    )
    state = _StreamState(request)
    state.thread_id = "thread-fixture"
    state.turn_id = "turn-fixture"
    _ = state.accept(
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-fixture",
                "turnId": "turn-fixture",
                "item": {
                    "type": "imageGeneration",
                    "id": "native-one",
                    "status": "completed",
                    "failure": None,
                    "savedPath": "/untrusted/provider/path.png",
                    "result": base64.b64encode(data).decode(),
                },
            },
        }
    )
    _ = state.accept(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-fixture",
                "turn": {"id": "turn-fixture", "status": "completed"},
            },
        }
    )

    assert state.result is not None
    item = state.result.items[0]
    path = tmp_path / "provider-images/native-one.png"
    assert item["savedPath"] == str(path.resolve())
    assert item["materializedSha256"] == hashlib.sha256(data).hexdigest()
    assert item["result"] == "materialized"
    assert path.read_bytes() == data
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_trace_app_server_command_keeps_shell_and_installs_the_named_profile(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "codex"
    _ = executable.write_text("fixture", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    python_root = Path(sys.base_prefix).resolve()

    command = image_edit_command(
        executable,
        workspace=workspace,
        permission_profile="trace-post-restricted",
        allow_shell=True,
        read_paths=(python_root,),
        reasoning_effort="medium",
    )

    assert command[:5] == (
        str(executable),
        "app-server",
        "--stdio",
        "--enable",
        "image_generation",
    )
    disabled = {command[index + 1] for index, value in enumerate(command) if value == "--disable"}
    assert disabled == {
        "apps",
        "browser_use",
        "browser_use_external",
        "computer_use",
        "hooks",
        "multi_agent",
        "plugins",
    }
    assert "shell_tool" not in disabled
    assert "unified_exec" not in disabled
    settings = [command[index + 1] for index, value in enumerate(command) if value == "-c"]
    assert 'default_permissions="trace-post-restricted"' in settings
    assert 'model_reasoning_effort="medium"' in settings
    assert 'web_search="disabled"' in settings
    assert "mcp_servers={}" in settings
    profile_setting = next(
        setting for setting in settings if setting.startswith("permissions.trace-post-restricted=")
    )
    parsed = _JSON.validate_python(tomllib.loads(profile_setting))
    permissions = parsed["permissions"]
    assert isinstance(permissions, dict)
    profile = permissions["trace-post-restricted"]
    assert isinstance(profile, dict)
    assert profile["filesystem"] == {
        ":root": "deny",
        ":minimal": "read",
        str(workspace.resolve()): "write",
        str(executable.resolve()): "read",
        str(python_root): "read",
    }
    assert profile["network"] == {"enabled": False}


@pytest.mark.parametrize("image_count", [6, 15])
def test_trace_post_rejects_a_runner_result_outside_the_seven_to_fourteen_image_bound(
    tmp_path: Path,
    image_count: int,
) -> None:
    executable = tmp_path / "codex"
    _ = executable.write_text("fixture", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = CodexTracePostProvider(executable, "gpt-6-astra", _Runner(image_count))

    with pytest.raises(RuntimeError, match=r"^codex_trace_post_outcome_unknown$"):
        _ = provider.run(workspace=workspace, instruction="fixture", timeout_seconds=30)


@dataclass(slots=True)
class _AlteredRunner:
    alteration: Literal[
        "sha", "inside_wrong_directory", "wrong_filename", "outside", "symlink", "duplicate"
    ]

    def run(self, request: ImageEditProcessRequest) -> ImageEditProcessResult:
        sink = request.workspace / "provider-images"
        sink.mkdir(mode=0o700)
        items: list[JsonObject] = []
        for index in range(7):
            data = _png_bytes("blue" if index % 2 == 0 else "green")
            path = sink / f"native-{index}.png"
            _ = path.write_bytes(data)
            items.append(_item(f"native-{index}", path, data))

        data = (sink / "native-0.png").read_bytes()
        path = sink / "native-0.png"
        if self.alteration == "inside_wrong_directory":
            path = request.workspace / "wrong.png"
            _ = path.write_bytes(data)
        elif self.alteration == "wrong_filename":
            path = sink / "not-the-event-id.png"
            _ = path.write_bytes(data)
        elif self.alteration == "outside":
            path = request.workspace.parent / "outside.png"
            _ = path.write_bytes(data)
        elif self.alteration == "symlink":
            path = sink / "linked.png"
            path.symlink_to(sink / "native-0.png")
        item = _item("native-0", path, data)
        if self.alteration == "sha":
            item["materializedSha256"] = "f" * 64
        items[0] = item
        if self.alteration == "duplicate":
            items[1] = item
        return ImageEditProcessResult("thread-fixture", "turn-fixture", items[0], tuple(items))


@pytest.mark.parametrize(
    "alteration",
    ["sha", "inside_wrong_directory", "wrong_filename", "outside", "symlink", "duplicate"],
)
def test_trace_post_rejects_unbound_or_reused_materialized_paths(
    tmp_path: Path,
    alteration: Literal[
        "sha", "inside_wrong_directory", "wrong_filename", "outside", "symlink", "duplicate"
    ],
) -> None:
    executable = tmp_path / "codex"
    _ = executable.write_text("fixture", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = CodexTracePostProvider(executable, "gpt-6-astra", _AlteredRunner(alteration))

    with pytest.raises(RuntimeError, match=r"^codex_trace_post_outcome_unknown$"):
        _ = provider.run(workspace=workspace, instruction="fixture", timeout_seconds=30)


def test_trace_post_rejects_missing_executable_or_workspace_before_dispatch(
    tmp_path: Path,
) -> None:
    runner = _Runner()
    executable = tmp_path / "codex"
    _ = executable.write_text("fixture", encoding="utf-8")
    provider = CodexTracePostProvider(executable, "gpt-6-astra", runner)
    with pytest.raises(FileNotFoundError):
        _ = provider.run(
            workspace=tmp_path / "missing-workspace",
            instruction="fixture",
            timeout_seconds=30,
        )
    assert runner.requests == []

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = CodexTracePostProvider(tmp_path / "missing-codex", "gpt-6-astra", runner)
    with pytest.raises(FileNotFoundError):
        _ = provider.run(workspace=workspace, instruction="fixture", timeout_seconds=30)
    assert runner.requests == []
