"""Real Slack slash-command ingress with durable admission and one canonical service."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentGoal,
    AgentRecordKind,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.marketing.agent_service.browser_login import https_origin
from ads_booster.marketing.channels.base import ChannelApplicationAdapter
from ads_booster.marketing.channels.contracts import (
    ChannelApprovalRequest,
    ChannelKind,
    ChannelRunRequest,
)
from ads_booster.marketing.channels.github_results import issue_results
from ads_booster.marketing.channels.slack import SlackRequestVerifier
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_MAX_TEXT = 8000
_MAX_TRIGGER = 256
_HELP = """/trace 조사할 목표 — 새 실행
/trace status 실행ID — 결과 확인
/trace review 실행ID 페이지번호 — 승인할 정확한 내용 확인
/trace input 실행ID 추가 근거 — 이어서 실행
/trace approve 실행ID 승인해시 또는 /trace reject 실행ID 승인해시"""


_ROW: TypeAdapter[tuple[str, ...] | None] = TypeAdapter(tuple[str, ...] | None)
_ROWS: TypeAdapter[list[tuple[str, ...]]] = TypeAdapter(list[tuple[str, ...]])


@dataclass(slots=True)
class SlackCommands:
    application: ChannelApplicationAdapter
    verifier: SlackRequestVerifier
    app_id: str
    team_id: str
    channel_id: str
    allowed_user_ids: frozenset[str]
    sender: Callable[[JsonObject], JsonObject]
    public_links: bool = True

    def __post_init__(self) -> None:
        """Use the canonical database; retain only normalized, non-secret commands."""
        _ = https_origin(self.application.result_base_url)
        with self._connect() as db:
            _ = db.execute("""CREATE TABLE IF NOT EXISTS slack_command_jobs (
                job_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, command_json TEXT NOT NULL,
                state TEXT NOT NULL, result_text TEXT,
                notification_state TEXT NOT NULL DEFAULT 'pending'
            )""")

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self.application.store.database_path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def receive(  # noqa: C901,PLR0912 - signed ingress validation and command admission.
        self, body: bytes, headers: dict[str, str], *, now: datetime
    ) -> JsonObject:
        self.verifier.verify(
            body,
            timestamp=headers.get("x-slack-request-timestamp", ""),
            signature=headers.get("x-slack-signature", ""),
            now=now,
        )
        fields = parse_qs(body.decode(), keep_blank_values=True, max_num_fields=30)
        if any(len(v) != 1 for v in fields.values()):
            raise ValueError("slack_duplicate_field")
        form = {k: v[0] for k, v in fields.items()}
        if (
            form.get("api_app_id") != self.app_id
            or form.get("team_id") != self.team_id
            or form.get("channel_id") != self.channel_id
            or form.get("command") != "/trace"
        ):
            raise ValueError("slack_command_scope_rejected")
        installation = self.application.store.resolve_installation(ChannelKind.SLACK, self.team_id)
        identity = self.application.store.resolve_identity(
            installation.installation_id, form.get("user_id", "")
        )
        if not identity.can_create_runs or identity.external_user_id not in self.allowed_user_ids:
            raise ValueError("slack_user_not_allowed")
        text = form.get("text", "").strip()
        if not text or text == "help":
            return {"response_type": "ephemeral", "text": _HELP}
        if len(text) > _MAX_TEXT:
            raise ValueError("slack_command_too_long")
        action, _, rest = text.partition(" ")
        if action == "review":
            run_id, _, page = rest.strip().partition(" ")
            return {
                "response_type": "ephemeral",
                "text": self.review(identity.tenant_id, run_id, int(page or "1")),
            }
        if action == "status":
            return {
                "response_type": "ephemeral",
                "text": self.summary(identity.tenant_id, rest.strip()),
            }
        trigger = form.get("trigger_id", "")
        if not trigger or len(trigger) > _MAX_TRIGGER:
            raise ValueError("slack_trigger_missing")
        job_id = hashlib.sha256(f"{self.team_id}:{trigger}".encode()).hexdigest()
        command: JsonObject = {"action": "create", "run_id": f"slack-{job_id[:32]}", "text": text}
        if action in {"approve", "reject", "input"}:
            run_id, _, argument = rest.partition(" ")
            run = self.application.service.repository.get(identity.tenant_id, run_id)
            if run is None or not argument.strip():
                raise ValueError("agent_run_not_found_or_input_missing")
            if action in {"approve", "reject"} and not identity.can_approve:
                raise ValueError("slack_reviewer_not_allowed")
            command = {
                "action": action,
                "run_id": run_id,
                "text": argument.strip(),
                "revision": run.revision,
            }
        payload = json.dumps(command, ensure_ascii=False, sort_keys=True)
        with self._connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            old = _ROW.validate_python(
                db.execute(
                    "SELECT user_id, command_json FROM slack_command_jobs WHERE job_id=?", (job_id,)
                ).fetchone()
            )
            if old is not None and old != (identity.external_user_id, payload):
                # A retry of an admitted input still binds the original revision.
                original = _JSON.validate_json(old[1])
                if old[0] != identity.external_user_id or any(
                    original.get(k) != command.get(k) for k in ("action", "run_id", "text")
                ):
                    raise ValueError("slack_command_idempotency_conflict")
            if old is None:
                _ = db.execute(
                    (
                        """INSERT INTO slack_command_jobs(job_id,user_id,command_json,state)
                    VALUES (?,?,?,'pending')"""
                    ),
                    (job_id, identity.external_user_id, payload),
                )
        return {
            "response_type": "ephemeral",
            "text": (
                f"접수했습니다. 실행 ID: {command['run_id']}\n"
                "결과와 승인 요청은 이 채널에 도착합니다."
            ),
        }

    def recover(self) -> None:
        """Only create is replayable after a crash; other uncertain mutations need readback."""
        with self._connect() as db:
            for job_id, raw in _ROWS.validate_python(
                db.execute(
                    "SELECT job_id,command_json FROM slack_command_jobs WHERE state='running'"
                ).fetchall()
            ):
                command = _JSON.validate_json(raw)
                state = "pending" if command["action"] == "create" else "blocked"
                _ = db.execute(
                    "UPDATE slack_command_jobs SET state=?,result_text=? WHERE job_id=?",
                    (
                        state,
                        "서비스가 재시작되었습니다. /trace status로 실행 상태를 확인하세요.",
                        job_id,
                    ),
                )
            _ = db.execute(
                """UPDATE slack_command_jobs SET notification_state='unknown' WHERE
                    notification_state='sending'"""
            )

    def work_once(self, *, now: datetime) -> bool:
        with self._connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            row = _ROW.validate_python(
                db.execute(
                    """SELECT job_id,user_id,command_json FROM slack_command_jobs WHERE
                    state='pending' ORDER BY rowid LIMIT 1"""
                ).fetchone()
            )
            if row is not None:
                _ = db.execute(
                    "UPDATE slack_command_jobs SET state='running' WHERE job_id=?", (row[0],)
                )
        if row is not None:
            job_id, user_id, raw = row
            command = _JSON.validate_json(raw)
            try:
                installation = self.application.store.resolve_installation(
                    ChannelKind.SLACK, self.team_id
                )
                identity = self.application.store.resolve_identity(
                    installation.installation_id, user_id
                )
                if user_id not in self.allowed_user_ids:
                    raise ValueError("slack_user_not_allowed")  # noqa: TRY301
                run_id, action, text = (
                    str(command["run_id"]),
                    command["action"],
                    str(command["text"]),
                )
                if action == "create":
                    _ = self.application.create_run(
                        installation,
                        identity,
                        ChannelRunRequest(
                            schema_version="trace.channel-run-request.v1",
                            delivery_id=job_id,
                            run_id=run_id,
                            goal=AgentGoal(
                                objective=text,
                                success_criteria=("출처와 불확실성이 명확한 답변을 만든다.",),
                                context={},
                            ),
                            budget=AgentBudget(max_tool_calls=8, max_cost_units=50),
                        ),
                        now=now,
                    )
                elif action in {"approve", "reject"}:
                    _ = self.application.decide_approval(
                        installation,
                        identity,
                        ChannelApprovalRequest(
                            schema_version="trace.channel-approval-request.v1",
                            delivery_id=job_id,
                            run_id=run_id,
                            invocation_sha256=text,
                            decision="granted" if action == "approve" else "rejected",
                            expires_at=now + timedelta(minutes=5) if action == "approve" else None,
                        ),
                        now=now,
                    )
                else:
                    with self.application.service.execution_lock:
                        run = self.application.service.repository.get(identity.tenant_id, run_id)
                        if run is None or run.revision != command["revision"]:
                            raise ValueError("agent_input_revision_changed")  # noqa: TRY301
                        _ = self.application.service.submit_input(
                            identity.tenant_id, run_id, {"note": text}, now=now
                        )
                result, state = self.summary(identity.tenant_id, run_id), "done"
            except Exception:  # noqa: BLE001 - record failure without repeating effects.
                result, state = (
                    f"실행 처리가 중단됐습니다. 웹에서 상태를 확인하세요: {self.application.result_url(str(command['run_id']))}",  # noqa: E501 - complete result link.
                    "blocked",
                )
            with self._connect() as db:
                _ = db.execute(
                    "UPDATE slack_command_jobs SET state=?,result_text=? WHERE job_id=?",
                    (state, result, job_id),
                )
        return self._notify() or row is not None

    def _notify(self) -> bool:
        with self._connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            row = _ROW.validate_python(
                db.execute(
                    """SELECT job_id,result_text FROM slack_command_jobs WHERE state IN
                    ('done','blocked') AND notification_state='pending' ORDER BY rowid LIMIT 1"""
                ).fetchone()
            )
            if row is None:
                return False
            _ = db.execute(
                "UPDATE slack_command_jobs SET notification_state='sending' WHERE job_id=?",
                (row[0],),
            )
        # Persist dispatch first. A lost response is unknown, never a new chat.postMessage.
        try:
            response = self.sender(
                {
                    "channel": self.channel_id,
                    "text": row[1],
                    "mrkdwn": False,
                    "unfurl_links": False,
                    "unfurl_media": False,
                    "blocks": [
                        {"type": "section", "text": {"type": "plain_text", "text": row[1][:2900]}}
                    ],
                }
            )
            state = "delivered" if response.get("ok") is True and response.get("ts") else "failed"
        except Exception:  # noqa: BLE001 - record failure without repeating effects.
            state = "unknown"
        with self._connect() as db:
            _ = db.execute(
                "UPDATE slack_command_jobs SET notification_state=? WHERE job_id=?", (state, row[0])
            )
        return True

    def review(self, tenant_id: str, run_id: str, page: int) -> str:
        run = self.application.service.repository.get(tenant_id, run_id)
        if run is None or run.state.value != "awaiting_approval":
            raise ValueError("agent_approval_not_pending")
        records = self.application.service.repository.records(tenant_id, run_id)
        latest = next(r for r in reversed(records) if r.kind is AgentRecordKind.INVOCATION)
        invocation = ToolInvocation.model_validate(latest.payload)
        content = invocation.model_dump_json(indent=2)
        size = 1800
        pages = (len(content) + size - 1) // size
        if not 1 <= page <= pages:
            raise ValueError("agent_review_page_invalid")
        return "\n".join(
            (
                f"실행: {run_id}",
                f"승인해시: {contract_sha256(invocation)}",
                f"페이지 {page}/{pages} (모든 페이지 확인 후 이 해시로 승인)",
                content[(page - 1) * size : page * size],
            )
        )

    def summary(self, tenant_id: str, run_id: str) -> str:
        run = self.application.service.repository.get(tenant_id, run_id)
        if run is None:
            raise ValueError("agent_run_not_found")
        records = self.application.service.repository.records(tenant_id, run_id)
        lines = [
            f"실행: {run_id}",
            f"상태: {run.state.value}",
        ]
        if self.public_links:
            lines.append(str(self.application.result_url(run_id)))
        if run.state.value == "awaiting_approval":
            latest = next(
                (r for r in reversed(records) if r.kind is AgentRecordKind.INVOCATION), None
            )
            if latest is not None:
                invocation = ToolInvocation.model_validate(latest.payload)
                lines += [
                    f"승인 전 /trace review {run_id} 1 로 전체 내용을 확인하세요.",
                    f"/trace approve {run_id} {contract_sha256(invocation)}",
                ]
        else:
            latest = next(
                (r for r in reversed(records) if r.kind is AgentRecordKind.REASONING), None
            )
            if latest is not None:
                decision = latest.payload.get("decision")
                if isinstance(decision, dict):
                    lines.append(str(decision.get("reasoning_summary", ""))[:1800])
        if result := issue_results(records):
            lines.append(result)
        return "\n".join(lines)
