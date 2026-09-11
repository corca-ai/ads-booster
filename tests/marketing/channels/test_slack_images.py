from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from PIL import Image
from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import AgentRunState
from ads_booster.contracts.reasoning import ReasoningDecision
from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.tools.image_generation import (
    CAPABILITY,
    CodexImages,
    read_artifact,
)
from ads_booster.bootstrap.integrations import (
    AgentServiceIntegrationConfig,
    ConfiguredAgentTools,
)
from ads_booster.channels.slack_events import SlackEvents
from ads_booster.channels.slack_images import SlackImageDelivery
from tests.marketing.agent_service.test_application import (
    _reasoning_result,  # pyright: ignore[reportPrivateUsage]
)
from tests.marketing.agent_service.test_github_issues import Response
from tests.marketing.agent_service.test_integrations import UnusedResearchRunner
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import receive, setup_events
from tests.marketing.channels.test_slack_github_issues import approve

if TYPE_CHECKING:
    from urllib.request import Request

    from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult
    from ads_booster.transport.json_types import JsonObject


class ImageReasoning:
    def __init__(self, *, authorize: bool = False) -> None:
        self.authorize: bool = authorize

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        available = any(
            d.capability_id == CAPABILITY for d in request.capability_snapshot.descriptors
        )
        decision = (
            ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="stop",
                expected_outcome="Draft",
                reasoning_summary="이미지 결과를 확인해 주세요.",
            )
            if request.evidence or not available
            else ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="invoke_tool",
                capability_id=CAPABILITY,
                tool_input={"prompt": "파란 배경의 미니멀한 앱 광고 이미지"},
                expected_outcome="Draft image",
                reasoning_summary="이미지 생성 요청을 검토해 주세요.",
                authorization_message=request.current_user_message if self.authorize else None,
            )
        )
        return _reasoning_result(request, decision)


def configured(
    tmp_path: Path, *, invalid: bool = False, lost: bool = False, bad_host: bool = False
) -> tuple[SlackEvents, list[JsonObject], list[Request], list[list[str]]]:
    commands: list[list[str]] = []
    requests: list[Request] = []
    root = tmp_path / "images"
    runtime = tmp_path / "codex-runtime"

    def runner(command: list[str], prompt: str, timeout: float) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        assert "image_generation" in command
        assert "--ignore-user-config" in command
        assert "shell_tool" in command
        assert "unified_exec" in command
        assert "API_KEY" not in prompt
        assert timeout == 600
        output = runtime / "generated_images/00000000-0000-0000-0000-000000000001/call.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        if invalid:
            _ = output.write_text("not an image")
        else:
            Image.new("RGB", (128, 128), "blue").save(output)
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {"type": "thread.started", "thread_id": "00000000-0000-0000-0000-000000000001"}
            ),
            "",
        )

    def opener(request: Request, *, timeout: float) -> Response:
        _ = timeout
        requests.append(request)
        if request.full_url.endswith("getUploadURLExternal"):
            return Response(
                {
                    "ok": True,
                    "file_id": "F123",
                    "upload_url": "https://evil.test/upload/image"
                    if bad_host
                    else "https://files.slack.com/upload/image",
                },
                200,
            )
        if request.full_url.endswith("completeUploadExternal"):
            if lost:
                raise TimeoutError
            return Response({"ok": True, "files": [{"id": "F123"}]}, 200)
        assert request.get_header("Authorization") is None
        return Response({}, 200)

    owner, messages = setup_events(tmp_path)
    config = ConfiguredAgentTools(
        AgentServiceIntegrationConfig(),
        UnusedResearchRunner(),
        images=CodexImages(Path("codex"), root, "fixture", runner, runtime),
    )
    service = owner.commands.application.service
    service.registry = ToolRegistry(config.descriptors(now=NOW))
    service.tools = config.adapters()
    service.reasoning = ImageReasoning()
    delivery = SlackImageDelivery(root, owner.store.database_path, "fixture", opener)
    return (
        SlackEvents(owner.commands, "UBOT", frozenset({"C1"}), image_delivery=delivery),
        messages,
        requests,
        commands,
    )


@pytest.mark.parametrize("direct_request", [False, True])
def test_image_request_approval_png_upload_thread_and_restart_deduplication(
    tmp_path: Path,
    direct_request: bool,
) -> None:
    owner, messages, requests, commands = configured(tmp_path)
    owner.commands.application.service.reasoning = ImageReasoning(authorize=direct_request)
    receive(owner, text="<@UBOT> 이미지 생성해줘")
    assert owner.work_once(now=NOW)
    if not direct_request:
        assert not commands
        assert not requests
        assert (
            owner.commands.application.service.repository.list_runs("team")[0].state
            is AgentRunState.AWAITING_APPROVAL
        )
        approve(
            owner,
            next(
                line for line in str(messages[-1]["text"]).splitlines() if line.startswith("승인 ")
            ),
        )
    assert len(commands) == 1
    assert len(requests) == 3
    data = requests[-1].data
    assert isinstance(data, bytes)
    payload = TypeAdapter(dict[str, object]).validate_json(data)
    assert payload["channel_id"] == "C1"
    assert payload["thread_ts"] == "100.001"
    assert "초안" in str(messages[-1]["text"])
    artifact = next((tmp_path / "images").glob("*.png"))
    assert read_artifact(artifact.parent, artifact.stem) == requests[1].data
    assert artifact.stat().st_mode & 0o077 == 0
    owner.recover()
    assert not owner.work_once(now=NOW)
    assert len(requests) == 3
    assert len(commands) == 1


@pytest.mark.parametrize("failure", ["invalid", "lost", "bad_host"])
def test_invalid_output_or_uncertain_upload_is_not_retried(tmp_path: Path, failure: str) -> None:
    owner, messages, requests, commands = configured(tmp_path, **{failure: True})
    receive(owner)
    assert owner.work_once(now=NOW)
    approve(
        owner,
        next(line for line in str(messages[-1]["text"]).splitlines() if line.startswith("승인 ")),
    )
    count = len(requests)
    assert count == {"invalid": 0, "lost": 3, "bad_host": 1}[failure]
    owner.recover()
    assert not owner.work_once(now=NOW)
    assert len(requests) == count
    assert len(commands) == 1
    if failure != "invalid":
        assert "확인하지 못했습니다" in str(messages[-1]["text"])


def test_private_dm_cannot_generate_or_share_images(tmp_path: Path) -> None:
    owner, _, requests, commands = configured(tmp_path)
    receive(owner, type="message", channel="D1", channel_type="im", text="이미지 생성해줘")
    assert owner.work_once(now=NOW)
    assert not requests
    assert not commands
