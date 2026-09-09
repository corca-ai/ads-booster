"""Real-model, installed-knowledge Slack rehearsal; no external Slack delivery."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from typing import TYPE_CHECKING, Annotated, Final, TypedDict

import typer
from pydantic import TypeAdapter

from ads_booster.bootstrap.channel_setup import slack_from_env
from ads_booster.bootstrap.lifecycle import (
    InstalledServicePaths,
    build_installed_knowledge_runtime,
    build_installed_marketing_agent_service,
)
from ads_booster.channels.slack import slack_signature
from ads_booster.channels.slack_events import SlackEvents
from ads_booster.contracts.agent_run import AgentRecordKind
from ads_booster.knowledge.configuration import KnowledgeSettings
from ads_booster.knowledge.messages import MessageValidationError
from ads_booster.providers.codex_cli import CodexCli

if TYPE_CHECKING:
    from ads_booster.knowledge.runtime import KnowledgeRuntime
    from ads_booster.transport.json_types import JsonObject

MESSAGES: Final = (
    (
        "가상 QA 테스트야. 실제 업무에 적용하지 마. 식별자는 청록별-7319, 캠페인은 모래숲-QA, "
        "대상은 새벽 배송 기사, 예산은 37만 원, 금지 표현은 무조건이야. 한국어 세 문장 이내로 "
        "답하고 이 조건으로 광고 문구 하나를 써줘."
    ),
    (
        "피드백: 휴식 대신 출근 전 준비를 강조하고 느낌표는 쓰지 마. 예산은 41만 원으로 "
        "정정할게. 나머지 조건은 유지해서 다시 써줘."
    ),
    "잠깐 다른 질문이야. CTA가 무엇인지 한 문장으로 설명해줘.",
    (
        "아까 QA로 돌아가자. 테스트 식별자와 현재 대상, 예산, 금지 표현, 수정된 문구 방향, "
        "문장부호 조건을 회상하고 새 문구 하나를 써줘. 캠페인 종료일도 알고 있는지 말해줘."
    ),
    (
        "새 대화야. 모래숲-QA의 식별자, 최신 대상과 예산, 금지 표현, 수정된 문구 방향과 "
        "문장부호 조건을 기억하니? 확인할 수 있는 값만 말하고 모르면 모른다고 해줘."
    ),
    (
        "모래숲-QA의 최신 조건과 수정 피드백을 다음 새 대화에서도 사용하도록 기억해줘. "
        "가상 QA에만 적용하고 실제 업무 선호로 일반화하지 마. 실제 저장이나 승인 요청이 "
        "필요하면 가능한 도구로 진행하고, 할 수 없는 일을 했다고 하지 마."
    ),
    (
        "재시작 뒤 새 대화에서 확인할게. 모래숲-QA의 최신 예산, 수정된 문구 방향과 "
        "문장부호 조건을 알려줘. 확인할 수 없으면 모른다고 해줘."
    ),
    (
        "별도 프로젝트 자작나무-QA의 예산은 얼마야? "
        "이 프로젝트에 대해 알려준 적이 없다면 모른다고 해줘."
    ),
)


class SchedulerSample(TypedDict):
    observed_at: str
    elapsed_seconds: float
    worked: bool
    active: bool
    batches: list[tuple[str, str, str, str]]
    jobs: list[tuple[str, str, str | None]]


def drain_naturally(
    runtime: KnowledgeRuntime, root: Path
) -> tuple[list[SchedulerSample], str | None]:
    samples: list[SchedulerSample] = []
    started = monotonic()
    while monotonic() - started < 300:
        worked = runtime.run_once()
        with closing(
            sqlite3.connect(f"file:{root / 'knowledge/index.sqlite'}?mode=ro", uri=True)
        ) as connection:
            batches = TypeAdapter(list[tuple[str, str, str, str]]).validate_python(
                connection.execute(
                    """SELECT batch_id,state,first_event_at,batch_deadline
                    FROM curation_batches ORDER BY batch_id"""
                ).fetchall()
            )
            jobs = TypeAdapter(list[tuple[str, str, str | None]]).validate_python(
                connection.execute(
                    "SELECT job_id,state,reason_code FROM jobs ORDER BY job_id"
                ).fetchall()
            )
        samples.append(
            {
                "observed_at": datetime.now(UTC).isoformat(),
                "elapsed_seconds": monotonic() - started,
                "worked": worked,
                "active": runtime.active,
                "batches": batches,
                "jobs": jobs,
            }
        )
        pending = any(row[1] in {"collecting", "ready", "running"} for row in batches) or any(
            row[1] in {"queued", "running", "waiting_dependency"} for row in jobs
        )
        if batches and not pending and not runtime.active and not worked:
            break
        sleep(1)
    else:
        return samples, "natural_scheduler_timeout"
    return samples, None


def recall_message(message: str) -> str:
    message = message.replace(
        "답하고 이 조건으로 광고 문구 하나를 써줘.", "답하고 이 조건을 확인해줘."
    )
    message = message.replace(
        "나머지 조건은 유지해서 다시 써줘.", "나머지 조건은 유지하고 최신 조건을 확인해줘."
    )
    message = message.replace("회상하고 새 문구 하나를 써줘.", "회상해줘.")
    return message + " 콘텐츠 제작 요청이 아니라 대화 기억 확인이야."


def conversation_tenants(database: Path) -> tuple[str, ...]:
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        rows = TypeAdapter(list[tuple[str]]).validate_python(
            connection.execute(
                "SELECT DISTINCT json_extract(data_json, '$.tenant_id') FROM slack_conversations"
            ).fetchall()
        )
    return tuple(row[0] for row in rows)


def file_suffix(channel_id: str, slack_user_id: str) -> str:
    return ("" if channel_id == "C1" else f"-{channel_id}") + (
        "" if slack_user_id == "U1" else f"-{slack_user_id}"
    )


def turn_message(turn: int, recall_only: bool, message_text: str | None) -> str:
    if message_text is not None:
        return message_text
    if turn >= len(MESSAGES):
        error = "Turns beyond 7 require --message-text."
        raise typer.BadParameter(error, param_hint="--turn")
    return recall_message(MESSAGES[turn]) if recall_only else MESSAGES[turn]


def main(  # noqa: PLR0913 - Typer exposes each independent canary option.
    *,
    root: Annotated[Path, typer.Option()],
    turn: Annotated[int, typer.Option(min=0)],
    recall_only: Annotated[bool, typer.Option()] = False,
    natural_drain: Annotated[bool, typer.Option()] = False,
    channel_id: Annotated[str, typer.Option()] = "C1",
    message_text: Annotated[str | None, typer.Option()] = None,
    slack_user_id: Annotated[str, typer.Option()] = "U1",
    thread_ts: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Execute one turn per process, preserving only installed durable state."""
    message = turn_message(turn, recall_only, message_text)
    root = root.resolve()
    paths = InstalledServicePaths(root / "service")
    paths.prepare()
    settings = KnowledgeSettings(
        root=root / "knowledge", control_root=root / "control", policy_path=root / "policy.json"
    )
    installation = root / "slack.json"
    _ = installation.write_text(
        json.dumps(
            {
                "app_id": "A1",
                "team_id": "T1",
                "tenant_id": "memory-rehearsal",
                "members": [
                    {"slack_user_id": "U1", "member_id": "member", "can_approve": True},
                    {"slack_user_id": "U2", "member_id": "member-2", "can_approve": True},
                ],
            }
        )
    )
    knowledge = build_installed_knowledge_runtime(
        settings=settings,
        service_database=paths.database,
        codex=CodexCli(executable=Path("/usr/local/bin/codex"), model="gpt-6-astra"),
        model_id="gpt-6-astra",
    )
    with closing(knowledge.runtime):
        service = build_installed_marketing_agent_service(
            paths=paths,
            codex_executable=Path("/usr/local/bin/codex"),
            model_id="gpt-6-astra",
            timeout_seconds=180,
            knowledge=knowledge.adapter,
        )
        commands = slack_from_env(
            {
                "TRACE_MARKETING_SLACK_INSTALLATION": str(installation),
                "TRACE_MARKETING_SLACK_SIGNING_SECRET": "fixture-secret",
                "TRACE_MARKETING_PUBLIC_ORIGIN": "https://example.test",
                "TRACE_MARKETING_SLACK_BOT_TOKEN": "fixture-unused",
                "TRACE_MARKETING_SLACK_CHANNEL_ID": channel_id,
            },
            service,
        )
        assert commands is not None
        replies: list[JsonObject] = []

        def send(payload: JsonObject) -> JsonObject:
            replies.append(payload)
            return {"ok": True, "ts": f"900.{turn:06d}"}

        commands.sender = send
        owner = SlackEvents(commands, "UBOT", frozenset({channel_id}))
        now = datetime.now(UTC)
        message_ts = f"{int(now.timestamp())}.{turn + 1:06d}"
        thread_path = root / (
            "thread-ts.txt"
            if channel_id == "C1" and slack_user_id == "U1"
            else f"thread-{channel_id}-{slack_user_id}.txt"
            if slack_user_id != "U1"
            else f"thread-{channel_id}.txt"
        )
        if turn == 0:
            _ = thread_path.write_text(thread_ts if thread_ts is not None else message_ts)
        event: JsonObject = {
            "type": "message" if channel_id.startswith("D") else "app_mention",
            "channel": channel_id,
            "channel_type": "im" if channel_id.startswith("D") else "channel",
            "user": slack_user_id,
            "ts": message_ts,
            "text": f"<@UBOT> {message}",
        }
        if thread_ts is not None or turn in {1, 2, 3, 5}:
            event["thread_ts"] = thread_ts if thread_ts is not None else thread_path.read_text()
        body = json.dumps(
            {
                "type": "event_callback",
                "api_app_id": "A1",
                "team_id": "T1",
                "event_id": f"memory-{channel_id}-E{turn}{file_suffix('C1', slack_user_id)}",
                "event": event,
            }
        ).encode()
        stamp = str(int(now.timestamp()))
        assert owner.receive(
            body,
            {
                "x-slack-request-timestamp": stamp,
                "x-slack-signature": slack_signature(b"fixture-secret", body, stamp),
            },
            now=now,
        ) == {"ok": True}
        assert owner.work_once(now=now)
        while owner.work_once(now=datetime.now(UTC)):
            pass
        curation_error = None
        scheduler_samples: list[SchedulerSample] = []
        drain_started = monotonic()
        try:
            if natural_drain:
                scheduler_samples, curation_error = drain_naturally(knowledge.runtime, root)
            else:
                knowledge.runtime.run_until_idle(flush_batches=True)
        except MessageValidationError as error:
            curation_error = error.code
        records_by_run: list[JsonObject] = []
        for run in (
            run
            for tenant in conversation_tenants(paths.database)
            for run in service.repository.list_runs(tenant)
        ):
            records = service.repository.records(run.tenant_id, run.run_id)
            conversation = owner.store.conversation_for_run(run.tenant_id, run.run_id)
            assert conversation is not None
            records_by_run.append(
                {
                    "run_id": run.run_id,
                    "state": run.state.value,
                    "conversation": conversation.model_dump(mode="json"),
                    "dialogue": owner.store.transcript(conversation.conversation_id),
                    "records": [
                        r.model_dump(mode="json")
                        for r in records
                        if r.kind
                        in {
                            AgentRecordKind.INTENT,
                            AgentRecordKind.REASONING,
                            AgentRecordKind.RECEIPT,
                            AgentRecordKind.EVIDENCE,
                        }
                    ],
                }
            )
        result = {
            "turn": turn,
            "channel_id": channel_id,
            "slack_user_id": slack_user_id,
            "message_ts": message_ts,
            "thread_ts": event.get("thread_ts", message_ts),
            "message": message,
            "replies": replies,
            "runs": records_by_run,
            "model": "gpt-6-astra",
            "curation_error": curation_error,
            "scheduler_samples": scheduler_samples,
            "drain_elapsed_seconds": monotonic() - drain_started,
            "boundary": (
                "real model and installed runtime; synthetic signed Slack; "
                + ("natural scheduler ticks; " if natural_drain else "forced batch drain; ")
                + "no approval"
            ),
        }
        _ = (root / f"turn-{turn}{file_suffix(channel_id, slack_user_id)}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        typer.echo(
            json.dumps(
                {"turn": turn, "curation_error": curation_error, "replies": replies},
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    typer.run(main)
