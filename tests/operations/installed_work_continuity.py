# ruff: noqa: INP001
"""Installed-wheel acceptance; fixture reasoning/Slack/Codex, real HTTP and SQLite.

Run outside the checkout without PYTHONPATH using the isolated wheel interpreter:
python /path/to/installed_work_continuity.py --checkout /path/to/checkout --output-dir /tmp/proof
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from pathlib import Path
from threading import Event, Thread
from typing import TYPE_CHECKING, cast
from unittest.mock import patch
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw
from pydantic import TypeAdapter

import ads_booster.marketing.agent_service.http_api as http_module
from ads_booster.contracts.agent_run import AgentRunState, contract_sha256
from ads_booster.contracts.reasoning import (
    ReasoningDecision,
    ReasoningProviderReceipt,
    ReasoningResult,
)
from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.application import MarketingAgentService
from ads_booster.marketing.agent_service.channel_setup import slack_from_env
from ads_booster.marketing.agent_service.http_api import (
    MarketingAgentApi,
    serve_marketing_agent_api,
)
from ads_booster.marketing.agent_service.image_review import review_images
from ads_booster.marketing.agent_service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.marketing.channels.slack import slack_signature
from ads_booster.marketing.channels.slack_events import SlackEvents
from ads_booster.marketing.runtime import SqliteSessionStore
from ads_booster.providers.codex_cli import CodexCli
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Generator
    from http.client import HTTPResponse

    from ads_booster.contracts.reasoning import ReasoningRequest

NOW = datetime.now(UTC)
_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_PORT = TypeAdapter(tuple[str, int])


class FixtureReasoning:
    """Proves transport/state contracts; does not prove natural-language reasoning quality."""

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        has_input = any(
            item.get("schema_version") == "trace.work-continuation.v1" for item in request.evidence
        )
        decision = ReasoningDecision(
            schema_version="trace.reasoning-decision.v1",
            action="stop" if has_input else "request_input",
            expected_outcome="Human work is retained as reported input",
            reasoning_summary="사람 작업 결과를 기다립니다."
            if not has_input
            else "보고받은 결과를 기록했습니다.",
        )
        return ReasoningResult(
            schema_version="trace.reasoning-result.v1",
            decision=decision,
            receipt=ReasoningProviderReceipt(
                schema_version="trace.reasoning-provider-receipt.v1",
                provider_id="fixture.reasoning",
                model_id="fixture",
                request_sha256=contract_sha256(request),
                output_schema_sha256="a" * 64,
                decision_sha256=contract_sha256(decision),
            ),
        )


def setup(root: Path, messages: list[JsonObject]) -> tuple[MarketingAgentService, SlackEvents]:
    installation = root / "fixture-slack.json"
    if not installation.exists():
        _ = installation.write_text(
            json.dumps(
                {
                    "app_id": "A1",
                    "team_id": "T1",
                    "tenant_id": "team",
                    "members": [
                        {"slack_user_id": "U1", "member_id": "member", "can_approve": True}
                    ],
                }
            )
        )
    database = root / "agent.sqlite"
    service = MarketingAgentService(
        SqliteAgentRunRepository(database),
        ToolRegistry(()),
        FixtureReasoning(),
        {},
        SqliteSessionStore(database),
    )
    commands = slack_from_env(
        {
            "TRACE_MARKETING_SLACK_INSTALLATION": str(installation),
            "TRACE_MARKETING_SLACK_SIGNING_SECRET": "fixture-signing-secret",
            "TRACE_MARKETING_PUBLIC_ORIGIN": "https://fixture.invalid",
            "TRACE_MARKETING_SLACK_BOT_TOKEN": "fixture-no-live-token",
            "TRACE_MARKETING_SLACK_CHANNEL_ID": "C1",
        },
        service,
    )
    assert commands is not None

    def send(payload: JsonObject) -> JsonObject:
        messages.append(payload)
        return {"ok": True, "ts": "999.001"}

    commands.sender = send
    return service, SlackEvents(commands, "UBOT", frozenset({"C1"}))


@contextmanager
def local_server(service: MarketingAgentService, events: SlackEvents) -> Generator[str]:
    servers: list[ThreadingHTTPServer] = []

    def capture(
        address: tuple[str, int], handler: type[BaseHTTPRequestHandler]
    ) -> ThreadingHTTPServer:
        server = ThreadingHTTPServer(address, handler)
        servers.append(server)
        return server

    api = MarketingAgentApi(
        service,
        "team",
        "fixture",
        "fixture-bearer",
        slack_commands=events.commands,
        slack_events=events,
    )
    started = Event()
    with patch.object(http_module, "ThreadingHTTPServer", capture):
        thread = Thread(
            target=serve_marketing_agent_api,
            kwargs={"api": api, "host": "127.0.0.1", "port": 0, "on_started": started.set},
            daemon=True,
        )
        thread.start()
        assert started.wait(10), "installed_http_server_did_not_start"
    server = servers[0]
    _, port = _PORT.validate_python(server.server_address)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(5)
        assert not thread.is_alive()


def http(
    base: str, path: str, *, body: bytes | None = None, headers: dict[str, str] | None = None
) -> JsonObject:
    request = Request(base + path, data=body, headers=headers or {})  # noqa: S310 - loopback fixture server.
    with cast("HTTPResponse", urlopen(request, timeout=10)) as response:  # noqa: S310 - loopback fixture.
        assert response.status == 200
        return _JSON.validate_json(response.read())


def message(  # noqa: PLR0913 - explicit signed-message fixture.
    base: str, events: SlackEvents, text: str, ts: str, *, thread: str = "", file_id: str = ""
) -> bytes:
    event: JsonObject = {
        "type": "message" if thread else "app_mention",
        "channel": "C1",
        "user": "U1",
        "text": text if thread else "<@UBOT> " + text,
        "ts": ts,
    }
    if thread:
        event["thread_ts"] = thread
    if file_id:
        event["files"] = [{"id": file_id, "name": "synthetic.png", "mimetype": "image/png"}]
    body = json.dumps(
        {
            "type": "event_callback",
            "api_app_id": "A1",
            "team_id": "T1",
            "event_id": "fixture-" + ts,
            "event": event,
        }
    ).encode()
    timestamp = str(int(NOW.timestamp()))
    headers = {
        "x-slack-request-timestamp": timestamp,
        "x-slack-signature": slack_signature(b"fixture-signing-secret", body, timestamp),
    }
    assert http(base, "/channels/slack/events", body=body, headers=headers) == {"ok": True}
    assert events.work_once(now=NOW)
    assert http(base, "/channels/slack/events", body=body, headers=headers) == {"ok": True}
    assert not events.work_once(now=NOW)
    return body


def assert_run(service: MarketingAgentService, run_id: str, state: AgentRunState) -> None:
    run = service.repository.get("team", run_id)
    assert run is not None
    assert run.state is state


def conversations(root: Path) -> JsonObject:
    messages: list[JsonObject] = []
    service, events = setup(root, messages)
    run_ids: list[str] = []
    cases = [
        (
            "100.001",
            "이 배경으로 달력을 만들 건데 글씨가 잘 보일지 봐줘.",
            "캐릭터는 유지하고 위쪽 여백만 늘려줘.",
        ),
        (
            "200.001",
            "Figma에서 배경은 만들었어. 다음엔 뭘 하면 돼?",
            "직접 적용했어. 이 캡처를 검수해줘.",
        ),
        (
            "300.001",
            "한국어 결과를 일본어와 영어로 만들어줘. 레이아웃은 유지해줘.",
            "일본어와 영어 캡처를 직접 만들었어.",
        ),
    ]
    for index, (ts, first, followup) in enumerate(cases, start=1):
        with local_server(service, events) as base:
            _ = message(base, events, first, ts, file_id=f"F{index}")
            runs = service.repository.list_runs("team")
            new = next(run for run in runs if run.run_id not in run_ids)
            run_ids.append(new.run_id)
            assert_run(service, new.run_id, AgentRunState.AWAITING_INPUT)
        if index == 2:
            service, events = setup(root, messages)
            events.recover()
            assert_run(service, new.run_id, AgentRunState.AWAITING_INPUT)
        with local_server(service, events) as base:
            _ = message(base, events, followup, f"{index}00.002", thread=ts, file_id=f"F{index}0")
            assert_run(service, new.run_id, AgentRunState.COMPLETED)
            view = http(
                base, "/v1/runs/" + new.run_id, headers={"Authorization": "Bearer fixture-bearer"}
            )
            assert new.run_id in json.dumps(view)
    with local_server(service, events) as base:
        _ = message(base, events, "일본어 제목만 고쳐줘.", "300.003", thread="300.001")
        _ = message(base, events, "어디까지 됐어?", "300.004", thread="300.001")
        _ = message(base, events, "잠깐 멈춰줘", "300.005", thread="300.001")
        assert_run(service, run_ids[-1], AgentRunState.AWAITING_INPUT)
        _ = message(base, events, "학생 타깃으로 바꿔서 이어가줘", "300.006", thread="300.001")
        assert_run(service, run_ids[-1], AgentRunState.COMPLETED)
        attempts: list[JsonObject] = []

        def lost(payload: JsonObject) -> JsonObject:
            attempts.append(payload)
            raise TimeoutError

        original = events.commands.sender
        events.commands.sender = lost
        _ = message(base, events, "상태", "300.007", thread="300.001")
        events.recover()
        assert not events.work_once(now=NOW)
        assert len(attempts) == 1
        events.commands.sender = original
    assert len(service.repository.list_runs("team")) == 3
    history = [
        record.model_dump(mode="json")
        for run_id in run_ids
        for record in service.repository.records("team", run_id)
    ]
    serialized = json.dumps(history, ensure_ascii=False)
    assert "human_reported" in serialized
    assert "F20" in serialized
    assert "일본어 제목만" in serialized
    return {
        "scenarios": [
            "A_partial_change_request_retained",
            "B_human_wait_restart",
            "C_locale_followup_same_run",
            "E_status_pause_revise",
        ],
        "run_count": 3,
        "signed_http": True,
        "duplicate_event_no_replay": True,
        "unknown_notification_no_retry": True,
        "same_run_continuation": True,
        "web_projection": True,
        "slack_messages": len(messages),
        "not_proven": [
            "automatic_image_edit",
            "locale_typography_quality",
            "live_slack",
            "real_reasoning",
        ],
    }


def visual(root: Path) -> JsonObject:
    path = root / "synthetic-calendar.png"
    image = Image.new("RGB", (360, 780), "#eef2fa")
    draw = ImageDraw.Draw(image)
    draw.rectangle((24, 30, 220, 58), fill="#182848")
    for row in range(5):
        draw.rectangle((24, 110 + row * 100, 336, 170 + row * 100), outline="#516785", width=2)
    image.save(path)

    def assess(self: CodexCli, prompt: str, schema: JsonObject, **kwargs: object) -> JsonObject:
        _ = self, prompt, schema
        images = TypeAdapter(tuple[Path, ...]).validate_python(kwargs["images"])
        with Image.open(images[0]) as decoded:
            assert decoded.size == (360, 780)
        return {
            "schema_version": "trace.image-visual-assessment.v1",
            "summary": "Fixture assessment",
            "findings": [],
            "uncertainties": ["Synthetic input; no quality verdict"],
            "human_questions": [],
        }

    with patch.object(CodexCli, "run_marketing_image_review_job", assess):
        result = review_images(
            CodexCli(Path("/fixture/codex"), model="fixture"),
            images=(path,),
            request="Inspect calendar whitespace, preserve layout",
            workspace_root=root,
        )
    assert result["product_support_verified"] is False
    return {
        "artifact": str(path),
        "review": result,
        "model": "fixture_not_live",
        "visual_quality_verified": False,
        "rendered_synthetic_png": True,
    }


class Arguments(argparse.Namespace):
    checkout: Path = Path()
    output_dir: Path | None = None


def main() -> None:
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("--checkout", type=Path, required=True)
    _ = parser.add_argument("--output-dir", type=Path)
    args = Arguments()
    _ = parser.parse_args(namespace=args)
    checkout = args.checkout.resolve()
    module = Path(http_module.__file__).resolve()
    assert not module.is_relative_to(checkout), "source_import_is_not_installed_proof"
    assert module.is_relative_to(Path(sys.prefix).resolve()), "module_outside_installed_interpreter"
    assert "site-packages" in module.parts, "editable_source_import_is_not_wheel_proof"
    assert not Path.cwd().resolve().is_relative_to(checkout), "run_outside_checkout"
    assert not os.environ.get("PYTHONPATH"), "remove_pythonpath_for_installed_proof"
    root = args.output_dir or Path(tempfile.mkdtemp(prefix="trace-installed-continuity-"))
    root.mkdir(parents=True, exist_ok=True)
    assert not any(root.iterdir()), "use_empty_isolated_proof_root"
    result: JsonObject = {
        "installed_module": str(module),
        "installed_version": version("trace-appium-capture"),
        "state_root": str(root),
        "continuity": conversations(root),
        "image_review": visual(root),
        "external_writes": False,
        "service_lifecycle": "local_http_only_not_systemd",
    }
    _ = (root / "evidence.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))  # noqa: T201 - machine-readable evidence output.


if __name__ == "__main__":
    main()
