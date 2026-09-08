"""Immutable, scope-first human effort observations and explicitly reviewed learning candidates."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ads_booster.contracts.agent_memory import MemoryNote
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.work_observation import WorkObservation, WorkSummary
from ads_booster.learning.memory import SQLiteMemoryStore
from ads_booster.learning.work_observation_validity import learning_source_is_current

if TYPE_CHECKING:
    import sqlite3
    from datetime import datetime
    from pathlib import Path

    from ads_booster.contracts.agent_memory import MemoryAccess


_MAX_LEARNING_SOURCES = 20
_MAX_REVIEW_TEXT = 600
_MAX_WORK_RECORDS = 1000


class WorkObservationStore:
    def __init__(self, database: Path) -> None:
        self.memory: SQLiteMemoryStore = SQLiteMemoryStore(database)
        with self.memory.connect() as db:
            _ = db.execute("""CREATE TABLE IF NOT EXISTS work_observations (
                scope TEXT NOT NULL, observation_id TEXT NOT NULL, supersedes TEXT,
                data TEXT NOT NULL, PRIMARY KEY(scope,observation_id),
                UNIQUE(scope,supersedes))""")

    @staticmethod
    def _scope(access: MemoryAccess) -> str:
        if not access.scope.work_id:
            raise ValueError("work_observation_requires_work")
        return access.scope.model_dump_json()

    @staticmethod
    def _get(db: sqlite3.Connection, scope: str, observation_id: str) -> WorkObservation | None:
        row = cast(
            "tuple[str] | None",
            db.execute(
                "SELECT data FROM work_observations WHERE scope=? AND observation_id=?",
                (scope, observation_id),
            ).fetchone(),
        )
        return WorkObservation.model_validate_json(row[0]) if row else None

    def get(self, observation_id: str, access: MemoryAccess) -> WorkObservation | None:
        scope = self._scope(access)
        with self.memory.connect() as db:
            return self._get(db, scope, observation_id)

    def record(self, observation: WorkObservation, access: MemoryAccess) -> WorkObservation:
        scope = self._scope(access)
        if observation.scope != access.scope or observation.author_id != access.actor_id:
            raise ValueError("work_observation_scope_or_author_denied")
        with self.memory.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            existing = self._get(db, scope, observation.observation_id)
            if existing is not None:
                if existing != observation:
                    raise ValueError("work_observation_idempotency_conflict")
                return existing
            count = cast(
                "tuple[int]",
                db.execute(
                    "SELECT COUNT(*) FROM work_observations WHERE scope=?", (scope,)
                ).fetchone(),
            )[0]
            if count >= _MAX_WORK_RECORDS:
                raise ValueError("work_observation_limit_reached")
            if observation.supersedes:
                previous = self._get(db, scope, observation.supersedes)
                if previous is None:
                    raise ValueError("work_observation_correction_scope_denied")
                if previous.author_id != access.actor_id and not access.can_review:
                    raise ValueError("work_observation_correction_author_denied")
                if observation.recorded_at < previous.recorded_at:
                    raise ValueError("work_observation_correction_time_invalid")
                successor = cast(
                    "tuple[int] | None",
                    db.execute(
                        "SELECT 1 FROM work_observations WHERE scope=? AND supersedes=?",
                        (scope, observation.supersedes),
                    ).fetchone(),
                )
                if successor is not None:
                    raise ValueError("work_observation_already_corrected")
            _ = db.execute(
                "INSERT INTO work_observations VALUES(?,?,?,?)",
                (
                    scope,
                    observation.observation_id,
                    observation.supersedes,
                    observation.model_dump_json(),
                ),
            )
        return observation

    def summarize(self, access: MemoryAccess) -> WorkSummary:
        scope = self._scope(access)
        with self.memory.connect() as db:
            rows = cast(
                "list[tuple[str]]",
                db.execute(
                    """SELECT original.data FROM work_observations original
                    WHERE original.scope=? AND NOT EXISTS (
                        SELECT 1 FROM work_observations correction
                        WHERE correction.scope=original.scope
                        AND correction.supersedes=original.observation_id)
                    ORDER BY original.observation_id""",
                    (scope,),
                ).fetchall(),
            )
        observations = tuple(WorkObservation.model_validate_json(row[0]) for row in rows)
        return WorkSummary(
            scope=access.scope,
            observations=observations,
            elapsed_minutes=sum(item.elapsed_minutes for item in observations),
            revision_count=sum(item.revision_count for item in observations),
        )

    def learning_candidate(  # noqa: PLR0913 - explicit attributed review packet.
        self,
        access: MemoryAccess,
        *,
        note_id: str,
        observation_ids: tuple[str, ...],
        observation: str,
        counterexample: str,
        applicability: str,
        now: datetime,
        expires_at: datetime,
    ) -> MemoryNote:
        if not 1 <= len(observation_ids) <= _MAX_LEARNING_SOURCES or len(
            set(observation_ids)
        ) != len(observation_ids):
            raise ValueError("work_learning_observation_limit")
        if any(
            not text.strip() or len(text) > _MAX_REVIEW_TEXT
            for text in (observation, counterexample, applicability)
        ):
            raise ValueError("work_learning_review_fields_required")
        current = {item.observation_id: item for item in self.summarize(access).observations}
        if any(item not in current for item in observation_ids):
            raise ValueError("work_learning_current_observations_required")
        sources = tuple(current[item] for item in sorted(observation_ids))
        if any(item.recorded_at > now for item in sources):
            raise ValueError("work_learning_future_observation")
        note = MemoryNote(
            note_id=note_id,
            scope=access.scope,
            author_id=access.actor_id,
            category="hypothesis",
            domain="learning",
            created_at=now,
            expires_at=expires_at,
            source_ref="work-observations:"
            + contract_sha256({"observations": [item.model_dump_json() for item in sources]}),
            source_sha256=contract_sha256(
                {"observations": [item.model_dump_json() for item in sources]}
            ),
            text=json.dumps(
                {
                    "observation": observation,
                    "counterexample": counterexample,
                    "applicability": applicability,
                    "observation_ids": [item.observation_id for item in sources],
                    "interpretation": "Reported snapshot; no causal inference or approval.",
                },
                ensure_ascii=False,
            ),
        )
        existing = self.memory.get(note_id, access)
        if existing is not None:
            expected = note.model_copy(
                update={
                    "created_at": existing.created_at,
                    "expires_at": existing.expires_at,
                    "stage": existing.stage,
                }
            )
            if existing != expected:
                raise ValueError("work_learning_idempotency_conflict")
            return existing
        self.memory.put(note, access, now=now)
        return note

    def learning_is_current(self, note: MemoryNote, access: MemoryAccess) -> bool:
        """Check on the selection connection when producing authoritative retrieval receipts."""
        with self.memory.connect() as db:
            return learning_source_is_current(db, note, access)
