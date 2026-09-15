"""Real Slack slash-command ingress with durable admission and one canonical service."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

from pydantic import TypeAdapter

from ads_booster.agent.service.drive_work import DriveOrigin, DriveWorkQueue
from ads_booster.channels.contracts import (
    ChannelApprovalRequest,
    ChannelKind,
    ChannelRunRequest,
)
from ads_booster.channels.github_results import issue_results
from ads_booster.channels.http.browser_login import https_origin
from ads_booster.channels.slack_run_status import conversational_answer, run_status
from ads_booster.channels.task_result_bindings import (
    bind_result,
    matches_result,
    record_delivery,
    sync_command_deliveries,
)
from ads_booster.channels.task_results import (
    BoundedCompletionRenderer,
    result_for,
)
from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentGoal,
    AgentRunState,
    contract_sha256,
)
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from ads_booster.channels.base import ChannelApplicationAdapter
    from ads_booster.channels.slack import SlackRequestVerifier
    from ads_booster.contracts.agent_run import AgentRun

_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_MAX_TEXT = 8000
_MAX_REVIEW_PAGE_DIGITS = 6
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
    new_run_budget: AgentBudget = field(
        default_factory=lambda: AgentBudget(max_tool_calls=32, max_cost_units=50)
    )
    drive_queue: DriveWorkQueue = field(init=False)

    def __post_init__(self) -> None:
        """Use the canonical database; retain only normalized, non-secret commands."""
        _ = https_origin(self.application.result_base_url)
        self.drive_queue = DriveWorkQueue(self.application.store.database_path)
        self.application.service.drive_admission = self.drive_queue.transition
        self.application.service.renderer = BoundedCompletionRenderer(
            include_links=self.public_links
        )
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
                "text": self.review_input(identity.tenant_id, run_id, page),
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
        self.drive_queue.recover("slack_command")
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
            sync_command_deliveries(db)

    def work_once(self, *, now: datetime) -> bool:
        with self._connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            row = _ROW.validate_python(
                db.execute(
                    """SELECT job_id,user_id,command_json FROM slack_command_jobs AS job WHERE
                    state='pending' AND NOT EXISTS (
                        SELECT 1 FROM slack_command_jobs AS active
                        WHERE active.state='running'
                        AND json_extract(active.command_json,'$.run_id')
                            =json_extract(job.command_json,'$.run_id')
                    ) ORDER BY rowid LIMIT 1"""
                ).fetchone()
            )
            if row is not None:
                _ = db.execute(
                    "UPDATE slack_command_jobs SET state='running' WHERE job_id=?", (row[0],)
                )
        if row is not None:
            job_id, user_id, raw = row
            command = _JSON.validate_json(raw)
            defer_notification = False
            notification_tenant = ""
            try:
                installation = self.application.store.resolve_installation(
                    ChannelKind.SLACK, self.team_id
                )
                identity = self.application.store.resolve_identity(
                    installation.installation_id, user_id
                )
                notification_tenant = identity.tenant_id
                if user_id not in self.allowed_user_ids:
                    raise ValueError("slack_user_not_allowed")  # noqa: TRY301
                run_id, action, text = (
                    str(command["run_id"]),
                    command["action"],
                    str(command["text"]),
                )
                origin = DriveOrigin(
                    tenant_id=identity.tenant_id,
                    run_id=run_id,
                    channel="slack_command",
                    principal_id=user_id,
                    event_id=job_id,
                )
                with self.drive_queue.ownership(origin=origin, now=now):
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
                                    success_criteria=(text,),
                                    context={},
                                ),
                                budget=self.new_run_budget,
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
                                expires_at=(
                                    now + timedelta(minutes=5) if action == "approve" else None
                                ),
                            ),
                            now=now,
                        )
                    else:
                        with self.application.service.run_locks.hold(identity.tenant_id, run_id):
                            run = self.application.service.repository.get(
                                identity.tenant_id, run_id
                            )
                            if run is None or run.revision != command["revision"]:
                                raise ValueError(  # noqa: TRY301
                                    "agent_input_revision_changed"
                                )
                            _ = self.application.service.submit_input(
                                identity.tenant_id, run_id, {"note": text}, now=now
                            )
                result, state = (
                    self.summary(identity.tenant_id, run_id, include_status=False),
                    "done",
                )
                current = self.application.service.repository.get(identity.tenant_id, run_id)
                defer_notification = current is not None and current.state.value == "running"
            except Exception:  # noqa: BLE001 - record failure without repeating effects.
                result, state = (
                    f"실행 처리가 중단됐습니다. 웹에서 상태를 확인하세요: {self.application.result_url(str(command['run_id']))}",  # noqa: E501 - complete result link.
                    "blocked",
                )
            with self._connect() as db:
                _ = db.execute(
                    """UPDATE slack_command_jobs SET state=?,result_text=?,
                    notification_state=CASE WHEN ? AND notification_state='pending'
                    THEN 'deferred' ELSE notification_state END WHERE job_id=?""",
                    (state, result, defer_notification, job_id),
                )
                run = self.application.service.repository.get(
                    notification_tenant, str(command["run_id"])
                )
                if run is not None:
                    self._bind_job_result(db, run, job_id)
            if not defer_notification:
                self.drive_queue.notification_persisted(notification_tenant, job_id)
        return self._notify() or row is not None or self._drive_once(now)

    def _drive_once(self, now: datetime) -> bool:
        claim = self.drive_queue.claim("slack_command", now)
        if claim is None:
            return False
        with (
            self.application.service.run_locks.hold(claim.origin.tenant_id, claim.origin.run_id),
            self.drive_queue.ownership(claim=claim, now=now),
        ):
            try:
                installation = self.application.store.resolve_installation(
                    ChannelKind.SLACK, self.team_id
                )
                identity = self.application.store.resolve_identity(
                    installation.installation_id, claim.origin.principal_id
                )
            except ValueError:
                self.drive_queue.block(claim, "actor_revoked", now)
                return True
            if (
                claim.origin.principal_id not in self.allowed_user_ids
                or identity.tenant_id != claim.origin.tenant_id
            ):
                self.drive_queue.block(claim, "actor_revoked", now)
                return True
            if claim.phase == "drive":
                _ = self.application.service.drive(identity.tenant_id, claim.origin.run_id, now=now)
                return True
            run = self.application.service.repository.get(identity.tenant_id, claim.origin.run_id)
            if run is not None and run.state.value != "running":
                with self._connect() as db:
                    _ = db.execute(
                        """UPDATE slack_command_jobs SET result_text=?,
                            notification_state='pending' WHERE job_id=?
                            AND notification_state='deferred'""",
                        (
                            self.summary(identity.tenant_id, run.run_id, include_status=False),
                            claim.origin.event_id,
                        ),
                    )
                    self._bind_job_result(db, run, claim.origin.event_id)
            _ = self.drive_queue.discard(claim, now=now)
        _ = self._notify()
        return True

    def _bind_job_result(self, db: sqlite3.Connection, run: AgentRun, job_id: str) -> None:
        row = _ROW.validate_python(
            db.execute(
                "SELECT result_text FROM slack_command_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        )
        projected = result_for(
            run, self.application.service.repository.records(run.tenant_id, run.run_id)
        )
        if row is not None and row[0] == projected.text:
            bind_result(db, "command:" + job_id, projected)

    def _notify(self) -> bool:
        with self._connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            row = _ROW.validate_python(
                db.execute(
                    """SELECT job_id,result_text,command_json,user_id FROM slack_command_jobs
                    WHERE state IN
                    ('done','blocked') AND notification_state='pending' ORDER BY rowid LIMIT 1"""
                ).fetchone()
            )
            if row is None:
                return False
            command = _JSON.validate_json(row[2])
            installation = self.application.store.resolve_installation(
                ChannelKind.SLACK, self.team_id
            )
            identity = self.application.store.resolve_identity(installation.installation_id, row[3])
            run = self.application.service.repository.get(
                identity.tenant_id, str(command["run_id"])
            )
            if run is not None and not matches_result(
                db,
                "command:" + row[0],
                replace(
                    result_for(
                        run, self.application.service.repository.records(run.tenant_id, run.run_id)
                    ),
                    text=row[1],
                ),
            ):
                _ = db.execute(
                    "UPDATE slack_command_jobs SET notification_state='superseded' WHERE job_id=?",
                    (row[0],),
                )
                record_delivery(db, "command:" + row[0], "superseded")
                return True
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
                        {
                            "type": "section",
                            "text": {"type": "plain_text", "text": row[1][offset : offset + 2500]},
                        }
                        for offset in range(0, len(row[1]), 2500)
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
            record_delivery(db, "command:" + row[0], state)
        return True

    def review_input(self, tenant_id: str, run_id: str, argument: str) -> str:
        """Handle human input without changing the pending approval or review evidence."""
        page = argument.strip() or "1"
        if not page.isascii() or not page.isdecimal() or len(page) > _MAX_REVIEW_PAGE_DIGITS:
            return (
                "검토는 승인 전에 실행할 내용을 확인하는 명령입니다. "
                "이 작업 스레드에 '검토 1'을 보내 첫 페이지부터 확인하세요. "
                "슬래시 명령은 '/trace review 실행ID 1'입니다."
            )
        try:
            return self.review(tenant_id, run_id, int(page))
        except ValueError as exc:
            if exc.args == ("agent_approval_not_pending",):
                return "현재 승인 대기 중인 작업이 없습니다. 먼저 작업 상태를 확인하세요."
            if exc.args == ("agent_review_page_invalid",):
                return (
                    "검토 페이지 범위를 벗어났습니다. 첫 페이지(1)에서 전체 페이지 수를 확인하세요."
                )
            raise

    def review(self, tenant_id: str, run_id: str, page: int) -> str:
        pages = self.review_pages(tenant_id, run_id)
        if not 1 <= page <= len(pages):
            raise ValueError("agent_review_page_invalid")
        return pages[page - 1]

    def review_pages(self, tenant_id: str, run_id: str) -> tuple[str, ...]:
        """One authoritative rendering for exact approval review and delivery evidence."""
        run = self.application.service.repository.get(tenant_id, run_id)
        if run is None or run.state.value != "awaiting_approval":
            raise ValueError("agent_approval_not_pending")
        invocation = self.application.service.pending_approval(tenant_id, run_id)
        if invocation is None:
            raise ValueError("agent_approval_not_pending")
        content = invocation.model_dump_json(indent=2)
        size = 1800
        pages = (len(content) + size - 1) // size
        return tuple(
            "\n".join(
                (
                    f"실행: {run_id}",
                    f"승인해시: {contract_sha256(invocation)}",
                    f"페이지 {page}/{pages} (모든 페이지 확인 후 이 해시로 승인)",
                    content[(page - 1) * size : page * size],
                )
            )
            for page in range(1, pages + 1)
        )

    def summary(self, tenant_id: str, run_id: str, *, include_status: bool = True) -> str:
        with self.application.service.run_locks.hold(tenant_id, run_id):
            run = self.application.service.repository.get(tenant_id, run_id)
            if run is None:
                raise ValueError("agent_run_not_found")
            records = self.application.service.repository.records(tenant_id, run_id)
            if not include_status and run.state is not AgentRunState.AWAITING_APPROVAL:
                return result_for(run, records).text
            return self._summary(tenant_id, run_id)

    def _summary(self, tenant_id: str, run_id: str) -> str:
        run = self.application.service.repository.get(tenant_id, run_id)
        if run is None:
            raise ValueError("agent_run_not_found")
        records = self.application.service.repository.records(tenant_id, run_id)
        steps = self.application.service.repository.steps(tenant_id, run_id)
        status = run_status(run, steps)
        lines = [
            f"실행: {run_id}",
            f"상태: {status}",
        ]
        if self.public_links:
            lines.append(str(self.application.result_url(run_id)))
        if run.state is AgentRunState.AWAITING_APPROVAL:
            invocation = self.application.service.pending_approval(tenant_id, run_id)
            if invocation is not None:
                lines += [
                    f"승인 전 /trace review {run_id} 1 로 전체 내용을 확인하세요.",
                    f"/trace approve {run_id} {contract_sha256(invocation)}",
                ]
        elif run.state is AgentRunState.COMPLETED:
            lines.append(result_for(run, records).text[:1800])
        elif run.state is AgentRunState.AWAITING_INPUT:
            lines.append(conversational_answer(run, steps, records)[:1800])
        if result := issue_results(records):
            lines.append(result)
        return "\n".join(lines)
