from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum, unique
from hashlib import sha256
from typing import TYPE_CHECKING, ClassVar, Final, Protocol, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from trace_capture.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

_REGISTRY_FILENAME: Final = "marketing-control.sqlite3"
_SAMPLE_MINUTES: Final = (5, 10, 15, 20, 25, 30)
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


@unique
class RunState(StrEnum):
    SCHEDULED = "scheduled"
    CONTEXT_SNAPSHOT = "context_snapshot"
    RESEARCH = "research"
    PLANNING = "planning"
    CANDIDATE_GENERATION = "candidate_generation"
    CAPTURE_REQUESTED = "capture_requested"
    CAPTURE_COMPLETED = "capture_completed"
    AUTOMATIC_QUALITY_CHECK = "automatic_quality_check"
    AWAITING_HUMAN_APPROVAL = "awaiting_human_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SCHEDULED_FOR_PUBLISH = "scheduled_for_publish"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    OBSERVING = "observing"
    EVALUATED = "evaluated"
    MEMORY_COMMITTED = "memory_committed"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN_SIDE_EFFECT = "unknown_side_effect"


_ALLOWED_TRANSITIONS: Final[dict[RunState, frozenset[RunState]]] = {
    RunState.SCHEDULED: frozenset({RunState.CONTEXT_SNAPSHOT, RunState.FAILED}),
    RunState.CONTEXT_SNAPSHOT: frozenset({RunState.RESEARCH, RunState.FAILED}),
    RunState.RESEARCH: frozenset({RunState.PLANNING, RunState.FAILED}),
    RunState.PLANNING: frozenset({RunState.CANDIDATE_GENERATION, RunState.FAILED}),
    RunState.CANDIDATE_GENERATION: frozenset({RunState.CAPTURE_REQUESTED, RunState.FAILED}),
    RunState.CAPTURE_REQUESTED: frozenset({RunState.CAPTURE_COMPLETED, RunState.FAILED}),
    RunState.CAPTURE_COMPLETED: frozenset({RunState.AUTOMATIC_QUALITY_CHECK, RunState.FAILED}),
    RunState.AUTOMATIC_QUALITY_CHECK: frozenset(
        {RunState.AWAITING_HUMAN_APPROVAL, RunState.FAILED}
    ),
    RunState.AWAITING_HUMAN_APPROVAL: frozenset({RunState.APPROVED, RunState.REJECTED}),
    RunState.APPROVED: frozenset({RunState.SCHEDULED_FOR_PUBLISH}),
    RunState.REJECTED: frozenset(),
    RunState.SCHEDULED_FOR_PUBLISH: frozenset({RunState.PUBLISHING}),
    RunState.PUBLISHING: frozenset(
        {RunState.PUBLISHED, RunState.FAILED, RunState.UNKNOWN_SIDE_EFFECT}
    ),
    RunState.PUBLISHED: frozenset({RunState.OBSERVING}),
    RunState.OBSERVING: frozenset({RunState.EVALUATED, RunState.FAILED}),
    RunState.EVALUATED: frozenset({RunState.MEMORY_COMMITTED}),
    RunState.MEMORY_COMMITTED: frozenset({RunState.COMPLETED}),
    RunState.COMPLETED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.UNKNOWN_SIDE_EFFECT: frozenset(),
}


class SimulationModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class MarketingAccount(SimulationModel):
    account_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    channel: str = "threads"
    country: str = Field(min_length=2, max_length=8)
    timezone: str = "Asia/Seoul"
    schedule_minutes: int = Field(default=1440, ge=1, le=43_200)
    instruction_revision: int = Field(default=1, ge=1)
    credential_ref: str | None = None
    enabled: bool = True
    adapter_mode: str = Field(default="simulation", pattern=r"^(simulation|live)$")


class MarketingRun(SimulationModel):
    run_id: str
    account_id: str
    state: RunState
    context_digest: str | None = None
    publication_id: str | None = None
    output: JsonObject = Field(default_factory=dict)


class ChannelAdapter(Protocol):
    def publish(self, account: MarketingAccount, run_id: str, candidate: JsonObject) -> str: ...

    def sample_metrics(
        self,
        account: MarketingAccount,
        publication_id: str,
        minute: int,
    ) -> JsonObject: ...


class SimulationChannelAdapter:
    def publish(self, account: MarketingAccount, run_id: str, candidate: JsonObject) -> str:
        del candidate
        return f"sim://{account.channel}/{account.account_id}/{run_id}"

    def sample_metrics(
        self,
        account: MarketingAccount,
        publication_id: str,
        minute: int,
    ) -> JsonObject:
        seed = int(sha256(f"{account.account_id}:{publication_id}".encode()).hexdigest()[:8], 16)
        return {
            "minute": minute,
            "views": (seed % 100) + minute * 7,
            "likes": (seed % 11) + minute // 5,
        }


class LiveAdapterUnavailableError(RuntimeError):
    pass


class LocalMarketingControlPlane:
    """A deterministic end-to-end proof of the cloud contract.

    The registry is shared, while each account's learned memory lives in its own
    SQLite file. This mirrors D1 plus one named Durable Object per account without
    pretending that local simulation proves a production deployment.
    """

    _home: Path
    _accounts_home: Path
    _database_path: Path
    _adapter: ChannelAdapter

    def __init__(self, home: Path, adapter: ChannelAdapter | None = None) -> None:
        self._home = home
        self._home.mkdir(parents=True, exist_ok=True)
        self._accounts_home = home / "accounts"
        self._accounts_home.mkdir(parents=True, exist_ok=True)
        self._database_path = home / _REGISTRY_FILENAME
        self._adapter = adapter or SimulationChannelAdapter()
        with self._connect(write=True) as connection:
            _ = connection.executescript(_registry_schema())
            _ = connection.execute(
                """
                INSERT OR IGNORE INTO shared_instructions (revision, body, created_at)
                VALUES (1, ?, ?)
                """,
                (_default_instruction(), datetime.now(UTC).timestamp()),
            )
        self._database_path.chmod(0o600)

    def register_account(self, account: MarketingAccount) -> MarketingAccount:
        now = datetime.now(UTC).timestamp()
        with self._connect(write=True) as connection:
            instruction = _fetchone(
                connection,
                "SELECT 1 FROM shared_instructions WHERE revision = ?",
                (account.instruction_revision,),
            )
            if instruction is None:
                raise ValueError(f"unknown instruction revision {account.instruction_revision}")
            _ = connection.execute(
                """
                INSERT INTO marketing_accounts
                    (account_id, config_json, enabled, next_run_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    config_json = excluded.config_json,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    account.account_id,
                    account.model_dump_json(),
                    int(account.enabled),
                    now,
                    now,
                    now,
                ),
            )
        self._initialize_account_memory(account.account_id)
        return account

    def start_run(self, account_id: str, *, auto_approve: bool = False) -> MarketingRun:
        account = self.get_account(account_id)
        if not account.enabled:
            raise ValueError(f"account {account_id!r} is disabled")
        if account.adapter_mode == "live":
            raise LiveAdapterUnavailableError(
                "live adapter requires a successful capability probe and explicit configuration"
            )
        run_id = uuid4().hex
        now = datetime.now(UTC).timestamp()
        run = MarketingRun(run_id=run_id, account_id=account_id, state=RunState.SCHEDULED)
        with self._connect(write=True) as connection:
            _ = connection.execute(
                """
                INSERT INTO marketing_runs
                    (run_id, account_id, state, output_json, created_at, updated_at)
                VALUES (?, ?, ?, '{}', ?, ?)
                """,
                (run_id, account_id, run.state, now, now),
            )
            self._append_event(connection, run_id, run.state, {})
        run = self._advance(run, RunState.CONTEXT_SNAPSHOT, self._context_snapshot(account))
        run = self._advance(run, RunState.RESEARCH, self._research(account, run))
        run = self._advance(run, RunState.PLANNING, {"goal": "one useful organic post"})
        candidate = self._candidate(account, run)
        run = self._advance(run, RunState.CANDIDATE_GENERATION, {"candidate": candidate})
        run = self._advance(run, RunState.CAPTURE_REQUESTED, {"capture": "requested"})
        run = self._advance(
            run,
            RunState.CAPTURE_COMPLETED,
            {"artifact": f"sim://artifact/{account_id}/{run_id}.png"},
        )
        run = self._advance(run, RunState.AUTOMATIC_QUALITY_CHECK, {"quality": "pass"})
        run = self._advance(run, RunState.AWAITING_HUMAN_APPROVAL, {})
        return self.approve(run_id) if auto_approve else run

    def approve(self, run_id: str) -> MarketingRun:
        run = self.get_run(run_id)
        if run.state is not RunState.AWAITING_HUMAN_APPROVAL:
            raise ValueError(f"run {run_id!r} is not awaiting approval")
        account = self.get_account(run.account_id)
        candidate = run.output.get("candidate")
        if not isinstance(candidate, dict):
            raise ValueError(f"run {run_id!r} has no candidate")
        run = self._advance(run, RunState.APPROVED, {"approved_by": "simulation-operator"})
        run = self._advance(run, RunState.SCHEDULED_FOR_PUBLISH, {})
        run = self._advance(run, RunState.PUBLISHING, {})
        publication_id = self._adapter.publish(account, run.run_id, candidate)
        run = self._advance(run, RunState.PUBLISHED, {"publication_id": publication_id})
        run = self._advance(run, RunState.OBSERVING, {})
        samples = tuple(
            self._adapter.sample_metrics(account, publication_id, minute)
            for minute in _SAMPLE_MINUTES
        )
        final = samples[-1]
        views = _json_integer(final.get("views", 0), field="views")
        likes = _json_integer(final.get("likes", 0), field="likes")
        evaluation: JsonObject = {
            "samples": list(samples),
            "engagement_rate": 0.0 if views == 0 else likes / views,
        }
        run = self._advance(run, RunState.EVALUATED, {"evaluation": evaluation})
        self._append_memory(account.account_id, run.run_id, evaluation)
        run = self._advance(run, RunState.MEMORY_COMMITTED, {"memory": "committed"})
        return self._advance(run, RunState.COMPLETED, {})

    def reject(self, run_id: str, reason: str) -> MarketingRun:
        run = self.get_run(run_id)
        if run.state is not RunState.AWAITING_HUMAN_APPROVAL:
            raise ValueError(f"run {run_id!r} is not awaiting approval")
        return self._advance(run, RunState.REJECTED, {"rejection_reason": reason})

    def get_account(self, account_id: str) -> MarketingAccount:
        with self._connect() as connection:
            row = _fetchone(
                connection,
                "SELECT config_json FROM marketing_accounts WHERE account_id = ?",
                (account_id,),
            )
        if row is None:
            raise KeyError(account_id)
        return MarketingAccount.model_validate_json(str(row[0]))

    def get_run(self, run_id: str) -> MarketingRun:
        with self._connect() as connection:
            row = _fetchone(
                connection,
                """
                SELECT account_id, state, context_digest, publication_id, output_json
                FROM marketing_runs WHERE run_id = ?
                """,
                (run_id,),
            )
        if row is None:
            raise KeyError(run_id)
        output = _JSON_OBJECT.validate_json(str(row[4]))
        return MarketingRun(
            run_id=run_id,
            account_id=str(row[0]),
            state=RunState(str(row[1])),
            context_digest=None if row[2] is None else str(row[2]),
            publication_id=None if row[3] is None else str(row[3]),
            output=output,
        )

    def memories(self, account_id: str) -> tuple[JsonObject, ...]:
        with self._account_connect(account_id) as connection:
            rows = _fetchall(
                connection,
                "SELECT content_json FROM account_memory ORDER BY created_at",
            )
        return tuple(_JSON_OBJECT.validate_json(str(row[0])) for row in rows)

    def _advance(
        self,
        current: MarketingRun,
        state: RunState,
        output_patch: JsonObject,
    ) -> MarketingRun:
        if state not in _ALLOWED_TRANSITIONS[current.state]:
            raise ValueError(f"invalid run transition {current.state} -> {state}")
        output = {**current.output, **output_patch}
        context_digest = current.context_digest
        publication_id = current.publication_id
        if state is RunState.CONTEXT_SNAPSHOT:
            context_digest = str(output_patch["context_digest"])
        if state is RunState.PUBLISHED:
            publication_id = str(output_patch["publication_id"])
        now = datetime.now(UTC).timestamp()
        with self._connect(write=True) as connection:
            updated = connection.execute(
                """
                UPDATE marketing_runs
                SET state = ?, context_digest = ?, publication_id = ?,
                    output_json = ?, updated_at = ?
                WHERE run_id = ? AND state = ?
                """,
                (
                    state,
                    context_digest,
                    publication_id,
                    json.dumps(output, sort_keys=True, separators=(",", ":")),
                    now,
                    current.run_id,
                    current.state,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError(f"concurrent run transition for {current.run_id!r}")
            self._append_event(connection, current.run_id, state, output_patch)
        return MarketingRun(
            run_id=current.run_id,
            account_id=current.account_id,
            state=state,
            context_digest=context_digest,
            publication_id=publication_id,
            output=output,
        )

    def _context_snapshot(self, account: MarketingAccount) -> JsonObject:
        with self._connect() as connection:
            row = _fetchone(
                connection,
                "SELECT body FROM shared_instructions WHERE revision = ?",
                (account.instruction_revision,),
            )
        if row is None:
            raise ValueError("shared instruction disappeared")
        body = str(row[0])
        memories = self.memories(account.account_id)
        snapshot: JsonObject = {
            "account": account.model_dump(mode="json"),
            "shared_instruction": body,
            "private_memory": [dict(memory) for memory in memories],
        }
        canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        return {"context_digest": sha256(canonical.encode()).hexdigest(), "context": snapshot}

    @staticmethod
    def _research(account: MarketingAccount, run: MarketingRun) -> JsonObject:
        return {
            "research": {
                "country": account.country,
                "signals": ["customer language", "organic conversation"],
                "run_id": run.run_id,
            }
        }

    @staticmethod
    def _candidate(account: MarketingAccount, run: MarketingRun) -> JsonObject:
        return {
            "id": f"candidate-{run.run_id[:8]}",
            "country": account.country,
            "caption": (
                "One specific customer problem, one useful observation, one honest next step."
            ),
            "hypothesis": "Specific organic posts create higher-signal conversations.",
        }

    def _initialize_account_memory(self, account_id: str) -> None:
        with self._account_connect(account_id, write=True) as connection:
            _ = connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS account_memory (
                    memory_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE,
                    content_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )
        self._account_path(account_id).chmod(0o600)

    def _append_memory(self, account_id: str, run_id: str, value: JsonObject) -> None:
        with self._account_connect(account_id, write=True) as connection:
            _ = connection.execute(
                """
                INSERT OR IGNORE INTO account_memory (memory_id, run_id, content_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    run_id,
                    json.dumps(value, sort_keys=True, separators=(",", ":")),
                    datetime.now(UTC).timestamp(),
                ),
            )

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        run_id: str,
        state: RunState,
        detail: JsonObject,
    ) -> None:
        _ = connection.execute(
            """
            INSERT INTO marketing_run_events (event_id, run_id, state, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                uuid4().hex,
                run_id,
                state,
                json.dumps(detail, sort_keys=True, separators=(",", ":")),
                datetime.now(UTC).timestamp(),
            ),
        )

    def _account_path(self, account_id: str) -> Path:
        digest = sha256(account_id.encode()).hexdigest()
        return self._accounts_home / f"{digest}.sqlite3"

    @contextmanager
    def _connect(self, *, write: bool = False) -> Generator[sqlite3.Connection]:
        with _sqlite_connection(self._database_path, write=write) as connection:
            yield connection

    @contextmanager
    def _account_connect(
        self,
        account_id: str,
        *,
        write: bool = False,
    ) -> Generator[sqlite3.Connection]:
        with _sqlite_connection(self._account_path(account_id), write=write) as connection:
            yield connection


@contextmanager
def _sqlite_connection(path: Path, *, write: bool) -> Generator[sqlite3.Connection]:
    connection = sqlite3.connect(path, timeout=30.0)
    try:
        _ = connection.execute("PRAGMA busy_timeout = 30000")
        if write:
            _ = connection.execute("BEGIN IMMEDIATE")
        yield connection
        if write:
            connection.commit()
    except sqlite3.Error:
        if write:
            connection.rollback()
        raise
    finally:
        connection.close()


type SqliteValue = bytes | float | int | str | None
type SqliteRow = tuple[SqliteValue, ...]


def _fetchone(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[SqliteValue, ...] = (),
) -> SqliteRow | None:
    cursor = connection.execute(query, parameters)
    return cast("SqliteRow | None", cursor.fetchone())


def _fetchall(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[SqliteValue, ...] = (),
) -> list[SqliteRow]:
    cursor = connection.execute(query, parameters)
    return cast("list[SqliteRow]", cursor.fetchall())


def _json_integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValueError(f"{field} must be an integer")
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{field} must be an integer") from error


def _registry_schema() -> str:
    return """
    CREATE TABLE IF NOT EXISTS shared_instructions (
        revision INTEGER PRIMARY KEY,
        body TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS marketing_accounts (
        account_id TEXT PRIMARY KEY,
        config_json TEXT NOT NULL,
        enabled INTEGER NOT NULL,
        next_run_at REAL NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS marketing_runs (
        run_id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL REFERENCES marketing_accounts(account_id),
        state TEXT NOT NULL,
        context_digest TEXT,
        publication_id TEXT,
        output_json TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS marketing_runs_account
    ON marketing_runs (account_id, created_at);
    CREATE TABLE IF NOT EXISTS marketing_run_events (
        event_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES marketing_runs(run_id),
        state TEXT NOT NULL,
        detail_json TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS marketing_events_run
    ON marketing_run_events (run_id, created_at);
    """


def _default_instruction() -> str:
    return (
        "Optimize for a reliable learning loop: use customer language, make one concrete "
        "claim, preserve human judgment before publication, record provenance, and never "
        "retry an unverified external side effect."
    )
