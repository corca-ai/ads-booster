from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter, ValidationError

from ads_booster.workspace.errors import WorkspaceStoreCorruptionError
from ads_booster.workspace.models import (
    CandidateId,
    CandidateImageInputs,
    CandidatePostingSlot,
    CandidateRecord,
    CandidateSource,
    CandidateStatus,
    WorkspaceId,
)

if TYPE_CHECKING:
    from ads_booster.workspace.database import SqliteCursor

type CandidateRow = tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str,
    str,
    str | None,
    int,
    float,
    float,
]

CANDIDATE_RECORD: Final = "candidate"
SELECT_CANDIDATE: Final = """
SELECT workspace_id, candidate_id, source, country, topic, caption, hypothesis, refs_used_json,
       principles_applied_json, shooting_order, image_inputs_json, ai_verdict, image_path,
       image_sha256, agent_run_id, posting_slot, status, review_note, revision, created_at,
       updated_at
FROM candidates
"""
INSERT_CANDIDATE: Final = """
INSERT INTO candidates (
    workspace_id, candidate_id, source, country, topic, caption, hypothesis, refs_used_json,
    principles_applied_json, shooting_order, image_inputs_json, ai_verdict, image_path,
    image_sha256, agent_run_id, posting_slot, status, review_note, revision, created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, NULL, 1, ?, ?)
"""
NEWEST_FIRST: Final = " ORDER BY created_at DESC, candidate_id DESC"
SELECT_STATUS: Final = "SELECT status FROM candidates WHERE workspace_id = ? AND candidate_id = ?"

_ROW_ADAPTER: TypeAdapter[CandidateRow] = TypeAdapter(CandidateRow)
_REFS_ADAPTER = TypeAdapter(tuple[str, ...])
_PRINCIPLES_ADAPTER = TypeAdapter(tuple[int, ...])
_IMAGE_INPUTS_ADAPTER = TypeAdapter(CandidateImageInputs)


def dump_references(value: tuple[str, ...]) -> str:
    return _REFS_ADAPTER.dump_json(value).decode()


def dump_principles(value: tuple[int, ...]) -> str:
    return _PRINCIPLES_ADAPTER.dump_json(value).decode()


def dump_image_inputs(value: CandidateImageInputs | None) -> str | None:
    return None if value is None else _IMAGE_INPUTS_ADAPTER.dump_json(value).decode()


def fetch_candidate(cursor: SqliteCursor) -> CandidateRow | None:
    row = cursor.fetchone()
    if row is None:
        return None
    try:
        return _ROW_ADAPTER.validate_python(row)
    except ValidationError as error:
        raise WorkspaceStoreCorruptionError(record_type=CANDIDATE_RECORD) from error


def candidate_from_row(row: CandidateRow) -> CandidateRecord:
    return CandidateRecord(
        workspace_id=WorkspaceId(row[0]),
        candidate_id=CandidateId(row[1]),
        source=CandidateSource(row[2]),
        country=row[3],
        topic=row[4],
        caption=row[5],
        hypothesis=row[6],
        refs_used=_REFS_ADAPTER.validate_json(row[7]),
        principles_applied=_PRINCIPLES_ADAPTER.validate_json(row[8]),
        shooting_order=row[9],
        image_inputs=(None if row[10] is None else _IMAGE_INPUTS_ADAPTER.validate_json(row[10])),
        ai_verdict=row[11],
        image_path=row[12],
        image_sha256=row[13],
        agent_run_id=row[14],
        posting_slot=CandidatePostingSlot(row[15]),
        status=CandidateStatus(row[16]),
        review_note=row[17],
        revision=row[18],
        created_at=row[19],
        updated_at=row[20],
    )
