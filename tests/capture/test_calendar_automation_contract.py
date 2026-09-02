import json

from ads_booster.capture.calendar_automation_contract import CalendarAutomationResult


def test_calendar_result_when_helper_prepares_twenty_events_then_parses_success() -> None:
    # Given Trace completed a request containing twenty Calendar events
    payload = json.dumps(
        {
            "schema_version": "trace.marketing-calendar-automation-result.v1",
            "operation": "prepare",
            "request_sha256": "a" * 64,
            "calendar_namespace": "trace-request-1",
            "status": "completed",
            "calendar_identifier": "trace-calendar-identifier",
            "event_count": 20,
        },
        separators=(",", ":"),
    )

    # When the Python worker parses the successful helper contract
    result = CalendarAutomationResult.model_validate_json(payload)

    # Then the full result remains valid for the request contract's weekly capacity
    assert result.event_count == 20
