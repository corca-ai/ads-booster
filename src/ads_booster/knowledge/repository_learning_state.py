"""Learning-round state transitions called from the batch repository."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.knowledge.learning_contracts import LearningRound, LearningRoundState

if TYPE_CHECKING:
    import sqlite3

_STRING_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])
_STRING_PAIR_ROWS: TypeAdapter[list[tuple[str, str]]] = TypeAdapter(list[tuple[str, str]])
_OPTIONAL_STRING_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)


def finish_learning_rounds(connection: sqlite3.Connection, batch_id: str) -> None:
    """Advance sealed rounds after an existing curation batch reaches terminal state."""
    rows = _STRING_ROWS.validate_python(
        connection.execute(
            """SELECT DISTINCT sealed_round_id FROM learning_admissions
            WHERE batch_id=? AND sealed_round_id IS NOT NULL""",
            (batch_id,),
        ).fetchall()
    )
    for (round_id,) in rows:
        states = tuple(
            state
            for (state,) in _STRING_ROWS.validate_python(
                connection.execute(
                    """SELECT DISTINCT batch.state FROM learning_admissions AS admission
                    JOIN curation_batches AS batch USING(batch_id)
                    WHERE admission.sealed_round_id=?""",
                    (round_id,),
                ).fetchall()
            )
        )
        if any(state in {"collecting", "ready", "running"} for state in states):
            continue
        job_states = tuple(
            state
            for (state,) in _STRING_ROWS.validate_python(
                connection.execute(
                    """SELECT DISTINCT job.state FROM learning_admissions AS admission
                    JOIN jobs AS job ON job.job_id=admission.job_id
                    WHERE admission.sealed_round_id=?""",
                    (round_id,),
                ).fetchall()
            )
        )
        if any(
            state in {"queued", "running", "waiting_dependency", "awaiting_answer"}
            for state in job_states
        ):
            continue
        state = (
            LearningRoundState.CANCELLED
            if "cancelled" in states or any(item in {"failed", "cancelled"} for item in job_states)
            else LearningRoundState.COMPLETED
        )
        current = _OPTIONAL_STRING_ROW.validate_python(
            connection.execute(
                "SELECT round_json FROM learning_rounds WHERE round_id=?",
                (round_id,),
            ).fetchone()
        )
        if current is None:
            continue
        round_ = LearningRound.model_validate_json(current[0]).model_copy(update={"state": state})
        _ = connection.execute(
            "UPDATE learning_rounds SET state=?,round_json=? WHERE round_id=?",
            (state.value, round_.model_dump_json(), round_id),
        )


def start_learning_rounds(connection: sqlite3.Connection, batch_id: str) -> None:
    """Mark a sealed round running when one of its partitioned batches is claimed."""
    rows = _STRING_PAIR_ROWS.validate_python(
        connection.execute(
            """SELECT DISTINCT round_.round_id,round_.round_json
            FROM learning_admissions AS admission
            JOIN learning_rounds AS round_ ON round_.round_id=admission.sealed_round_id
            WHERE admission.batch_id=? AND round_.state='ready'""",
            (batch_id,),
        ).fetchall()
    )
    for round_id, encoded in rows:
        round_ = LearningRound.model_validate_json(encoded).model_copy(
            update={"state": LearningRoundState.RUNNING}
        )
        _ = connection.execute(
            "UPDATE learning_rounds SET state='running',round_json=? WHERE round_id=?",
            (round_.model_dump_json(), round_id),
        )


__all__ = ["finish_learning_rounds", "start_learning_rounds"]
