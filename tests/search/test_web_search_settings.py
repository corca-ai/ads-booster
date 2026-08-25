from __future__ import annotations

from typing import TYPE_CHECKING

from trace_capture.config.settings import AgentSettings

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_agent_settings_load_web_search_provider_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACE_AGENT_WEB_SEARCH_PROVIDER", "brave")
    monkeypatch.setenv("TRACE_AGENT_WEB_SEARCH_TIMEOUT_SECONDS", "7")

    settings = AgentSettings.from_environment(workspace=tmp_path)

    assert settings.web_search_provider == "brave"
    assert settings.web_search_timeout_seconds == 7.0
