from __future__ import annotations

import logging
import subprocess
import sys
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.providers.threads_api import ThreadsApiError
from ads_booster.threads.oauth_diagnostics import OAuthDiagnosticEvent, OAuthDiagnostics

if TYPE_CHECKING:
    import pytest


def test_local_storage_failure_logs_only_allowlisted_metadata(
    caplog: pytest.LogCaptureFixture,
) -> None:
    diagnostic = OAuthDiagnostics(stage="persist_account")
    diagnostic.finish(OSError("credential=fixture-secret /private/user/token"))
    record = caplog.records[-1]
    event = TypeAdapter(OAuthDiagnosticEvent).validate_json(record.getMessage())
    assert event["stage"] == "persist_account"
    assert event["error_type"] == "OSError"
    assert event["meta_code"] is None
    assert event["completed_stages"] == []
    assert "fixture-secret" not in caplog.text
    assert "private/user" not in caplog.text
    assert record.levelno == logging.WARNING
    assert record.exc_info is None


def test_authorization_window_rejection_has_no_completed_service_stages(
    caplog: pytest.LogCaptureFixture,
) -> None:
    diagnostic = OAuthDiagnostics(stage="authorization_callback")
    diagnostic.finish(ThreadsApiError(401, 1349245, "sensitive-provider-message"))
    event = TypeAdapter(OAuthDiagnosticEvent).validate_json(caplog.records[-1].getMessage())
    assert event["stage"] == "authorization_callback"
    assert event["completed_stages"] == []
    assert event["meta_code"] == 1349245
    assert "sensitive-provider-message" not in caplog.text


def test_failure_json_reaches_stderr_without_global_logging_configuration() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """from ads_booster.threads.oauth_diagnostics import OAuthDiagnostics
from ads_booster.providers.threads_api import ThreadsApiError
OAuthDiagnostics(stage='granted_scopes').finish(ThreadsApiError(400,190,'SECRET'))
""",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    event = TypeAdapter(OAuthDiagnosticEvent).validate_json(result.stderr.strip())
    assert event["stage"] == "granted_scopes"
    assert event["meta_code"] == 190
    assert "SECRET" not in result.stderr
    assert result.stdout == ""
