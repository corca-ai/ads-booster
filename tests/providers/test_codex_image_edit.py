# pyright: reportPrivateUsage=false
"""Fixture generation and protocol streams; never invoke a live image model."""

from __future__ import annotations

import base64
import io
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
from PIL import Image
from pydantic import TypeAdapter

from ads_booster.providers.codex_cli import CodexCliError, ReviewImage
from ads_booster.providers.codex_image_edit import (
    CodexImageEditProvider,
    ImageEditProcessRequest,
    ImageEditProcessResult,
    _StreamState,
    image_edit_command,
)
from ads_booster.transport.json_types import JsonObject


def source() -> ReviewImage:
    buffer = io.BytesIO()
    Image.new("RGB", (12, 16), "white").save(buffer, format="PNG")
    return ReviewImage(buffer.getvalue(), "PNG", 12, 16)


@dataclass
class FixtureRunner:
    mode: Literal["good", "missing_event", "outside", "changed", "timeout", "jpeg"] = "good"
    calls: int = 0

    def run(self, request: ImageEditProcessRequest) -> ImageEditProcessResult:
        self.calls += 1
        adapter: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
        marker = adapter.validate_json(
            (request.workspace / "codex-image-edit-started.json").read_text()
        )
        assert marker["source_sha256s"] == [source().sha256]
        assert request.image_paths[0].read_bytes() == source().data
        assert request.model == "gpt-6-astra"
        if self.mode == "timeout":
            raise TimeoutError
        output = request.workspace / "generated.png"
        if self.mode == "outside":
            output = request.workspace.parent / "outside.png"
        image = Image.new("RGB", (12, 20), "blue")
        image.save(output, format="JPEG" if self.mode == "jpeg" else "PNG")
        if self.mode == "changed":
            image.save(request.image_paths[0], format="PNG")
        return ImageEditProcessResult(
            "thread-fixture",
            "turn-fixture",
            {
                "type": "agentMessage" if self.mode == "missing_event" else "imageGeneration",
                "id": "image-fixture",
                "status": "completed",
                "savedPath": str(output),
                "result": "fixture-only",
                "failure": None,
            },
        )


def test_generated_png_has_exact_input_provenance_and_no_replay(tmp_path: Path) -> None:
    runner = FixtureRunner()
    provider = CodexImageEditProvider(Path("/fixture/codex"), "gpt-6-astra", runner)
    result = provider.generate(
        operation_id="one", workspace=tmp_path, prompt="Add blue space", images=(source(),)
    )
    assert result.event_id == "image-fixture"
    assert result.source_sha256s == (source().sha256,)
    assert result.path.is_file()
    with pytest.raises(CodexCliError, match="already_invoked"):
        _ = provider.generate(
            operation_id="one", workspace=tmp_path, prompt="Add blue space", images=(source(),)
        )
    assert runner.calls == 1


@pytest.mark.parametrize(
    ("mode", "error"),
    [
        ("missing_event", "generation_event_invalid"),
        ("outside", "outside_root"),
        ("changed", "source_changed"),
        ("jpeg", "output_not_png"),
    ],
)
def test_output_file_alone_never_proves_generation(
    tmp_path: Path,
    mode: Literal["missing_event", "outside", "changed", "jpeg"],
    error: str,
) -> None:
    provider = CodexImageEditProvider(Path("/fixture/codex"), "gpt-6-astra", FixtureRunner(mode))
    with pytest.raises(CodexCliError, match=error):
        _ = provider.generate(
            operation_id="one", workspace=tmp_path, prompt="Fixture", images=(source(),)
        )


def test_timeout_is_not_replayed_after_restart(tmp_path: Path) -> None:
    runner = FixtureRunner("timeout")
    first = CodexImageEditProvider(Path("/fixture/codex"), "gpt-6-astra", runner)
    with pytest.raises(TimeoutError):
        _ = first.generate(
            operation_id="one", workspace=tmp_path, prompt="Fixture", images=(source(),)
        )
    second = CodexImageEditProvider(Path("/fixture/codex"), "gpt-6-astra", runner)
    with pytest.raises(CodexCliError, match="already_invoked"):
        _ = second.generate(
            operation_id="one", workspace=tmp_path, prompt="Fixture", images=(source(),)
        )
    assert runner.calls == 1


def fake_server(  # noqa: PLR0913 - independent protocol fault cases.
    tmp_path: Path,
    *,
    bad_request: bool = False,
    active_mcp: bool = False,
    thread_mcp: bool = False,
    thread_failure: bool = False,
    notification_fault: Literal["none", "wrong_turn", "duplicate", "failed", "missing"] = "none",
) -> Path:
    executable = tmp_path / "fixture-server"
    # Official app-server field shapes; generated pixels are synthetic fixture bytes.
    _ = executable.write_text(f"""#!{sys.executable}
import base64,json,sys
from pathlib import Path

def send(value):
 print(json.dumps(value),flush=True)

for line in sys.stdin:
 request=json.loads(line)
 method=request.get("method")
 if method=="initialize":
  send({{"id":request["id"],"result":{{}}}})
 elif method=="mcpServerStatus/list":
  exposed={active_mcp!r} or ({thread_mcp!r} and request["params"].get("threadId"))
  data=[{{"name":"fixture-external","tools":{{"send_message":{{}}}}}}] if exposed else []
  send({{"id":request["id"],"result":{{"data":data,"nextCursor":None}}}})
 elif method=="modelProvider/capabilities/read":
  send({{"id":request["id"],"result":{{"imageGeneration":True}}}})
 elif method=="thread/start":
  if {thread_failure!r}:
   send({{"id":request["id"],"error":{{"code":-32603,"message":"private host detail"}}}})
   continue
  send({{"id":request["id"],"result":{{"thread":{{"id":"thread-fixture"}},"activePermissionProfile":{{"id":"trace-image-edit-restricted"}}}}}})
 elif method=="turn/start":
  params=request["params"]
  assert params["model"]=="gpt-6-astra"
  assert params["permissions"]=="trace-image-edit-restricted"
  assert "sandboxPolicy" not in params
  assert params["input"][1]["type"]=="localImage"
  if {bad_request!r}:
   send({{"id":99,"method":"item/commandExecution/requestApproval","params":{{}}}})
   continue
  output=Path(params["cwd"])/"generated.png"
  output.write_bytes(base64.b64decode({base64.b64encode(source().data).decode()!r}))
  send({{"id":request["id"],"result":{{"turn":{{"id":"turn-fixture"}}}}}})
  turn_id="wrong" if {notification_fault!r}=="wrong_turn" else "turn-fixture"
  item={{"method":"item/completed","params":{{
   "threadId":"thread-fixture","turnId":turn_id,
   "item":{{"type":"imageGeneration","id":"fixture-generation",
    "result":"fixture-only","status":"completed","savedPath":str(output)}}}}}}
  if {notification_fault!r}!="missing":
   send(item)
  if {notification_fault!r}=="duplicate":
   send(item)
  status="failed" if {notification_fault!r}=="failed" else "completed"
  send({{"method":"turn/completed","params":{{"threadId":"thread-fixture",
   "turn":{{"id":"turn-fixture","status":status}}}}}})
""")
    executable.chmod(0o700)
    return executable


def test_real_stdio_transport_with_fixture_server(tmp_path: Path) -> None:
    provider = CodexImageEditProvider(fake_server(tmp_path), "gpt-6-astra")
    readiness = provider.readiness(timeout_seconds=3)
    assert readiness.ready
    assert not readiness.live_io_verified
    result = provider.generate(
        operation_id="fixture",
        workspace=tmp_path,
        prompt="Fixture only",
        images=(source(),),
        timeout_seconds=3,
    )
    assert result.event_id == "fixture-generation"
    assert result.thread_id == "thread-fixture"
    command = image_edit_command(provider.executable)
    assert "image_generation" in command
    for feature in ("shell_tool", "unified_exec", "apps", "browser_use", "computer_use"):
        assert command[command.index(feature) - 1] == "--disable"
    assert 'web_search="disabled"' in command
    assert "mcp_servers={}" in command


def test_server_approval_request_is_denied_without_response(tmp_path: Path) -> None:
    provider = CodexImageEditProvider(fake_server(tmp_path, bad_request=True), "gpt-6-astra")
    with pytest.raises(CodexCliError, match="unexpected_request"):
        _ = provider.generate(
            operation_id="fixture",
            workspace=tmp_path,
            prompt="Fixture only",
            images=(source(),),
            timeout_seconds=3,
        )


@pytest.mark.parametrize("fault", ["wrong_turn", "duplicate", "failed", "missing"])
def test_incomplete_or_rebound_generation_stream_never_succeeds(
    tmp_path: Path,
    fault: Literal["wrong_turn", "duplicate", "failed", "missing"],
) -> None:
    provider = CodexImageEditProvider(
        fake_server(tmp_path, notification_fault=fault), "gpt-6-astra"
    )
    with pytest.raises(CodexCliError):
        _ = provider.generate(
            operation_id="fixture",
            workspace=tmp_path,
            prompt="Fixture only",
            images=(source(),),
            timeout_seconds=3,
        )
    assert (tmp_path / "codex-image-edit-started.json").is_file()


def test_missing_executable_is_not_ready(tmp_path: Path) -> None:
    provider = CodexImageEditProvider(tmp_path / "absent", "gpt-6-astra")
    readiness = provider.readiness(timeout_seconds=1)
    assert not readiness.ready
    assert not readiness.live_io_verified


def test_oversized_source_rejected_before_provider_admission(tmp_path: Path) -> None:
    runner = FixtureRunner()
    provider = CodexImageEditProvider(Path("/fixture/codex"), "gpt-6-astra", runner)
    oversized = ReviewImage(b"x" * (10 * 1024 * 1024 + 1), "PNG", 12, 16)
    with pytest.raises(CodexCliError, match="input_invalid"):
        _ = provider.generate(
            operation_id="one", workspace=tmp_path, prompt="Fixture", images=(oversized,)
        )
    assert runner.calls == 0
    assert not (tmp_path / "codex-image-edit-started.json").exists()


def test_external_mcp_is_rejected_before_generation(tmp_path: Path) -> None:
    provider = CodexImageEditProvider(fake_server(tmp_path, active_mcp=True), "gpt-6-astra")
    with pytest.raises(CodexCliError, match="mcp"):
        _ = provider.generate(
            operation_id="fixture",
            workspace=tmp_path,
            prompt="Fixture only",
            images=(source(),),
            timeout_seconds=3,
        )
    assert not (tmp_path / "generated.png").exists()


def test_thread_specific_mcp_is_rejected_before_turn(tmp_path: Path) -> None:
    provider = CodexImageEditProvider(fake_server(tmp_path, thread_mcp=True), "gpt-6-astra")
    with pytest.raises(CodexCliError, match="mcp"):
        _ = provider.generate(
            operation_id="fixture",
            workspace=tmp_path,
            prompt="Fixture only",
            images=(source(),),
            timeout_seconds=3,
        )
    assert not (tmp_path / "generated.png").exists()


def test_disabled_servers_have_explicit_per_server_overrides() -> None:
    command = image_edit_command(Path("/fixture/codex"), ("one", "two-server"))
    assert "mcp_servers.one.enabled=false" in command
    assert "mcp_servers.two-server.enabled=false" in command


def test_turn_rpc_uses_named_restricted_profile_supported_by_0153_schema(tmp_path: Path) -> None:
    state = _StreamState(
        ImageEditProcessRequest(Path("/fixture/codex"), "gpt-6-astra", tmp_path, "fixture", (), 10)
    )
    state.thread_id = "fixture-thread"
    turn = state.accept({"id": 5, "result": {"data": [], "nextCursor": None}})[0]
    params = turn["params"]
    assert isinstance(params, dict)
    assert "sandboxPolicy" not in params
    assert params["permissions"] == "trace-image-edit-restricted"


def test_rpc_error_exposes_phase_and_numeric_code_without_provider_text(tmp_path: Path) -> None:
    state = _StreamState(
        ImageEditProcessRequest(Path("/fixture/codex"), "gpt-6-astra", tmp_path, "fixture", (), 10)
    )
    with pytest.raises(CodexCliError, match="codex_image_edit_turn_start_rpc_error_32600") as error:
        _ = state.accept(
            {"id": 3, "error": {"code": -32600, "message": "private prompt secret readOnlyAccess"}}
        )
    assert "private" not in str(error.value)


def test_advertised_image_capability_is_unready_if_restricted_thread_fails(tmp_path: Path) -> None:
    provider = CodexImageEditProvider(fake_server(tmp_path, thread_failure=True), "gpt-6-astra")
    status = provider.readiness(timeout_seconds=3)
    assert status.ready is False
    assert status.reason == "codex_image_edit_thread_start_rpc_error_32603"
    assert not (tmp_path / "generated.png").exists()


def test_process_profile_denies_global_reads_and_network_with_only_job_and_binary_access(
    tmp_path: Path,
) -> None:
    command = image_edit_command(Path(sys.executable), workspace=tmp_path)
    value = next(
        arg for arg in command if arg.startswith("permissions.trace-image-edit-restricted=")
    )
    adapter: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
    parsed = adapter.validate_python(tomllib.loads(value))
    profiles = parsed["permissions"]
    assert isinstance(profiles, dict)
    profile = profiles["trace-image-edit-restricted"]
    assert isinstance(profile, dict)
    assert profile["filesystem"] == {
        ":root": "deny",
        ":minimal": "read",
        str(tmp_path.resolve()): "write",
        str(Path(sys.executable).resolve()): "read",
    }
    assert profile["network"] == {"enabled": False}
    assert 'default_permissions="trace-image-edit-restricted"' in command
