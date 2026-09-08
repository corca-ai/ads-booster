"""Currentness predicate shared by memory selection/review without importing memory storage."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.models import Identifier
from ads_booster.contracts.work_observation import WorkObservation
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from ads_booster.contracts.agent_memory import MemoryAccess, MemoryNote

_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_IDS: TypeAdapter[tuple[Identifier, ...]] = TypeAdapter(tuple[Identifier, ...])
_MAX_SOURCES = 20


def learning_source_is_current(  # noqa: PLR0911 - fail-closed lineage checks.
    db: sqlite3.Connection, note: MemoryNote, access: MemoryAccess
) -> bool:
    if not note.source_ref.startswith("work-observations:"):
        return True
    if note.scope != access.scope or not access.scope.work_id:
        return False
    try:
        payload = _JSON.validate_json(note.text)
        ids = _IDS.validate_python(payload.get("observation_ids"))
        if not 1 <= len(ids) <= _MAX_SOURCES or len(set(ids)) != len(ids):
            return False
        scope = access.scope.model_dump_json()
        sources: list[WorkObservation] = []
        for observation_id in sorted(ids):
            row = cast(
                "tuple[str] | None",
                db.execute(
                    """SELECT data FROM work_observations original
                WHERE original.scope=? AND original.observation_id=? AND NOT EXISTS (
                    SELECT 1 FROM work_observations correction
                    WHERE correction.scope=original.scope
                    AND correction.supersedes=original.observation_id)""",
                    (scope, observation_id),
                ).fetchone(),
            )
            if row is None:
                return False
            item = WorkObservation.model_validate_json(row[0])
            if item.scope != access.scope or item.observation_id != observation_id:
                return False
            sources.append(item)
        digest = contract_sha256({"observations": [item.model_dump_json() for item in sources]})
        return note.source_sha256 == digest and note.source_ref == "work-observations:" + digest
    except ValueError, TypeError, sqlite3.OperationalError:
        return False
