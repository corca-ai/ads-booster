from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from pydantic import TypeAdapter

from ads_booster.agent.service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.agent.service.task_progress import TaskProjection, project_task, task_records
from ads_booster.contracts.agent_run import AgentRunState, AgentStep, AgentStepKind, contract_sha256
from ads_booster.contracts.models import ContractModel

_PROCESS_OWNER_ID = uuid4().hex
_CLAIM_LOST = "drive_queue_claim_lost"
_LEASE_CONFIGURATION_INVALID = "drive_queue_lease_configuration_invalid"
_OWNERSHIP_ARGUMENTS_INVALID = "drive_queue_ownership_arguments_invalid"

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from ads_booster.agent.service.sqlite_repository import RepositoryAdmission
    from ads_booster.contracts.agent_run import AgentRun


class DriveOrigin(ContractModel):
    tenant_id: str
    run_id: str
    channel: Literal["slack", "http", "slack_command", "schedule"]
    principal_id: str
    event_id: str
    conversation_id: str = ""


@dataclass(frozen=True, slots=True)
class DriveClaim:
    origin: DriveOrigin
    revision: int
    phase: Literal["drive", "notify"] = "drive"
    owner_id: str = ""
    lease_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class _DriveOwnership:
    origin: DriveOrigin
    owner_id: str
    phase: Literal["drive", "notify"]
    lease_expires_at: datetime
    release_state: Literal["pending", "notify"]


_ACTIVE_OWNERSHIP: ContextVar[_DriveOwnership | None] = ContextVar(
    "agent_drive_ownership", default=None
)


_ROW: TypeAdapter[tuple[str, int, str] | None] = TypeAdapter(tuple[str, int, str] | None)
_TEXT: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)
_EXISTS: TypeAdapter[tuple[int] | None] = TypeAdapter(tuple[int] | None)
type _LeaseRow = tuple[int, str, str | None, str | None]
_LEASE: TypeAdapter[_LeaseRow | None] = TypeAdapter(_LeaseRow | None)
_TABLE_INFO: TypeAdapter[list[tuple[int, str, str, int, str | None, int]]] = TypeAdapter(
    list[tuple[int, str, str, int, str | None, int]]
)


class DriveAdmissionConflictError(ValueError):
    pass


DriveAdmissionConflict = DriveAdmissionConflictError


class DriveClaimLostError(DriveAdmissionConflictError):
    """The durable queue claim was expired or transferred to another worker."""


@dataclass(frozen=True, slots=True)
class DriveWorkQueue:
    database_path: Path
    owner_id: str = field(default_factory=lambda: _PROCESS_OWNER_ID)
    lease_duration: timedelta = timedelta(minutes=30)

    def __post_init__(self) -> None:
        """Keep runnable slices beside the canonical admission and Run ledger."""
        if not self.owner_id or self.lease_duration <= timedelta(0):
            raise ValueError(_LEASE_CONFIGURATION_INVALID)
        with self.connect() as db:
            _ = db.executescript("""
                CREATE TABLE IF NOT EXISTS agent_drive_origins (
                    tenant_id TEXT NOT NULL, run_id TEXT NOT NULL, origin_json TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,run_id));
                CREATE TABLE IF NOT EXISTS agent_drive_work (
                    tenant_id TEXT NOT NULL, run_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    due_at TEXT NOT NULL, state TEXT NOT NULL, claim_owner TEXT,
                    lease_expires_at TEXT,
                    PRIMARY KEY(tenant_id,run_id));
            """)
            _ = db.execute("BEGIN IMMEDIATE")
            columns = {
                row[1]
                for row in _TABLE_INFO.validate_python(
                    db.execute("PRAGMA table_info(agent_drive_work)").fetchall()
                )
            }
            if "claim_owner" not in columns:
                _ = db.execute("ALTER TABLE agent_drive_work ADD COLUMN claim_owner TEXT")
            if "lease_expires_at" not in columns:
                _ = db.execute("ALTER TABLE agent_drive_work ADD COLUMN lease_expires_at TEXT")
            _ = db.execute(
                """CREATE INDEX IF NOT EXISTS agent_drive_work_claimable
                ON agent_drive_work(state,due_at,lease_expires_at)"""
            )

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def bind(self, origin: DriveOrigin) -> None:
        with self.connect() as db:
            _bind_origin(db, origin)

    def admission(
        self,
        origin: DriveOrigin,
        following: RepositoryAdmission | None = None,
    ) -> RepositoryAdmission:
        def admit(connection: sqlite3.Connection) -> None:
            _bind_origin(connection, origin)
            if following is not None:
                following(connection)

        return admit

    @contextmanager
    def ownership(
        self,
        *,
        now: datetime,
        origin: DriveOrigin | None = None,
        claim: DriveClaim | None = None,
    ) -> Generator[None]:
        """Fence every canonical transition performed by one bounded service slice."""
        if (origin is None) == (claim is None):
            raise ValueError(_OWNERSHIP_ARGUMENTS_INVALID)
        selected_origin = claim.origin if claim is not None else origin
        if selected_origin is None:
            raise ValueError(_OWNERSHIP_ARGUMENTS_INVALID)
        active = _ACTIVE_OWNERSHIP.get()
        if active is not None:
            if (
                active.origin.tenant_id != selected_origin.tenant_id
                or active.origin.run_id != selected_origin.run_id
            ):
                raise DriveClaimLostError(_CLAIM_LOST)
            yield
            return
        ownership = self._take_ownership(selected_origin, claim=claim, now=now)
        token = _ACTIVE_OWNERSHIP.set(ownership)
        try:
            yield
        finally:
            current = _ACTIVE_OWNERSHIP.get()
            _ACTIVE_OWNERSHIP.reset(token)
            self._release_ownership(current or ownership)

    def _take_ownership(
        self, origin: DriveOrigin, *, claim: DriveClaim | None, now: datetime
    ) -> _DriveOwnership:
        if claim is not None:
            if claim.owner_id != self.owner_id or claim.lease_expires_at is None:
                raise DriveClaimLostError(_CLAIM_LOST)
            with self.connect() as db:
                _require_owned_claim(db, claim, now)
            return _DriveOwnership(
                origin=origin,
                owner_id=claim.owner_id,
                phase=claim.phase,
                lease_expires_at=claim.lease_expires_at,
                release_state="notify" if claim.phase == "notify" else "pending",
            )
        lease_expires_at = now + self.lease_duration
        release_state: Literal["pending", "notify"] = "pending"
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            _recover_run_expired(db, origin.tenant_id, origin.run_id, now)
            row = _LEASE.validate_python(
                db.execute(
                    """SELECT revision,state,claim_owner,lease_expires_at FROM agent_drive_work
                    WHERE tenant_id=? AND run_id=?""",
                    (origin.tenant_id, origin.run_id),
                ).fetchone()
            )
            if row is not None:
                if row[1] not in {"pending", "notify"}:
                    raise DriveClaimLostError(_CLAIM_LOST)
                release_state = "notify" if row[1] == "notify" else "pending"
                cursor = db.execute(
                    """UPDATE agent_drive_work SET state='running',claim_owner=?,
                    lease_expires_at=? WHERE tenant_id=? AND run_id=? AND revision=? AND state=?
                    AND claim_owner IS NULL AND lease_expires_at IS NULL""",
                    (
                        self.owner_id,
                        lease_expires_at.isoformat(),
                        origin.tenant_id,
                        origin.run_id,
                        row[0],
                        row[1],
                    ),
                )
                if cursor.rowcount != 1:
                    raise DriveClaimLostError(_CLAIM_LOST)
            _bind_origin(db, origin)
        return _DriveOwnership(
            origin=origin,
            owner_id=self.owner_id,
            phase="drive",
            lease_expires_at=lease_expires_at,
            release_state=release_state,
        )

    def _release_ownership(self, ownership: _DriveOwnership) -> None:
        with self.connect() as db:
            _ = db.execute(
                """UPDATE agent_drive_work SET state=?,claim_owner=NULL,lease_expires_at=NULL
                WHERE tenant_id=? AND run_id=? AND state IN ('running','notifying')
                AND claim_owner=?""",
                (
                    ownership.release_state,
                    ownership.origin.tenant_id,
                    ownership.origin.run_id,
                    ownership.owner_id,
                ),
            )

    def transition(self, run: AgentRun, task: TaskProjection, now: datetime) -> RepositoryAdmission:
        def admission(connection: sqlite3.Connection) -> None:
            owner = _TEXT.validate_python(
                connection.execute(
                    "SELECT origin_json FROM agent_drive_origins WHERE tenant_id=? AND run_id=?",
                    (run.tenant_id, run.run_id),
                ).fetchone()
            )
            if owner is None:
                return
            origin = DriveOrigin.model_validate_json(owner[0])
            claim = _LEASE.validate_python(
                connection.execute(
                    """SELECT revision,state,claim_owner,lease_expires_at FROM agent_drive_work
                    WHERE tenant_id=? AND run_id=?""",
                    (run.tenant_id, run.run_id),
                ).fetchone()
            )
            queue_state = _target_queue_state(connection, run, task)
            ownership = _ACTIVE_OWNERSHIP.get()
            ownership_state = _transition_ownership(claim, ownership, run, now)
            if ownership_state == "already" and ownership is not None:
                _ = _ACTIVE_OWNERSHIP.set(replace(ownership, release_state=queue_state))
                return
            if task.checkpoint.disposition == "satisfied":
                _fence_pending_input(connection, origin)
            if task.checkpoint.wait_reason in {"actor_revoked", "authorization_unavailable"}:
                _ = connection.execute(
                    "DELETE FROM agent_drive_work WHERE tenant_id=? AND run_id=?",
                    (run.tenant_id, run.run_id),
                )
                return
            if ownership_state == "active" and ownership is not None:
                _ = connection.execute(
                    """INSERT INTO agent_drive_work(
                        tenant_id,run_id,revision,due_at,state,claim_owner,lease_expires_at
                    ) VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(tenant_id,run_id) DO UPDATE SET revision=excluded.revision,
                    due_at=excluded.due_at,state='running',claim_owner=excluded.claim_owner,
                    lease_expires_at=excluded.lease_expires_at""",
                    (
                        run.tenant_id,
                        run.run_id,
                        run.revision + 1,
                        now.isoformat(),
                        "running",
                        ownership.owner_id,
                        ownership.lease_expires_at.isoformat(),
                    ),
                )
                _ = _ACTIVE_OWNERSHIP.set(replace(ownership, release_state=queue_state))
                return
            _ = connection.execute(
                """INSERT INTO agent_drive_work(
                    tenant_id,run_id,revision,due_at,state,claim_owner,lease_expires_at
                ) VALUES(?,?,?,?,?,NULL,NULL)
                ON CONFLICT(tenant_id,run_id) DO UPDATE SET revision=excluded.revision,
                due_at=excluded.due_at,state=excluded.state,claim_owner=NULL,
                lease_expires_at=NULL""",
                (run.tenant_id, run.run_id, run.revision + 1, now.isoformat(), queue_state),
            )

        return admission

    def claim(self, channel: str, now: datetime) -> DriveClaim | None:
        lease_expires_at = now + self.lease_duration
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            _recover_expired(db, channel, now)
            row = _ROW.validate_python(
                db.execute(
                    """SELECT origin.origin_json,work.revision,work.state FROM agent_drive_work work
                    JOIN agent_drive_origins origin USING(tenant_id,run_id)
                    WHERE work.state IN ('pending','notify') AND work.due_at<=?
                    AND json_extract(origin.origin_json,'$.channel')=?
                    ORDER BY work.due_at LIMIT 1""",
                    (now.isoformat(), channel),
                ).fetchone()
            )
            if row is None:
                return None
            origin = DriveOrigin.model_validate_json(row[0])
            _ = db.execute(
                """UPDATE agent_drive_work SET state=?,claim_owner=?,lease_expires_at=?
                WHERE tenant_id=? AND run_id=? AND revision=? AND state=?""",
                (
                    "notifying" if row[2] == "notify" else "running",
                    self.owner_id,
                    lease_expires_at.isoformat(),
                    origin.tenant_id,
                    origin.run_id,
                    row[1],
                    row[2],
                ),
            )
        return DriveClaim(
            origin,
            row[1],
            "notify" if row[2] == "notify" else "drive",
            self.owner_id,
            lease_expires_at,
        )

    def renew(self, claim: DriveClaim, now: datetime) -> DriveClaim | None:
        lease_expires_at = now + self.lease_duration
        ownership = _ACTIVE_OWNERSHIP.get()
        active = (
            ownership is not None
            and ownership.origin.tenant_id == claim.origin.tenant_id
            and ownership.origin.run_id == claim.origin.run_id
            and ownership.owner_id == claim.owner_id
        )
        with self.connect() as db:
            cursor = (
                db.execute(
                    """UPDATE agent_drive_work SET lease_expires_at=?
                    WHERE tenant_id=? AND run_id=? AND state IN ('running','notifying')
                    AND claim_owner=? AND lease_expires_at>?""",
                    (
                        lease_expires_at.isoformat(),
                        claim.origin.tenant_id,
                        claim.origin.run_id,
                        claim.owner_id,
                        now.isoformat(),
                    ),
                )
                if active
                else db.execute(
                    """UPDATE agent_drive_work SET lease_expires_at=?
                    WHERE tenant_id=? AND run_id=? AND revision=?
                    AND state IN ('running','notifying') AND claim_owner=?
                    AND lease_expires_at>?""",
                    (
                        lease_expires_at.isoformat(),
                        claim.origin.tenant_id,
                        claim.origin.run_id,
                        claim.revision,
                        claim.owner_id,
                        now.isoformat(),
                    ),
                )
            )
            if cursor.rowcount != 1:
                return None
            row = _LEASE.validate_python(
                db.execute(
                    """SELECT revision,state,claim_owner,lease_expires_at FROM agent_drive_work
                    WHERE tenant_id=? AND run_id=?""",
                    (claim.origin.tenant_id, claim.origin.run_id),
                ).fetchone()
            )
        if row is None:
            return None
        if active and ownership is not None:
            _ = _ACTIVE_OWNERSHIP.set(replace(ownership, lease_expires_at=lease_expires_at))
        return DriveClaim(claim.origin, row[0], claim.phase, claim.owner_id, lease_expires_at)

    def discard(self, claim: DriveClaim, *, now: datetime | None = None) -> bool:
        released_at = now or datetime.now(UTC)
        with self.connect() as db:
            cursor = db.execute(
                """DELETE FROM agent_drive_work WHERE tenant_id=? AND run_id=?
                AND revision=? AND state IN ('running','notifying') AND claim_owner=?
                AND lease_expires_at>?""",
                (
                    claim.origin.tenant_id,
                    claim.origin.run_id,
                    claim.revision,
                    claim.owner_id,
                    released_at.isoformat(),
                ),
            )
        return cursor.rowcount == 1

    def notification_persisted(self, tenant_id: str, event_id: str) -> None:
        with self.connect() as db:
            _ = db.execute(
                """DELETE FROM agent_drive_work WHERE state='notify'
                AND (tenant_id,run_id) IN (SELECT tenant_id,run_id FROM agent_drive_origins
                WHERE tenant_id=? AND json_extract(origin_json,'$.event_id')=?)""",
                (tenant_id, event_id),
            )

    def block(self, claim: DriveClaim, reason: str, now: datetime) -> None:
        if claim.phase == "notify":
            _ = self.discard(claim, now=now)
            return
        repository = SqliteAgentRunRepository(self.database_path)
        run = repository.get(claim.origin.tenant_id, claim.origin.run_id)
        if run is None:
            _ = self.discard(claim, now=now)
            return
        task = project_task(run, repository.records(run.tenant_id, run.run_id))
        checkpoint = task.checkpoint.model_copy(
            update={"disposition": "blocked", "wait_reason": reason, "next_action": "done"}
        )
        blocked = TaskProjection(task.spec, checkpoint)
        step = AgentStep(
            schema_version="trace.agent-step.v1",
            step_id=f"{run.run_id}:step:{run.revision}",
            run_id=run.run_id,
            sequence=run.revision,
            kind=AgentStepKind.OBSERVE,
            state="completed",
            input_sha256=contract_sha256(task.checkpoint),
            output_sha256=contract_sha256(checkpoint),
            parent_step_sha256=run.head_step_sha256,
            occurred_at=max(now, run.updated_at),
        )
        transition = self.transition(run, blocked, now)

        def admission(connection: sqlite3.Connection) -> None:
            _require_owned_claim(connection, claim, now)
            transition(connection)

        _ = repository.append_step(
            run,
            step,
            state=AgentRunState.BLOCKED,
            expected_revision=run.revision,
            blocked_reason=reason,
            records=task_records(run, blocked, step.occurred_at),
            admission=admission,
        )

    def recover(self, channel: str, *, now: datetime | None = None) -> None:
        recovered_at = now or datetime.now(UTC)
        with self.connect() as db:
            _recover_expired(db, channel, recovered_at)


def _target_queue_state(
    connection: sqlite3.Connection, run: AgentRun, task: TaskProjection
) -> Literal["pending", "notify"]:
    state = _TEXT.validate_python(
        connection.execute(
            "SELECT state FROM agent_runs WHERE tenant_id=? AND run_id=?",
            (run.tenant_id, run.run_id),
        ).fetchone()
    )
    runnable = (
        state == ("running",)
        and task.checkpoint.disposition == "active"
        and task.checkpoint.next_action not in {None, "wait", "done"}
    )
    return "pending" if runnable else "notify"


def _transition_ownership(
    claim: _LeaseRow | None,
    ownership: _DriveOwnership | None,
    run: AgentRun,
    now: datetime,
) -> Literal["active", "already", "none"]:
    if ownership is None:
        if claim is not None and claim[1] in {"running", "notifying"}:
            raise DriveClaimLostError(_CLAIM_LOST)
        return "none"
    if ownership.origin.tenant_id != run.tenant_id or ownership.origin.run_id != run.run_id:
        raise DriveClaimLostError(_CLAIM_LOST)
    if claim is None:
        if ownership.phase != "drive":
            raise DriveClaimLostError(_CLAIM_LOST)
        return "active"
    live = (
        claim[1] == "running"
        and claim[2] == ownership.owner_id
        and claim[3] is not None
        and claim[3] > now.isoformat()
    )
    if live and claim[0] == run.revision + 1:
        return "already"
    if live and claim[0] == run.revision:
        return "active"
    raise DriveClaimLostError(_CLAIM_LOST)


def _recover_expired(connection: sqlite3.Connection, channel: str, now: datetime) -> None:
    _ = connection.execute(
        """UPDATE agent_drive_work SET state=CASE state WHEN 'notifying' THEN 'notify'
        ELSE 'pending' END,claim_owner=NULL,lease_expires_at=NULL
        WHERE state IN ('running','notifying')
        AND (lease_expires_at IS NULL OR lease_expires_at<=?)
        AND (tenant_id,run_id) IN (SELECT tenant_id,run_id FROM agent_drive_origins
        WHERE json_extract(origin_json,'$.channel')=?)""",
        (now.isoformat(), channel),
    )


def _recover_run_expired(
    connection: sqlite3.Connection, tenant_id: str, run_id: str, now: datetime
) -> None:
    _ = connection.execute(
        """UPDATE agent_drive_work SET state=CASE state WHEN 'notifying' THEN 'notify'
        ELSE 'pending' END,claim_owner=NULL,lease_expires_at=NULL
        WHERE tenant_id=? AND run_id=? AND state IN ('running','notifying')
        AND (lease_expires_at IS NULL OR lease_expires_at<=?)""",
        (tenant_id, run_id, now.isoformat()),
    )


def _bind_origin(connection: sqlite3.Connection, origin: DriveOrigin) -> None:
    _ = connection.execute(
        """INSERT INTO agent_drive_origins VALUES(?,?,?) ON CONFLICT(tenant_id,run_id)
        DO UPDATE SET origin_json=excluded.origin_json""",
        (origin.tenant_id, origin.run_id, origin.model_dump_json()),
    )


def _require_owned_claim(connection: sqlite3.Connection, claim: DriveClaim, now: datetime) -> None:
    row = _EXISTS.validate_python(
        connection.execute(
            """SELECT 1 FROM agent_drive_work WHERE tenant_id=? AND run_id=? AND revision=?
            AND state IN ('running','notifying') AND claim_owner=? AND lease_expires_at>?""",
            (
                claim.origin.tenant_id,
                claim.origin.run_id,
                claim.revision,
                claim.owner_id,
                now.isoformat(),
            ),
        ).fetchone()
    )
    if row is None:
        raise DriveClaimLostError(_CLAIM_LOST)


def _fence_pending_input(connection: sqlite3.Connection, origin: DriveOrigin) -> None:
    pending = None
    if origin.channel == "slack":
        pending = _EXISTS.validate_python(
            connection.execute(
                """SELECT 1 FROM slack_message_jobs WHERE conversation_id=? AND state='pending'
                AND COALESCE(json_extract(message_json,'$.notification_only'),0)=0 LIMIT 1""",
                (origin.conversation_id,),
            ).fetchone()
        )
    elif origin.channel == "http":
        pending = _EXISTS.validate_python(
            connection.execute(
                """SELECT 1 FROM agent_web_jobs WHERE tenant=? AND state='pending'
                AND json_extract(request_json,'$.run_id')=?
                AND json_extract(request_json,'$.action') IN ('input','approval') LIMIT 1""",
                (origin.tenant_id, origin.run_id),
            ).fetchone()
        )
    elif origin.channel == "slack_command":
        pending = _EXISTS.validate_python(
            connection.execute(
                """SELECT 1 FROM slack_command_jobs WHERE state='pending'
                AND json_extract(command_json,'$.run_id')=? LIMIT 1""",
                (origin.run_id,),
            ).fetchone()
        )
    if pending is not None:
        message = "task_completion_pending_admission"
        raise DriveAdmissionConflict(message)
