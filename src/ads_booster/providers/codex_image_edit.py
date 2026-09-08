"""Dedicated image-generation process boundary; never replay an admitted operation.

The event contract is the installed Codex app-server schema (0.153.4), not an
assumed ``codex exec --json`` image event. Provider capability is not I/O proof.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from pydantic import TypeAdapter

from ads_booster.providers.codex_cli import CodexCliError, ReviewImage, read_review_images
from ads_booster.transport.json_types import JsonObject, JsonValue

_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_GLOBAL_MCP_ID = 4
_THREAD_MCP_ID = 5
_MAX_RPC_CODE = 99999
_INITIALIZE_ID = 1
_THREAD_START_ID = 2
_TURN_START_ID = 3
_MAX_PROBE_SECONDS = 30
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_IMAGES = 4
_MAX_PROMPT_CHARS = 20000
_MAX_TIMEOUT_SECONDS = 3600
_MAX_STREAM_BYTES = 20 * 1024 * 1024
_DISABLED = (
    "apps",
    "browser_use",
    "computer_use",
    "hooks",
    "multi_agent",
    "shell_tool",
    "unified_exec",
    "plugins",
)


@dataclass(frozen=True, slots=True)
class ImageEditProcessRequest:
    """Immutable local inputs for one provider invocation."""

    executable: Path
    model: str
    workspace: Path
    prompt: str
    image_paths: tuple[Path, ...]
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class ImageEditProcessResult:
    """A completed image-generation item, bound to its completed provider turn."""

    thread_id: str
    turn_id: str
    item: JsonObject


class ImageEditRunner(Protocol):
    def run(self, request: ImageEditProcessRequest) -> ImageEditProcessResult: ...


@dataclass(frozen=True, slots=True)
class ImageEditResult:
    """Verified raster readback; does not assert edit quality or native UI support."""

    path: Path
    sha256: str
    event_id: str
    prompt_sha256: str
    source_sha256s: tuple[str, ...]
    thread_id: str
    turn_id: str


def _error(code: str) -> CodexCliError:
    return CodexCliError(code)


def image_edit_command(
    executable: Path,
    disabled_mcp_servers: tuple[str, ...] = (),
    *,
    workspace: Path | None = None,
) -> tuple[str, ...]:
    """Disable unrelated tools without modifying the user's login or config."""
    command = [str(executable), "app-server", "--stdio", "--enable", "image_generation"]
    for feature in _DISABLED:
        command.extend(("--disable", feature))
    for setting in ('web_search="disabled"', "mcp_servers={}", "analytics.enabled=false"):
        command.extend(("-c", setting))
    for name in disabled_mcp_servers:
        command.extend(("-c", f"mcp_servers.{name}.enabled=false"))
    if workspace is not None:
        profile = (
            '{filesystem={":root"="deny",":minimal"="read",'
            + json.dumps(str(workspace.resolve()))
            + '="write",'
            + json.dumps(str(executable.resolve()))
            + '="read"},network={enabled=false}}'
        )
        command.extend(
            (
                "-c",
                "permissions.trace-image-edit-restricted=" + profile,
                "-c",
                'default_permissions="trace-image-edit-restricted"',
            )
        )
    return tuple(command)


def _configured_mcp_names() -> tuple[str, ...]:
    """Read names only for process overrides; never emit configuration values."""
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    config = home / "config.toml"
    if not config.exists():
        return ()
    values = _JSON.validate_python(tomllib.loads(config.read_text()))
    servers = values.get("mcp_servers", {})
    if not isinstance(servers, dict) or any(
        re.fullmatch(r"[a-zA-Z0-9_-]{1,160}", name) is None for name in servers
    ):
        code = "codex_image_edit_mcp_config_unsupported"
        raise _error(code)
    return tuple(sorted(servers))


def _mcp_request(request_id: int, thread_id: str | None = None) -> JsonObject:
    return {
        "id": request_id,
        "method": "mcpServerStatus/list",
        "params": {
            "detail": "toolsAndAuthOnly",
            "limit": 100,
            "threadId": thread_id,
        },
    }


def _check_mcp_inventory(response: JsonObject, disabled: tuple[str, ...]) -> None:
    servers = response.get("data")
    if not isinstance(servers, list) or response.get("nextCursor") is not None:
        code = "codex_image_edit_mcp_inventory_incomplete"
        raise _error(code)
    for server in servers:
        if (
            not isinstance(server, dict)
            or server.get("name") not in disabled
            or server.get("tools") != {}
        ):
            code = "codex_image_edit_mcp_exposure_detected"
            raise _error(code)


@dataclass(frozen=True, slots=True)
class SubprocessAppServerImageEditRunner:
    """Read bounded official JSON-RPC notifications; never execute server requests."""

    def run(self, request: ImageEditProcessRequest) -> ImageEditProcessResult:
        try:
            return self._run(request)
        except (OSError, ValueError, TimeoutError) as error:
            code = "codex_image_edit_outcome_unknown"
            raise _error(code) from error

    def _run(self, request: ImageEditProcessRequest) -> ImageEditProcessResult:
        return _exchange(request, _StreamState(request))


class _ExchangeState[T](Protocol):
    disabled_mcp_servers: tuple[str, ...]
    result: T | None

    def initial(self) -> JsonObject: ...
    def accept(self, message: JsonObject) -> tuple[JsonObject, ...]: ...


def _exchange[T](request: ImageEditProcessRequest, state: _ExchangeState[T]) -> T:  # noqa: C901

    state.disabled_mcp_servers = _configured_mcp_names()
    process = subprocess.Popen(  # noqa: S603 - trusted configured executable and fixed args.
        (
            *image_edit_command(
                request.executable, state.disabled_mcp_servers, workspace=request.workspace
            ),
            "-c",
            "model=" + json.dumps(request.model),
        ),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=request.workspace,
    )
    try:
        if process.stdin is None or process.stdout is None:
            code = "codex_image_edit_stream_missing"
            raise _error(code)
        with selectors.DefaultSelector() as selector:
            _ = selector.register(process.stdout, selectors.EVENT_READ)
            _ = process.stdin.write(_encode(state.initial()))
            process.stdin.flush()
            deadline = time.monotonic() + request.timeout_seconds
            pending: bytes = b""
            total = 0
            while time.monotonic() < deadline:
                if not selector.select(max(0, deadline - time.monotonic())):
                    break
                chunk: bytes = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    code = "codex_image_edit_outcome_unknown"
                    raise _error(code)
                total += len(chunk)
                if total > _MAX_STREAM_BYTES:
                    code = "codex_image_edit_stream_limit"
                    raise _error(code)
                pending += chunk
                while b"\n" in pending:
                    separator = pending.index(b"\n")
                    line: bytes = pending[:separator]
                    pending = pending[separator + 1 :]
                    outgoing = state.accept(_JSON.validate_json(line))
                    for message in outgoing:
                        _ = process.stdin.write(_encode(message))
                        process.stdin.flush()
                    if state.result is not None:
                        return state.result
            code = "codex_image_edit_outcome_unknown"
            raise _error(code)
    finally:
        process.terminate()
        try:
            _ = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            _ = process.wait()
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()


def _encode(value: JsonObject) -> bytes:
    return (json.dumps(value, separators=(",", ":")) + "\n").encode()


@dataclass(slots=True)
class _StreamState:
    request: ImageEditProcessRequest
    disabled_mcp_servers: tuple[str, ...] = ()
    thread_id: str = ""
    turn_id: str = ""
    items: list[JsonObject] = field(default_factory=list)
    result: ImageEditProcessResult | None = None

    def initial(self) -> JsonObject:
        return {
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "trace-image-edit", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        }

    def accept(self, message: JsonObject) -> tuple[JsonObject, ...]:  # noqa: C901 - bounded RPC phases.
        if "error" in message:
            response_id = message.get("id")
            phases = {
                1: "initialize",
                2: "thread_start",
                3: "turn_start",
                4: "global_mcp",
                5: "thread_mcp",
            }
            phase = (
                phases.get(response_id, "unknown") if isinstance(response_id, int) else "unknown"
            )
            error = message["error"]
            rpc_code = error.get("code") if isinstance(error, dict) else None
            suffix = (
                str(abs(rpc_code))
                if isinstance(rpc_code, int) and abs(rpc_code) <= _MAX_RPC_CODE
                else "unknown"
            )
            raise _error("codex_image_edit_" + phase + "_rpc_error_" + suffix)
        if message.get("method") == "error" or ("id" in message and "method" in message):
            code = "codex_image_edit_unexpected_request_or_error"
            raise _error(code)
        response = message.get("result")
        if message.get("id") == _INITIALIZE_ID and isinstance(response, dict):
            return ({"method": "initialized", "params": {}}, _mcp_request(_GLOBAL_MCP_ID))
        if message.get("id") == _GLOBAL_MCP_ID and isinstance(response, dict):
            _check_mcp_inventory(response, self.disabled_mcp_servers)
            return (
                {
                    "id": 2,
                    "method": "thread/start",
                    "params": {
                        "model": self.request.model,
                        "cwd": str(self.request.workspace),
                        "ephemeral": True,
                        "approvalPolicy": "never",
                        "permissions": "trace-image-edit-restricted",
                        "baseInstructions": "Use only image generation for this task.",
                        "developerInstructions": (
                            "Never invoke shell, network or other tools. Save exactly one PNG "
                            "inside cwd. Never modify attached source files."
                        ),
                    },
                },
            )
        if message.get("id") == _THREAD_START_ID and isinstance(response, dict):
            profile = response.get("activePermissionProfile")
            if not isinstance(profile, dict) or profile.get("id") != "trace-image-edit-restricted":
                code = "codex_image_edit_permission_profile_unconfirmed"
                raise _error(code)
            thread = response.get("thread")
            if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
                code = "codex_image_edit_thread_missing"
                raise _error(code)
            self.thread_id = str(thread["id"])
            return (_mcp_request(_THREAD_MCP_ID, self.thread_id),)
        if message.get("id") == _THREAD_MCP_ID and isinstance(response, dict):
            _check_mcp_inventory(response, self.disabled_mcp_servers)
            inputs: list[JsonObject] = [{"type": "text", "text": self.request.prompt}]
            inputs.extend(
                {"type": "localImage", "path": str(path)} for path in self.request.image_paths
            )
            return (
                {
                    "id": 3,
                    "method": "turn/start",
                    "params": {
                        "threadId": self.thread_id,
                        "model": self.request.model,
                        "input": list(inputs),
                        "cwd": str(self.request.workspace),
                        "approvalPolicy": "never",
                        "permissions": "trace-image-edit-restricted",
                    },
                },
            )
        if message.get("id") == _TURN_START_ID and isinstance(response, dict):
            turn = response.get("turn")
            if isinstance(turn, dict) and isinstance(turn.get("id"), str):
                self._bind_turn(str(turn["id"]))
            return ()
        self._notification(message)
        return ()

    def _bind_turn(self, turn_id: JsonValue) -> None:
        if (
            not isinstance(turn_id, str)
            or not turn_id
            or (self.turn_id and turn_id != self.turn_id)
        ):
            code = "codex_image_edit_turn_mismatch"
            raise _error(code)
        self.turn_id = turn_id

    def _notification(self, message: JsonObject) -> None:
        method, params = message.get("method"), message.get("params")
        if method not in ("item/started", "item/completed", "turn/completed"):
            return
        if not isinstance(params, dict) or params.get("threadId") != self.thread_id:
            code = "codex_image_edit_thread_mismatch"
            raise _error(code)
        if method == "turn/completed":
            turn = params.get("turn")
            if not isinstance(turn, dict) or turn.get("status") != "completed":
                code = "codex_image_edit_outcome_unknown"
                raise _error(code)
            self._bind_turn(turn.get("id"))
            if len(self.items) != 1:
                code = "codex_image_edit_generation_event_required"
                raise _error(code)
            self.result = ImageEditProcessResult(self.thread_id, self.turn_id, self.items[0])
            return
        self._bind_turn(params.get("turnId"))
        item = params.get("item")
        if not isinstance(item, dict):
            code = "codex_image_edit_item_invalid"
            raise _error(code)
        kind = item.get("type")
        if kind not in ("imageGeneration", "agentMessage", "reasoning", "userMessage"):
            code = "codex_image_edit_unexpected_tool"
            raise _error(code)
        if method == "item/completed" and kind == "imageGeneration":
            self.items.append(item)


@dataclass(frozen=True, slots=True)
class ImageEditReadiness:
    ready: bool
    reason: str
    live_io_verified: bool = False


@dataclass(slots=True)
class _CapabilityState:
    request: ImageEditProcessRequest
    stream: _StreamState = field(init=False)
    checking_thread: bool = False
    disabled_mcp_servers: tuple[str, ...] = ()
    result: bool | None = None

    def __post_init__(self) -> None:
        """Reuse generation preflight but stop before the turn request."""
        self.stream = _StreamState(self.request)

    def initial(self) -> JsonObject:
        return {
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "trace-image-edit-probe", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        }

    def accept(self, message: JsonObject) -> tuple[JsonObject, ...]:
        if self.checking_thread:
            self.stream.disabled_mcp_servers = self.disabled_mcp_servers
            outgoing = self.stream.accept(message)
            if any(item.get("method") == "turn/start" for item in outgoing):
                self.result = True
                return ()
            return outgoing
        if (
            "error" in message
            or message.get("method") == "error"
            or ("id" in message and "method" in message)
        ):
            code = "codex_image_edit_capability_unavailable"
            raise _error(code)
        response = message.get("result")
        if message.get("id") == _INITIALIZE_ID and isinstance(response, dict):
            return ({"method": "initialized", "params": {}}, _mcp_request(_GLOBAL_MCP_ID))
        if message.get("id") == _GLOBAL_MCP_ID and isinstance(response, dict):
            _check_mcp_inventory(response, self.disabled_mcp_servers)
            return (
                {
                    "id": 2,
                    "method": "modelProvider/capabilities/read",
                    "params": {},
                },
            )
        if message.get("id") == _THREAD_START_ID and isinstance(response, dict):
            if response.get("imageGeneration") is not True:
                self.result = False
            else:
                self.checking_thread = True
                self.stream.disabled_mcp_servers = self.disabled_mcp_servers
                return self.stream.accept(
                    {"id": _GLOBAL_MCP_ID, "result": {"data": [], "nextCursor": None}}
                )
        return ()


@dataclass(frozen=True, slots=True)
class CodexImageEditProvider:
    executable: Path
    model: str
    runner: ImageEditRunner = field(default_factory=SubprocessAppServerImageEditRunner)

    def readiness(self, *, timeout_seconds: float = 10) -> ImageEditReadiness:
        """Probe capability and restricted ephemeral-thread setup; never submit a turn."""
        if not self.model.strip() or not 0 < timeout_seconds <= _MAX_PROBE_SECONDS:
            return ImageEditReadiness(ready=False, reason="codex_image_edit_probe_config_invalid")
        try:
            with TemporaryDirectory(prefix="trace-image-edit-probe-") as directory:
                request = ImageEditProcessRequest(
                    self.executable,
                    self.model,
                    Path(directory),
                    "",
                    (),
                    timeout_seconds,
                )
                supported = _exchange(request, _CapabilityState(request))
        except (CodexCliError, OSError, ValueError) as error:
            reason = str(error)
            if re.fullmatch(r"codex_image_edit_[a-z0-9_]{1,100}", reason) is None:
                reason = "codex_image_edit_capability_unavailable"
            return ImageEditReadiness(ready=False, reason=reason)
        return ImageEditReadiness(
            supported,
            "provider_restricted_thread_ready"
            if supported
            else "provider_image_generation_unavailable",
        )

    def generate(  # noqa: C901, PLR0915 - one admission/readback boundary.
        self,
        *,
        operation_id: str,
        workspace: Path,
        prompt: str,
        images: tuple[ReviewImage, ...],
        timeout_seconds: float = 300,
    ) -> ImageEditResult:
        """Admit once before provider access; caller owns canonical approval and scope."""
        if (
            not re.fullmatch(r"[a-zA-Z0-9_-]{1,160}", operation_id)
            or not self.model.strip()
            or not prompt.strip()
            or len(prompt) > _MAX_PROMPT_CHARS
            or not 0 < len(images) <= _MAX_IMAGES
            or any(not 0 < len(image.data) <= _MAX_IMAGE_BYTES for image in images)
            or not 0 < timeout_seconds <= _MAX_TIMEOUT_SECONDS
        ):
            code = "codex_image_edit_input_invalid"
            raise _error(code)
        root = workspace.resolve(strict=True)
        if workspace.is_symlink() or not root.is_dir():
            code = "codex_image_edit_workspace_invalid"
            raise _error(code)
        marker = root / "codex-image-edit-started.json"
        record: JsonObject = {
            "operation_id": operation_id,
            "model": self.model,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "source_sha256s": [image.sha256 for image in images],
        }
        try:
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            code = "codex_image_edit_already_invoked"
            raise _error(code) from error
        with os.fdopen(fd, "wb") as stream:
            _ = stream.write(_encode(record))
            stream.flush()
            os.fsync(stream.fileno())
        directory_fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        paths: list[Path] = []
        for index, image in enumerate(images):
            path = root / f"source-{index}.{'png' if image.format == 'PNG' else 'jpg'}"
            with path.open("xb") as stream:
                _ = stream.write(image.data)
            path.chmod(0o600)
            if read_review_images((path,))[0].sha256 != image.sha256:
                code = "codex_image_edit_source_changed"
                raise _error(code)
            paths.append(path)
        response = self.runner.run(
            ImageEditProcessRequest(
                self.executable,
                self.model,
                root,
                prompt,
                tuple(paths),
                timeout_seconds,
            )
        )
        item = response.item
        if (
            item.get("type") != "imageGeneration"
            or item.get("status") != "completed"
            or item.get("failure") is not None
            or not isinstance(item.get("id"), str)
            or not item["id"]
            or not isinstance(item.get("savedPath"), str)
        ):
            code = "codex_image_edit_generation_event_invalid"
            raise _error(code)
        output = Path(str(item["savedPath"]))
        if not output.is_absolute() or output.is_symlink():
            code = "codex_image_edit_output_outside_root"
            raise _error(code)
        output = output.resolve(strict=True)
        if not output.is_relative_to(root) or output in paths:
            code = "codex_image_edit_output_outside_root"
            raise _error(code)
        image = read_review_images((output,))[0]
        if image.format != "PNG":
            code = "codex_image_edit_output_not_png"
            raise _error(code)
        if any(
            read_review_images((path,))[0].sha256 != original.sha256
            for path, original in zip(paths, images, strict=True)
        ):
            code = "codex_image_edit_source_changed"
            raise _error(code)
        return ImageEditResult(
            output,
            image.sha256,
            str(item["id"]),
            hashlib.sha256(prompt.encode()).hexdigest(),
            tuple(image.sha256 for image in images),
            response.thread_id,
            response.turn_id,
        )
