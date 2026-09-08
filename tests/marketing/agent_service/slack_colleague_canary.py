"""Opt-in installed-model multi-turn Slack rehearsal; all Slack writes stay local.

Uses signed synthetic events, the real durable inbox and a restarted Slack adapter
between messages. Inspect each answer; completed is not a quality verdict.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import AgentRecordKind
from ads_booster.agent.core.registry import CapabilityPolicy
from ads_booster.bootstrap.channel_setup import slack_from_env
from ads_booster.bootstrap.lifecycle import (
    InstalledServicePaths,
    build_installed_marketing_agent_service,
)
from ads_booster.tools.web_search import WebSearch
from ads_booster.channels.slack import slack_signature
from ads_booster.channels.slack_events import SlackEvents
from ads_booster.tools.compatibility import DelegatingToolAdapter

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject

MESSAGES = (
    ("지원하는 도구를 짧게 소개해줘", "Count only registered tools, no invented capability."),
    ("마케팅 스킬은 뭐가 있어? 직접 확인해줘", "Use skills.list; report actual installed skills."),
    ("팀 위키가 비어 있는지 확인할 수 있어?", "Unavailable lookup is not an empty wiki."),
    ("인터넷 검색도 할 수 있어?", "Answer the new availability question directly."),
    ("지금 질문에는 한 문장으로만 답해줘", "One sentence; do not repeat tool inventory."),
    (
        (
            "그럼 주간 일정 관리 앱을 홍보할 참고 사례를 검색해서 두 줄로 요약해줘. "
            "검색 결과는 합성 연습 자료이니 실제 시장 근거로 주장하지 마."
        ),
        "Execute research.search and return two lines based on synthetic evidence.",
    ),
)


def search(query: str) -> list[dict[str, str]]:
    _ = query
    return [
        {
            "href": "https://example.test/weekly-review",
            "title": "Synthetic weekly review campaign",
            "body": "Fixture: Sunday planning message highlights a weekly overview; no metrics.",
        }
    ]


class Arguments(argparse.Namespace):
    output_root: Path = Path()
    codex: Path = Path()
    model: str = ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--output-root", type=Path, required=True)
    _ = parser.add_argument("--codex", type=Path, required=True)
    _ = parser.add_argument("--model", required=True)
    args = parser.parse_args(namespace=Arguments())
    root = args.output_root.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    installation = root / "slack.json"
    _ = installation.write_text(
        json.dumps(
            {
                "app_id": "A1",
                "team_id": "T1",
                "tenant_id": "synthetic-rehearsal",
                "members": [{"slack_user_id": "U1", "member_id": "member", "can_approve": False}],
            }
        )
    )
    replies: list[JsonObject] = []

    def send(payload: JsonObject) -> JsonObject:
        replies.append(payload)
        return {"ok": True, "ts": "123.456"}

    for index, (message, criteria) in enumerate(MESSAGES):
        # Rebuild from installed composition, preserving only durable state.
        service = build_installed_marketing_agent_service(
            paths=InstalledServicePaths(root / "service"),
            codex_executable=args.codex,
            model_id=args.model,
            timeout_seconds=180,
        )
        service.capability_policy = CapabilityPolicy(
            allowed_capability_ids=(
                "skills.list",
                "skills.read",
                "creative.prepare",
                "research.search",
            )
        )
        search_adapter = DelegatingToolAdapter(
            capability_id="research.search",
            version="1",
            executor_id="synthetic-search",
            executor=WebSearch(search=search).execute,
        )
        service.tools = {**service.tools, "research.search": search_adapter}
        commands = slack_from_env(
            {
                "TRACE_MARKETING_SLACK_INSTALLATION": str(installation),
                "TRACE_MARKETING_SLACK_SIGNING_SECRET": "fixture-secret",
                "TRACE_MARKETING_PUBLIC_ORIGIN": "https://example.test",
                "TRACE_MARKETING_SLACK_BOT_TOKEN": "fixture-unused",
                "TRACE_MARKETING_SLACK_CHANNEL_ID": "C1",
            },
            service,
        )
        assert commands is not None
        commands.sender = send
        owner = SlackEvents(commands, "UBOT", frozenset({"C1"}))
        now = datetime.now(UTC)
        event: JsonObject = {
            "type": "app_mention" if index == 0 else "message",
            "channel": "C1",
            "user": "U1",
            "ts": f"100.{index + 1:06d}",
            "text": f"<@UBOT> {message}" if index == 0 else message,
        }
        if index:
            event["thread_ts"] = "100.000001"
        body = json.dumps(
            {
                "type": "event_callback",
                "api_app_id": "A1",
                "team_id": "T1",
                "event_id": f"E{index}",
                "event": event,
            }
        ).encode()
        timestamp = str(int(now.timestamp()))
        assert owner.receive(
            body,
            {
                "x-slack-request-timestamp": timestamp,
                "x-slack-signature": slack_signature(b"fixture-secret", body, timestamp),
            },
            now=now,
        ) == {"ok": True}
        assert owner.work_once(now=now)
        runs = service.repository.list_runs("synthetic-rehearsal")
        assert len(runs) == 1
        run = runs[0]
        records = service.repository.records(run.tenant_id, run.run_id)
        conversation = owner.store.conversation_for_run(run.tenant_id, run.run_id)
        assert conversation is not None
        result: JsonObject = {
            "message": message,
            "criteria": criteria,
            "state": run.state.value,
            "requested_model": args.model,
            "dialogue": owner.store.transcript(conversation.conversation_id),
            "decisions": [r.payload for r in records if r.kind is AgentRecordKind.INTENT],
            "provider_receipts": [
                r.payload for r in records if r.kind is AgentRecordKind.REASONING
            ],
            "note": "Synthetic signed Slack and search, real model, no external Slack writes.",
        }
        _ = (root / f"turn-{index + 1}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        print(json.dumps({"turn": index + 1, "state": run.state.value}), flush=True)  # noqa: T201


if __name__ == "__main__":
    main()
