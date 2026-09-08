"""Immutable, scope-first human outcome observations and explicitly reviewed learning candidates."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ads_booster.contracts.agent_memory import MemoryNote
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.performance_observation import (
    PerformanceComparison,
    PerformanceObservation,
)
from ads_booster.marketing.agent_service.memory import SQLiteMemoryStore
from ads_booster.marketing.agent_service.performance_observation_validity import (
    learning_source_is_current,
)

if TYPE_CHECKING:
    import sqlite3
    from datetime import datetime
    from pathlib import Path

    from ads_booster.contracts.agent_memory import MemoryAccess


_MAX_LEARNING_SOURCES = 20
_MAX_REVIEW_TEXT = 600
_MAX_WORK_RECORDS = 1000
_MAX_LIST = 100


class PerformanceObservationStore:
    def __init__(self, database: Path) -> None:
        self.memory: SQLiteMemoryStore = SQLiteMemoryStore(database)
        with self.memory.connect() as db:
            _ = db.execute("""CREATE TABLE IF NOT EXISTS performance_observations (
                scope TEXT NOT NULL, observation_id TEXT NOT NULL, supersedes TEXT,
                data TEXT NOT NULL, PRIMARY KEY(scope,observation_id),
                UNIQUE(scope,supersedes))""")

    @staticmethod
    def _scope(access: MemoryAccess) -> str:
        if not access.scope.work_id:
            raise ValueError("performance_observation_requires_work")
        return access.scope.model_dump_json()

    @staticmethod
    def _get(
        db: sqlite3.Connection, scope: str, observation_id: str
    ) -> PerformanceObservation | None:
        row = cast(
            "tuple[str] | None",
            db.execute(
                "SELECT data FROM performance_observations WHERE scope=? AND observation_id=?",
                (scope, observation_id),
            ).fetchone(),
        )
        return PerformanceObservation.model_validate_json(row[0]) if row else None

    def get(self, observation_id: str, access: MemoryAccess) -> PerformanceObservation | None:
        scope = self._scope(access)
        with self.memory.connect() as db:
            return self._get(db, scope, observation_id)

    def record(
        self, observation: PerformanceObservation, access: MemoryAccess
    ) -> PerformanceObservation:
        scope = self._scope(access)
        if observation.scope != access.scope or observation.author_id != access.actor_id:
            raise ValueError("performance_observation_scope_or_author_denied")
        with self.memory.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            existing = self._get(db, scope, observation.observation_id)
            if existing is not None:
                if existing != observation:
                    raise ValueError("performance_observation_idempotency_conflict")
                return existing
            count = cast(
                "tuple[int]",
                db.execute(
                    "SELECT COUNT(*) FROM performance_observations WHERE scope=?", (scope,)
                ).fetchone(),
            )[0]
            if count >= _MAX_WORK_RECORDS:
                raise ValueError("performance_observation_limit_reached")
            if observation.supersedes:
                previous = self._get(db, scope, observation.supersedes)
                if previous is None:
                    raise ValueError("performance_observation_correction_scope_denied")
                if previous.author_id != access.actor_id and not access.can_review:
                    raise ValueError("performance_observation_correction_author_denied")
                if observation.recorded_at < previous.recorded_at:
                    raise ValueError("performance_observation_correction_time_invalid")
                successor = cast(
                    "tuple[int] | None",
                    db.execute(
                        "SELECT 1 FROM performance_observations WHERE scope=? AND supersedes=?",
                        (scope, observation.supersedes),
                    ).fetchone(),
                )
                if successor is not None:
                    raise ValueError("performance_observation_already_corrected")
            _ = db.execute(
                "INSERT INTO performance_observations VALUES(?,?,?,?)",
                (
                    scope,
                    observation.observation_id,
                    observation.supersedes,
                    observation.model_dump_json(),
                ),
            )
        return observation

    def list(
        self, access: MemoryAccess, *, current_only: bool = True, limit: int = 100
    ) -> tuple[PerformanceObservation, ...]:
        scope = self._scope(access)
        if not 1 <= limit <= _MAX_LIST:
            raise ValueError("performance_observation_list_limit")
        with self.memory.connect() as db:
            rows = cast(
                "list[tuple[str]]",
                db.execute(
                    """SELECT original.data FROM performance_observations original
                    WHERE original.scope=? AND (?=0 OR NOT EXISTS (
                        SELECT 1 FROM performance_observations correction
                        WHERE correction.scope=original.scope
                        AND correction.supersedes=original.observation_id))
                    ORDER BY rtrim(json_extract(original.data, '$.recorded_at'), 'Z') DESC,
                    original.observation_id LIMIT ?""",
                    (scope, int(current_only), limit),
                ).fetchall(),
            )
        return tuple(PerformanceObservation.model_validate_json(row[0]) for row in rows)

    def _current_sources(
        self, observation_ids: tuple[str, ...], access: MemoryAccess
    ) -> tuple[PerformanceObservation, ...]:
        scope = self._scope(access)
        if not 1 <= len(observation_ids) <= _MAX_LEARNING_SOURCES or len(
            set(observation_ids)
        ) != len(observation_ids):
            raise ValueError("performance_learning_observation_limit")
        sources: list[PerformanceObservation] = []
        with self.memory.connect() as db:
            _ = db.execute("BEGIN")
            for identifier in sorted(observation_ids):
                item = self._get(db, scope, identifier)
                successor = cast(
                    "tuple[int] | None",
                    db.execute(
                        "SELECT 1 FROM performance_observations WHERE scope=? AND supersedes=?",
                        (scope, identifier),
                    ).fetchone(),
                )
                if item is None or successor is not None:
                    raise ValueError("performance_learning_current_observations_required")
                sources.append(item)
        return tuple(sources)

    def compare(
        self, observation_ids: tuple[str, ...], access: MemoryAccess
    ) -> PerformanceComparison:
        sources = self._current_sources(observation_ids, access)
        fields = ("channel", "account_id", "country", "window_start", "window_end")
        mismatches = tuple(
            field + "_mismatch"
            for field in fields
            if any(getattr(item, field) != getattr(sources[0], field) for item in sources[1:])
        )
        return PerformanceComparison(
            scope=access.scope,
            observations=sources,
            comparable=len(sources) > 1 and not mismatches,
            mismatch_reasons=mismatches
            if len(sources) > 1
            else ("additional_observation_required",),
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
            raise ValueError("performance_learning_observation_limit")
        if any(
            not text.strip() or len(text) > _MAX_REVIEW_TEXT
            for text in (observation, counterexample, applicability)
        ):
            raise ValueError("performance_learning_review_fields_required")
        sources = self._current_sources(observation_ids, access)
        if any(item.recorded_at > now for item in sources):
            raise ValueError("performance_learning_future_observation")
        note = MemoryNote(
            note_id=note_id,
            scope=access.scope,
            author_id=access.actor_id,
            category="hypothesis",
            domain="learning",
            created_at=now,
            expires_at=expires_at,
            source_ref="performance-observations:"
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
                raise ValueError("performance_learning_idempotency_conflict")
            return existing
        self.memory.put(note, access, now=now)
        return note

    def learning_is_current(self, note: MemoryNote, access: MemoryAccess) -> bool:
        """Check on the selection connection when producing authoritative retrieval receipts."""
        with self.memory.connect() as db:
            return learning_source_is_current(db, note, access)
