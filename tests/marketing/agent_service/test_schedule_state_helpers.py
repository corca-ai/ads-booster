from datetime import UTC, datetime, timedelta

from ads_booster.agent.service.schedule_runtime import _blocked_occurrence
from ads_booster.contracts.agent_schedule import ScheduleOccurrence, ScheduleOccurrenceState


def test_blocked_occurrence_preserves_identity_and_records_reason() -> None:
    # Given
    created_at = datetime(2026, 9, 15, tzinfo=UTC)
    occurrence = ScheduleOccurrence(
        occurrence_id="occurrence",
        schedule_id="schedule",
        schedule_revision=2,
        scheduled_for=created_at,
        run_id="run",
        created_at=created_at,
        updated_at=created_at,
    )
    blocked_at = created_at + timedelta(minutes=1)

    # When
    blocked = _blocked_occurrence(
        occurrence, reason="schedule_authorization_source_changed", now=blocked_at
    )

    # Then
    assert blocked == occurrence.model_copy(
        update={
            "state": ScheduleOccurrenceState.BLOCKED,
            "reason": "schedule_authorization_source_changed",
            "updated_at": blocked_at,
        }
    )
