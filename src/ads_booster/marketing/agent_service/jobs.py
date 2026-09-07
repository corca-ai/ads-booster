"""Durable asynchronous web admission; canonical service remains the only Run owner."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentGoal,
    AgentRecordKind,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.contracts.models import ContractModel
from ads_booster.marketing.agent_service.application import CreateAgentRunRequest
from ads_booster.marketing.agent_service.knowledge_ingress import (
    CanonicalKnowledgeIngress,
    KnowledgeIngressSink,
)
from ads_booster.marketing.agent_service.knowledge_ingress_api import (
    ApiIngressRequest,
    build_api_ingress,
)
from ads_booster.marketing.agent_service.oauth import OAuthIdentity
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Generator

    from ads_booster.marketing.agent_service.application import MarketingAgentService


class WebJob(ContractModel):
    job_id: str
    run_id: str
    action: Literal["create", "input", "approval", "resume"]
    goal: AgentGoal | None = None
    budget: AgentBudget | None = None
    evidence: JsonObject | None = None
    expected_revision: int | None = None
    invocation_sha256: str | None = None
    decision: Literal["granted", "rejected"] | None = None
    expires_at: datetime | None = None


_MAX_ID = 160
_ROW: TypeAdapter[tuple[str, ...] | None] = TypeAdapter(tuple[str, ...] | None)
_ROWS: TypeAdapter[list[tuple[str, ...]]] = TypeAdapter(list[tuple[str, ...]])


@dataclass(slots=True)
class AgentJobs:
    service: MarketingAgentService
    knowledge_sink: KnowledgeIngressSink | None = None
    knowledge_ingress: CanonicalKnowledgeIngress = field(init=False)

    def __post_init__(self) -> None:
        """Create additive durable request admission tables."""
        self.knowledge_ingress = CanonicalKnowledgeIngress(
            self.service.repository.database_path, sink=self.knowledge_sink
        )
        with self._db() as db:
            _ = db.execute("""CREATE TABLE IF NOT EXISTS agent_web_jobs (
                tenant TEXT NOT NULL, job_id TEXT NOT NULL, principal TEXT NOT NULL,
                request_json TEXT NOT NULL, state TEXT NOT NULL, error TEXT,
                PRIMARY KEY(tenant,job_id))""")

    @contextmanager
    def _db(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self.service.repository.database_path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def enqueue(
        self,
        tenant: str,
        principal: str,
        job: WebJob,
        *,
        now: datetime | None = None,
    ) -> JsonObject:
        if (
            not job.job_id
            or len(job.job_id) > _MAX_ID
            or not job.run_id
            or len(job.run_id) > _MAX_ID
        ):
            raise ValueError("agent_job_identity_invalid")
        if job.action == "create" and (job.goal is None or job.budget is None):
            raise ValueError("agent_job_goal_required")
        if job.action != "create" and self.service.repository.get(tenant, job.run_id) is None:
            raise ValueError("agent_run_not_found")
        if job.action == "approval" and (not job.invocation_sha256 or not job.decision):
            raise ValueError("agent_job_exact_approval_required")
        if job.action == "input" and (job.evidence is None or job.expected_revision is None):
            raise ValueError("agent_job_evidence_required")
        payload = job.model_dump_json()
        with self._db() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            old = _ROW.validate_python(
                db.execute(
                    "SELECT principal,request_json FROM agent_web_jobs WHERE tenant=? AND job_id=?",
                    (tenant, job.job_id),
                ).fetchone()
            )
            if old is not None and old != (principal, payload):
                raise ValueError("agent_job_idempotency_conflict")
            if old is None:
                _ = db.execute(
                    "INSERT INTO agent_web_jobs VALUES (?,?,?,?,'pending',NULL)",
                    (tenant, job.job_id, principal, payload),
                )
                if job.action in {"create", "input"}:
                    text = (
                        job.goal.objective
                        if job.action == "create" and job.goal is not None
                        else json.dumps(
                            job.evidence or {},
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    )
                    ingress = build_api_ingress(
                        ApiIngressRequest(
                            request_id=job.job_id,
                            run_id=job.run_id,
                            action=job.action,
                            text=text,
                            identity=OAuthIdentity(tenant_id=tenant, principal_id=principal),
                            revision=1,
                            occurred_at=now or datetime.now(UTC),
                        )
                    )
                    _ = self.knowledge_ingress.admit(
                        db, ingress.binding, ingress.event, ingress.envelope
                    )
        return self.status(tenant, job.job_id)

    def status(self, tenant: str, job_id: str) -> JsonObject:
        with self._db() as db:
            row = _ROW.validate_python(
                db.execute(
                    (
                        """SELECT state,COALESCE(error,''),request_json FROM agent_web_jobs
                    WHERE tenant=? AND job_id=?"""
                    ),
                    (tenant, job_id),
                ).fetchone()
            )
        if row is None:
            raise ValueError("agent_job_not_found")
        job = WebJob.model_validate_json(row[2])
        return {"job_id": job_id, "run_id": job.run_id, "state": row[0], "error": row[1]}

    def recover(self) -> None:
        with self._db() as db:
            for tenant, job_id, raw in _ROWS.validate_python(
                db.execute(
                    "SELECT tenant,job_id,request_json FROM agent_web_jobs WHERE state='running'"
                ).fetchall()
            ):
                job = WebJob.model_validate_json(raw)
                replayable = job.action in {"create", "resume"}
                _ = db.execute(
                    "UPDATE agent_web_jobs SET state=?,error=? WHERE tenant=? AND job_id=?",
                    (
                        "pending" if replayable else "blocked",
                        None if replayable else "agent_job_interrupted_check_run",
                        tenant,
                        job_id,
                    ),
                )

    def work_once(self, *, now: datetime) -> bool:
        if self.knowledge_ingress.dispatch_once():
            return True
        with self._db() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            row = _ROW.validate_python(
                db.execute(
                    """SELECT tenant,job_id,principal,request_json FROM agent_web_jobs
                    WHERE state='pending' ORDER BY rowid LIMIT 1"""
                ).fetchone()
            )
            if row is None:
                return False
            _ = db.execute(
                "UPDATE agent_web_jobs SET state='running' WHERE tenant=? AND job_id=?", row[:2]
            )
        tenant, job_id, principal, raw = row
        job = WebJob.model_validate_json(raw)
        try:
            with self.service.execution_lock:
                if job.action == "create":
                    if job.goal is None or job.budget is None:
                        raise ValueError("agent_job_goal_required")  # noqa: TRY301
                    _ = self.service.create(
                        CreateAgentRunRequest(
                            run_id=job.run_id, tenant_id=tenant, goal=job.goal, budget=job.budget
                        ),
                        now=now,
                    )
                elif job.action == "input":
                    run = self.service.repository.get(tenant, job.run_id)
                    if run is None or run.revision != job.expected_revision:
                        raise ValueError("agent_input_revision_changed")  # noqa: TRY301
                    _ = self.service.submit_input(tenant, job.run_id, job.evidence or {}, now=now)
                elif job.action == "resume":
                    _ = self.service.drive(tenant, job.run_id, now=now)
                else:
                    records = self.service.repository.records(tenant, job.run_id)
                    latest = next(
                        (r for r in reversed(records) if r.kind is AgentRecordKind.INVOCATION), None
                    )
                    if (
                        latest is None
                        or contract_sha256(ToolInvocation.model_validate(latest.payload))
                        != job.invocation_sha256
                    ):
                        raise ValueError("agent_approval_invocation_changed")  # noqa: TRY301
                    _ = self.service.decide_approval(
                        tenant,
                        job.run_id,
                        approver_id=principal,
                        granted=job.decision == "granted",
                        expires_at=job.expires_at,
                        now=now,
                        expected_invocation_sha256=job.invocation_sha256,
                    )
            state, error = "done", None
        except Exception:  # noqa: BLE001 - persist a sanitized blocked outcome.
            state, error = "blocked", "agent_job_failed_check_run"
        with self._db() as db:
            _ = db.execute(
                "UPDATE agent_web_jobs SET state=?,error=? WHERE tenant=? AND job_id=?",
                (state, error, tenant, job_id),
            )
        return True
