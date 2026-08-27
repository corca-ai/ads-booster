from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter, ValidationError

from ads_booster.workspace.errors import WorkspaceStoreCorruptionError
from ads_booster.workspace.models import (
    CandidateBackgroundProvenance,
    CandidateGenerationProvenance,
    CandidateId,
    CandidateImageInputs,
    CandidatePersonaDomain,
    CandidatePostingSlot,
    CandidateRecord,
    CandidateSource,
    CandidateStatus,
    MarketingAccountId,
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
    str | None,
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
    str | None,
    str | None,
    str,
    str,
    str | None,
    int,
    float,
    float,
    str | None,
]

CANDIDATE_RECORD: Final = "candidate"
SELECT_CANDIDATE: Final = """
SELECT workspace_id, candidate_id, source, country, topic, persona_domain, caption, hypothesis,
       refs_used_json, principles_applied_json, shooting_order, image_inputs_json, ai_verdict,
       image_path, image_sha256, agent_run_id, generation_provenance_json,
       background_provenance_json, posting_slot, status, review_note, revision, created_at,
       updated_at, account_id
FROM candidates
"""
INSERT_CANDIDATE: Final = """
INSERT INTO candidates (
    workspace_id, candidate_id, source, country, topic, persona_domain, caption, hypothesis,
    refs_used_json, principles_applied_json, shooting_order, image_inputs_json, ai_verdict,
    image_path, image_sha256, agent_run_id, generation_provenance_json,
    background_provenance_json, posting_slot, status, review_note, revision, created_at,
    updated_at, account_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL, ?, ?, NULL, 1, ?, ?, ?)
"""
NEWEST_FIRST: Final = " ORDER BY created_at DESC, candidate_id DESC"
SELECT_STATUS: Final = "SELECT status FROM candidates WHERE workspace_id = ? AND candidate_id = ?"

_ROW_ADAPTER: TypeAdapter[CandidateRow] = TypeAdapter(CandidateRow)
_REFS_ADAPTER = TypeAdapter(tuple[str, ...])
_PRINCIPLES_ADAPTER = TypeAdapter(tuple[int, ...])
_IMAGE_INPUTS_ADAPTER = TypeAdapter(CandidateImageInputs)
_GENERATION_ADAPTER = TypeAdapter(CandidateGenerationProvenance)
_BACKGROUND_ADAPTER = TypeAdapter(CandidateBackgroundProvenance)


def dump_references(value: tuple[str, ...]) -> str:
    return _REFS_ADAPTER.dump_json(value).decode()


def dump_principles(value: tuple[int, ...]) -> str:
    return _PRINCIPLES_ADAPTER.dump_json(value).decode()


def dump_image_inputs(value: CandidateImageInputs | None) -> str | None:
    return None if value is None else _IMAGE_INPUTS_ADAPTER.dump_json(value).decode()


def dump_generation_provenance(value: CandidateGenerationProvenance | None) -> str | None:
    return None if value is None else _GENERATION_ADAPTER.dump_json(value).decode()


def dump_background_provenance(value: CandidateBackgroundProvenance | None) -> str | None:
    return None if value is None else _BACKGROUND_ADAPTER.dump_json(value).decode()


def load_persona_domain(payload: str | None) -> CandidatePersonaDomain | None:
    """Read one stored domain, treating a token the vocabulary dropped as simply absent."""
    if payload is None:
        return None
    try:
        return CandidatePersonaDomain(payload)
    except ValueError:
        return None


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
        persona_domain=load_persona_domain(row[5]),
        caption=row[6],
        hypothesis=row[7],
        refs_used=_REFS_ADAPTER.validate_json(row[8]),
        principles_applied=_PRINCIPLES_ADAPTER.validate_json(row[9]),
        shooting_order=row[10],
        image_inputs=(None if row[11] is None else _IMAGE_INPUTS_ADAPTER.validate_json(row[11])),
        ai_verdict=row[12],
        image_path=row[13],
        image_sha256=row[14],
        agent_run_id=row[15],
        generation_provenance=(
            None if row[16] is None else _GENERATION_ADAPTER.validate_json(row[16])
        ),
        background_provenance=(
            None if row[17] is None else _BACKGROUND_ADAPTER.validate_json(row[17])
        ),
        posting_slot=CandidatePostingSlot(row[18]),
        status=CandidateStatus(row[19]),
        review_note=row[20],
        revision=row[21],
        created_at=row[22],
        updated_at=row[23],
        account_id=None if row[24] is None else MarketingAccountId(row[24]),
    )
