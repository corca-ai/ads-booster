from __future__ import annotations

from typing import TYPE_CHECKING

from ads_booster.config.settings import AgentSettings

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


def test_agent_settings_default_to_luna_with_xhigh_reasoning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given no model or reasoning environment override
    monkeypatch.delenv("TRACE_AGENT_MODEL", raising=False)
    monkeypatch.delenv("TRACE_AGENT_REASONING_EFFORT", raising=False)

    # When Agent settings are created
    settings = AgentSettings.from_environment(workspace=tmp_path)

    # Then every production composition receives the requested Luna xhigh defaults
    assert settings.model == "gpt-5.6-luna"
    assert settings.reasoning_effort == "xhigh"
