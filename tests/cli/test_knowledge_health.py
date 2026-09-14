from __future__ import annotations

import os
from threading import Event, Thread, current_thread
from typing import TYPE_CHECKING
from unittest.mock import Mock

from ads_booster.cli import marketing
from tests.marketing.channels.test_slack_events import setup_events

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest

    from ads_booster.channels.http.http_api import MarketingAgentApi


def test_service_health_degrades_when_knowledge_thread_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events, _ = setup_events(tmp_path)
    service = events.commands.application.service
    for name in tuple(os.environ):
        if name.startswith("TRACE_MARKETING_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("TRACE_MARKETING_SERVICE_TOKEN", "fixture")
    monkeypatch.setenv("TRACE_MARKETING_RELEASE", "candidate-sha")
    maintenance = tmp_path / "maintenance"
    monkeypatch.setenv("TRACE_MARKETING_MAINTENANCE_FILE", str(maintenance))
    for suffix in ("ROOT", "CONTROL_ROOT", "POLICY"):
        monkeypatch.setenv(f"TRACE_MARKETING_KNOWLEDGE_{suffix}", str(tmp_path / suffix))
    entered, finish = Event(), Event()
    threads: list[Thread] = []

    def run_knowledge() -> None:
        threads.append(current_thread())
        entered.set()
        _ = finish.wait(5)

    runtime = Mock(run_continuous=run_knowledge, request_stop=finish.set)
    monkeypatch.setattr(
        marketing, "build_installed_knowledge_runtime", Mock(return_value=Mock(runtime=runtime))
    )
    monkeypatch.setattr(
        marketing, "resolve_codex_executable", Mock(return_value=tmp_path / "codex")
    )
    monkeypatch.setattr(
        marketing, "build_installed_marketing_agent_service", Mock(return_value=service)
    )
    monkeypatch.setattr(marketing, "connect_trace_post", Mock(return_value=Mock()))
    monkeypatch.setattr(marketing, "browser_from_env", Mock(return_value=None))
    monkeypatch.setattr(marketing, "slack_from_env", Mock(return_value=None))
    monkeypatch.setattr(marketing, "events_from_env", Mock(return_value=None))
    monkeypatch.setattr(marketing, "_configured_daily_scheduler", Mock(return_value=None))

    def serve(
        api: MarketingAgentApi,
        *,
        host: str,
        port: int,
        on_started: Callable[[], None],
    ) -> None:
        _ = host, port
        on_started()
        assert entered.wait(5)
        try:
            healthy = api.dispatch("GET", "/health", authorization=None)
            assert healthy.status == 200
            assert isinstance(healthy.body, dict)
            assert healthy.body["knowledge_worker"] == "running"
            finish.set()
            threads[0].join(5)
            assert not threads[0].is_alive()
            maintenance.touch()
            failed = api.dispatch("GET", "/health", authorization=None)
            assert failed.status == 503
            assert isinstance(failed.body, dict)
            assert failed.body["status"] == "degraded"
            assert failed.body["knowledge_worker"] == "stopped"
            assert failed.body["maintenance"] is True
            for key in ("owner", "release", "update_protocol"):
                assert failed.body[key] == healthy.body[key]
            assert isinstance(failed.body["active"], int)
        finally:
            finish.set()

    monkeypatch.setattr(marketing, "serve_marketing_agent_api", serve)
    marketing.service_run(model="fixture", home=tmp_path, tenant="team", principal="member")
