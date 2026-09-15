# pyright: reportPrivateUsage=false
"""Synthetic app-server and materialized-image checks; never call a live image model."""

from __future__ import annotations

import base64
import hashlib
import io
import os
import sys
import tomllib
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Literal

import pytest
from PIL import Image
from pydantic import TypeAdapter

from ads_booster.providers.codex_cli import CodexCliError
from ads_booster.providers.codex_image_edit import (
    ImageEditProcessDiagnostic,
    ImageEditProcessRequest,
    ImageEditProcessResult,
    ImageEditSkillInput,
    _StreamState,
    image_edit_command,
)
from ads_booster.providers.codex_trace_post import CodexTracePostProvider
from ads_booster.transport.json_types import JsonObject

_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_INPUTS: TypeAdapter[list[JsonObject]] = TypeAdapter(list[JsonObject])
_METHODS: TypeAdapter[list[str]] = TypeAdapter(list[str])


@pytest.mark.parametrize("shell_failed", [False, True])
def test_empty_image_turn_records_the_observed_preparation_boundary(
    tmp_path: Path, shell_failed: bool
) -> None:
    request = ImageEditProcessRequest(
        Path("/fixture/codex"),
        "gpt-5.6-luna",
        tmp_path,
        "fixture",
        (),
        30,
        allow_shell=True,
        allowed_item_types=("commandExecution", "agentMessage"),
    )
    state = _StreamState(request, thread_id="thread", turn_id="turn")
    _ = state.accept(
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread",
                "turnId": "turn",
                "item": {
                    "type": "commandExecution",
                    "id": "command",
                    "status": "completed",
                    "exitCode": 1 if shell_failed else 0,
                    "aggregatedOutput": "sensitive fixture text",
                },
            },
        }
    )
    expected = (
        "codex_image_edit_preparation_failed" if shell_failed else "codex_image_edit_no_generation"
    )
    with pytest.raises(CodexCliError, match=expected):
        _ = state.accept(
            {
                "method": "turn/completed",
                "params": {"threadId": "thread", "turn": {"id": "turn", "status": "completed"}},
            }
        )
    assert state.diagnostics["command_completed"] == 1
    assert state.diagnostics["command_failed"] == int(shell_failed)
    assert state.diagnostics["image_completed"] == 0
    assert "sensitive" not in str(state.diagnostics)


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


def _install_skill(workspace: Path) -> Path:
    skill = workspace / "repo/skills/trace-post/SKILL.md"
    skill.parent.mkdir(parents=True)
    _ = skill.write_text("fixture skill", encoding="utf-8")
    return skill.resolve()


@pytest.mark.parametrize("invalid", ["missing", "outside", "symlink", "name"])
def test_native_skill_input_rejects_an_unbound_workspace_path(
    tmp_path: Path,
    invalid: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside/SKILL.md"
    outside.parent.mkdir()
    _ = outside.write_text("outside", encoding="utf-8")
    skill_path = workspace / "skills/trace-post/SKILL.md"
    skill_name = "trace-post"
    if invalid == "outside":
        skill_path = outside
    elif invalid == "symlink":
        skill_path.parent.mkdir(parents=True)
        skill_path.symlink_to(outside)
    elif invalid == "name":
        skill_path.parent.mkdir(parents=True)
        _ = skill_path.write_text("fixture", encoding="utf-8")
        skill_name = "Trace Post"

    with pytest.raises(CodexCliError, match="codex_image_edit_skill_input_invalid"):
        _ = ImageEditProcessRequest(
            Path("/fixture/codex"),
            "gpt-6-astra",
            workspace,
            "fixture",
            (),
            30,
            skill_input=ImageEditSkillInput(name=skill_name, path=skill_path),
        )


@pytest.mark.parametrize(
    ("variant", "reason"),
    [
        ("missing", "codex_image_edit_skill_unavailable"),
        ("disabled", "codex_image_edit_skill_unavailable"),
        ("wrong_path", "codex_image_edit_skill_unavailable"),
        ("malformed", "codex_image_edit_skill_discovery_invalid"),
    ],
)
def test_native_skill_discovery_fails_closed_for_the_target_skill(
    tmp_path: Path,
    variant: str,
    reason: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill = _install_skill(workspace)
    request = ImageEditProcessRequest(
        Path("/fixture/codex"),
        "gpt-6-astra",
        workspace,
        "fixture",
        (),
        30,
        skill_input=ImageEditSkillInput("trace-post", skill),
    )
    state = _StreamState(request)
    roots = state.accept({"id": 4, "result": {"data": [], "nextCursor": None}})
    assert roots == (
        {
            "id": 6,
            "method": "skills/extraRoots/set",
            "params": {"extraRoots": [str(skill.parent.parent)]},
        },
    )
    listing = state.accept({"id": 6, "result": {}})
    assert listing == (
        {
            "id": 7,
            "method": "skills/list",
            "params": {"cwds": [str(workspace.resolve())], "forceReload": True},
        },
    )
    metadata: JsonObject = {
        "name": "trace-post",
        "description": "fixture",
        "enabled": True,
        "path": str(skill),
        "scope": "repo",
    }
    skills: list[JsonObject] = [] if variant == "missing" else [metadata]
    if variant == "disabled":
        metadata["enabled"] = False
    elif variant == "wrong_path":
        metadata["path"] = str(workspace / "wrong/SKILL.md")
    elif variant == "malformed":
        metadata["enabled"] = "yes"
    response = _JSON.validate_python(
        {
            "data": [
                {
                    "cwd": str(workspace.resolve()),
                    "errors": [],
                    "skills": skills,
                }
            ]
        }
    )
    with pytest.raises(CodexCliError, match=reason):
        _ = state.accept({"id": 7, "result": response})


def test_native_skill_discovery_tolerates_same_name_elsewhere_and_unrelated_errors(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill = _install_skill(workspace)
    request = ImageEditProcessRequest(
        Path("/fixture/codex"),
        "gpt-6-astra",
        workspace,
        "fixture",
        (),
        30,
        skill_input=ImageEditSkillInput("trace-post", skill),
    )
    state = _StreamState(request)
    outgoing = state.accept(
        {
            "id": 7,
            "result": {
                "data": [
                    {
                        "cwd": str(workspace.resolve()),
                        "errors": [{"path": str(workspace / "other"), "message": "ignored"}],
                        "skills": [
                            {
                                "name": "trace-post",
                                "description": "another installation",
                                "enabled": True,
                                "path": str(workspace / "elsewhere/trace-post/SKILL.md"),
                                "scope": "user",
                            },
                            {
                                "name": "trace-post",
                                "description": "fixture",
                                "enabled": True,
                                "path": str(skill),
                                "scope": "repo",
                            },
                        ],
                    }
                ]
            },
        }
    )
    assert outgoing[0]["method"] == "thread/start"


@pytest.mark.parametrize(
    ("request_id", "phase"),
    [(6, "skills_roots"), (7, "skills_list")],
)
def test_native_skill_discovery_rpc_errors_are_sanitized(
    tmp_path: Path,
    request_id: int,
    phase: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill = _install_skill(workspace)
    state = _StreamState(
        ImageEditProcessRequest(
            Path("/fixture/codex"),
            "gpt-6-astra",
            workspace,
            "fixture",
            (),
            30,
            skill_input=ImageEditSkillInput("trace-post", skill),
        )
    )

    with pytest.raises(CodexCliError, match=f"codex_image_edit_{phase}_rpc_error_32603") as failure:
        _ = state.accept(
            {
                "id": request_id,
                "error": {
                    "code": -32603,
                    "message": "TOP SECRET PROVIDER DETAIL",
                },
            }
        )
    assert "TOP SECRET" not in str(failure.value)


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
    skill = _install_skill(workspace)
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
    assert request.persist_sanitized_diagnostic is True
    assert request.skill_input is not None
    assert request.skill_input.name == "trace-post"
    assert request.skill_input.path == skill.resolve()
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
    packaged_help = (
        files("ads_booster")
        .joinpath("trace_post_bundle/scripts/README.md")
        .read_text(encoding="utf-8")
    )
    assert "tools.image_gen__imagegen" in packaged_help
    assert "functions.exec with tools.image_gen__imagegen" in request.developer_instructions
    assert "native image-generation tool exposed in this turn" in request.developer_instructions
    assert "./provider-images/<image item id>.png" in request.developer_instructions
    assert result.thread_id == "thread-fixture"
    assert result.turn_id == "turn-fixture"
    assert len(result.images) == 7
    for index, image in enumerate(result.images):
        assert image.event_id == f"image-{index}"
        assert image.path.parent == workspace / "provider-images"
        assert image.sha256 == hashlib.sha256(image.path.read_bytes()).hexdigest()


def _native_tool_contract_server(
    tmp_path: Path,
    *,
    empty_turn: bool = False,
    reject_turn: bool = False,
) -> Path:
    """Model the app-server boundary without making a model or image call."""
    executable = tmp_path / "fixture-app-server"
    encoded = base64.b64encode(_png_bytes()).decode()
    _ = executable.write_text(
        f"""#!{sys.executable}
import json
import sys
from pathlib import Path

def send(value):
    print(json.dumps(value), flush=True)

extra_roots = None
methods = []
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    methods.append(method)
    if method == "initialize":
        send({{"id": request["id"], "result": {{}}}})
    elif method == "mcpServerStatus/list":
        send({{"id": request["id"], "result": {{"data": [], "nextCursor": None}}}})
    elif method == "skills/extraRoots/set":
        extra_roots = request["params"]
        send({{"id": request["id"], "result": {{}}}})
    elif method == "skills/list":
        Path(request["params"]["cwds"][0], "received-skill-discovery.json").write_text(
            json.dumps({{"extraRoots": extra_roots, "list": request["params"]}})
        )
        root = Path(extra_roots["extraRoots"][0])
        skill_path = root / "trace-post/SKILL.md"
        send({{"id": request["id"], "result": {{"data": [{{
            "cwd": request["params"]["cwds"][0],
            "errors": [],
            "skills": [{{"name": "trace-post", "description": "fixture", "enabled": True,
                        "path": str(skill_path), "scope": "repo"}}],
        }}]}}}})
    elif method == "thread/start":
        Path(request["params"]["cwd"], "received-instructions.txt").write_text(
            request["params"]["developerInstructions"]
        )
        send({{"id": request["id"], "result": {{
            "thread": {{"id": "thread-fixture"}},
            "activePermissionProfile": {{"id": "trace-post-restricted"}},
        }}}})
    elif method == "turn/start":
        Path(request["params"]["cwd"], "received-rpc-methods.json").write_text(
            json.dumps(methods)
        )
        Path(request["params"]["cwd"], "received-turn-input.json").write_text(
            json.dumps(request["params"]["input"])
        )
        if {reject_turn!r}:
            Path(request["params"]["cwd"], "turn-start-count.txt").write_text("1")
            send({{"id": request["id"], "error": {{"code": -32602, "message": "rejected"}}}})
            continue
        send({{"id": request["id"], "result": {{"turn": {{"id": "turn-fixture"}}}}}})
        if {empty_turn!r}:
            send({{"method": "turn/completed", "params": {{
                "threadId": "thread-fixture",
                "turn": {{"id": "turn-fixture", "status": "completed"}},
            }}}})
            continue
        send({{"method": "item/started", "params": {{
            "threadId": "thread-fixture", "turnId": "turn-fixture",
            "item": {{"type": "commandExecution", "id": "command-started"}},
        }}}})
        for event_id, exit_code in (("command-ok", 0), ("command-bad", 9), ("command-none", None)):
            send({{"method": "item/completed", "params": {{
                "threadId": "thread-fixture", "turnId": "turn-fixture",
                "item": {{"type": "commandExecution", "id": event_id, "exitCode": exit_code}},
            }}}})
        send({{"method": "item/completed", "params": {{
            "threadId": "thread-fixture", "turnId": "turn-fixture",
            "item": {{"type": "agentMessage", "id": "message-one", "text": "TOP SECRET MESSAGE"}},
        }}}})
        for index in range(7):
            send({{"method": "item/completed", "params": {{
                "threadId": "thread-fixture",
                "turnId": "turn-fixture",
                "item": {{
                    "type": "imageGeneration",
                    "id": f"native-{{index}}",
                    "status": "completed",
                    "failure": None,
                    "result": {encoded!r},
                }},
            }}}})
        send({{"method": "turn/completed", "params": {{
            "threadId": "thread-fixture",
            "turn": {{"id": "turn-fixture", "status": "completed"}},
        }}}})
""",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return executable


def test_stdio_trace_post_transports_the_native_tool_compatibility_instruction(
    tmp_path: Path,
) -> None:
    executable = _native_tool_contract_server(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill = _install_skill(workspace)

    result = CodexTracePostProvider(executable, "gpt-6-astra").run(
        workspace=workspace,
        instruction="follow the frozen trace-post fixture",
        timeout_seconds=3,
    )

    assert len(result.images) == 7
    assert all(image.path.parent == workspace / "provider-images" for image in result.images)
    received = (workspace / "received-instructions.txt").read_text()
    assert "functions.exec with tools.image_gen__imagegen" in received
    assert "native image-generation tool exposed in this turn" in received
    assert "Do not look for functions.exec" in received
    turn_input = _INPUTS.validate_json((workspace / "received-turn-input.json").read_text())
    discovery = _JSON.validate_json((workspace / "received-skill-discovery.json").read_text())
    assert discovery == {
        "extraRoots": {"extraRoots": [str(skill.parent.parent)]},
        "list": {"cwds": [str(workspace.resolve())], "forceReload": True},
    }
    assert _METHODS.validate_json((workspace / "received-rpc-methods.json").read_text()) == [
        "initialize",
        "initialized",
        "mcpServerStatus/list",
        "skills/extraRoots/set",
        "skills/list",
        "thread/start",
        "mcpServerStatus/list",
        "turn/start",
    ]
    assert turn_input[0] == {
        "type": "text",
        "text": "$trace-post\n\nfollow the frozen trace-post fixture",
    }
    assert turn_input[1] == {
        "type": "skill",
        "name": "trace-post",
        "path": str(skill),
    }
    diagnostic_path = workspace / "codex-image-edit-diagnostic.json"
    diagnostic = ImageEditProcessDiagnostic.model_validate_json(diagnostic_path.read_text())
    assert diagnostic.completed.image_generation == 7
    assert diagnostic.started.command_execution == 1
    assert diagnostic.completed.command_execution == 3
    assert diagnostic.image_count == 7
    assert diagnostic.command_success == 1
    assert diagnostic.command_nonzero == 1
    assert diagnostic.command_absent == 1
    assert diagnostic.agent_message_count == 1
    assert diagnostic.agent_message_max_length == "1_200"
    assert diagnostic.terminal == "completed"
    assert len(diagnostic_path.read_bytes()) <= 4096
    assert diagnostic_path.stat().st_mode & 0o777 == 0o600
    assert "follow the frozen trace-post fixture" not in diagnostic_path.read_text()
    assert "TOP SECRET MESSAGE" not in diagnostic_path.read_text()


def test_rejected_native_skill_turn_is_not_retried_as_text_only(tmp_path: Path) -> None:
    executable = _native_tool_contract_server(tmp_path, reject_turn=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill = _install_skill(workspace)

    with pytest.raises(CodexCliError, match="codex_image_edit_turn_start_rpc_error_32602"):
        _ = CodexTracePostProvider(executable, "gpt-6-astra").run(
            workspace=workspace,
            instruction="fixture",
            timeout_seconds=3,
        )

    assert (workspace / "turn-start-count.txt").read_text() == "1"
    turn_input = _INPUTS.validate_json((workspace / "received-turn-input.json").read_text())
    assert turn_input == [
        {"type": "text", "text": "$trace-post\n\nfixture"},
        {"type": "skill", "name": "trace-post", "path": str(skill)},
    ]


def test_diagnostic_write_failure_does_not_replace_a_successful_provider_result(
    tmp_path: Path,
) -> None:
    executable = _native_tool_contract_server(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _ = _install_skill(workspace)
    (workspace / "codex-image-edit-diagnostic.json").mkdir()
    temporary = workspace / ".codex-image-edit-diagnostic.tmp"
    _ = temporary.write_text("belongs-to-another-process", encoding="utf-8")

    result = CodexTracePostProvider(executable, "gpt-6-astra").run(
        workspace=workspace,
        instruction="fixture",
        timeout_seconds=3,
    )

    assert len(result.images) == 7
    assert temporary.read_text() == "belongs-to-another-process"


def test_diagnostic_write_failure_does_not_replace_the_provider_failure(
    tmp_path: Path,
) -> None:
    executable = _native_tool_contract_server(tmp_path, empty_turn=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _ = _install_skill(workspace)
    (workspace / "codex-image-edit-diagnostic.json").mkdir()
    temporary = workspace / ".codex-image-edit-diagnostic.tmp"
    _ = temporary.write_text("belongs-to-another-process", encoding="utf-8")

    with pytest.raises(Exception, match="codex_image_edit_no_generation"):
        _ = CodexTracePostProvider(executable, "gpt-6-astra").run(
            workspace=workspace,
            instruction="fixture",
            timeout_seconds=3,
        )
    assert temporary.read_text() == "belongs-to-another-process"


def test_completed_native_turn_is_checkpointed_before_runner_returns(tmp_path: Path) -> None:
    executable = _native_tool_contract_server(tmp_path)
    workspace = tmp_path / "checkpoint-work"
    workspace.mkdir()
    _ = _install_skill(workspace)
    checkpoints: list[tuple[int, bool]] = []
    provider = CodexTracePostProvider(executable, "fixture")
    result = provider.run_checkpointed(
        workspace=workspace,
        instruction="fixture",
        timeout_seconds=10,
        on_checkpoint=lambda result, completed: checkpoints.append((len(result.images), completed)),
    )
    assert checkpoints == [(index, False) for index in range(1, 8)] + [(7, True)]
    assert len(result.images) == 7
    diagnostic = ImageEditProcessDiagnostic.model_validate_json(
        (workspace / "codex-image-edit-diagnostic.json").read_text()
    )
    assert diagnostic.terminal == "completed"
    assert diagnostic.completed.image_generation == len(result.images)


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


def test_terminal_items_are_counted_but_do_not_replace_completion_notifications(
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
        allowed_item_types=("imageGeneration",),
        min_image_generations=1,
        max_image_generations=1,
    )
    state = _StreamState(request)
    state.thread_id = "thread-fixture"
    state.turn_id = "turn-fixture"

    with pytest.raises(Exception, match="codex_image_edit_no_generation"):
        _ = state.accept(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-fixture",
                    "turn": {
                        "id": "turn-fixture",
                        "status": "completed",
                        "itemsView": "full",
                        "items": [
                            {
                                "type": "imageGeneration",
                                "id": "terminal-one",
                                "status": "completed",
                                "failure": None,
                                "result": base64.b64encode(data).decode(),
                            }
                        ],
                    },
                },
            }
        )
    assert state.diagnostic().terminal_items.image_generation == 1
    assert state.items == []


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
        str(
            (Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "tmp/arg0").resolve()
        ): "read",
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
    _ = _install_skill(workspace)
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
    _ = _install_skill(workspace)
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
