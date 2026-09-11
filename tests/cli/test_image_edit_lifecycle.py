"""Optional image editing joins the existing service stop and maintenance lifecycle."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from threading import Event
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from ads_booster.cli import marketing
from tests.marketing.channels.test_slack_events import setup_events

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class FakeThread:
    configuration: dict[str, object]
    starts: int = 0
    joins: list[int] = field(default_factory=list)

    def start(self) -> None:
        self.starts += 1

    def is_alive(self) -> bool:
        return True

    def join(self, *, timeout: int) -> None:
        self.joins.append(timeout)


def _trace_connection_spy(
    monkeypatch: pytest.MonkeyPatch, service: object
) -> list[dict[str, object]]:
    connections: list[dict[str, object]] = []

    def connect(connected_service: object, **kwargs: object) -> Mock:
        assert connected_service is service
        connections.append(kwargs)
        return Mock()

    monkeypatch.setattr(marketing, "connect_trace_post", connect)
    return connections


@pytest.mark.parametrize("enabled", [False, True])
def test_service_starts_and_stops_optional_worker_after_http_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    events, messages = setup_events(tmp_path)
    service = events.commands.application.service
    for name in tuple(os.environ):
        if name.startswith("TRACE_MARKETING_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("TRACE_MARKETING_SERVICE_TOKEN", "fixture")
    config = tmp_path / "image.json"
    if enabled:
        monkeypatch.setenv("TRACE_MARKETING_IMAGE_EDIT_CONFIG", str(config))
    monkeypatch.setattr(
        marketing, "resolve_codex_executable", Mock(return_value=tmp_path / "codex")
    )
    monkeypatch.setattr(
        marketing, "build_installed_marketing_agent_service", Mock(return_value=service)
    )
    monkeypatch.setattr(marketing, "browser_from_env", Mock(return_value=None))
    monkeypatch.setattr(marketing, "slack_from_env", Mock(return_value=events.commands))
    monkeypatch.setattr(marketing, "events_from_env", Mock(return_value=events))
    monkeypatch.setattr(marketing, "_configured_daily_scheduler", Mock(return_value=None))
    tool = Mock()
    connections: list[dict[str, object]] = []

    def connect(connected_service: object, **kwargs: object) -> Mock:
        assert connected_service is service
        connections.append(kwargs)
        return tool

    monkeypatch.setattr(marketing, "connect_image_edit", connect)
    trace_connections = _trace_connection_spy(monkeypatch, service)
    threads: list[FakeThread] = []

    def thread(**kwargs: object) -> FakeThread:
        created = FakeThread(kwargs)
        threads.append(created)
        return created

    monkeypatch.setattr(marketing, "Thread", thread)

    def serve(*args: object, **kwargs: object) -> None:
        _ = args
        callback = kwargs["on_started"]
        assert callable(callback)
        assert all(item.starts == 0 for item in threads)
        _ = callback()

    monkeypatch.setattr(marketing, "serve_marketing_agent_api", serve)
    marketing.service_run(model="fixture", home=tmp_path, tenant="team", principal="member")
    assert len(connections) == int(enabled)
    if enabled:
        assert connections[0]["config_path"] == config
        completion = connections[0]["on_completed"]
        assert callable(completion)
        _ = completion("team", "missing-run", "fixture-event")
    assert [
        (connection["executable"], connection["model"]) for connection in trace_connections
    ] == [(tmp_path / "codex", "fixture")]
    assert len(threads) == (4 if enabled else 3)
    for item in threads:
        assert item.starts == 1
        assert item.joins == [5]
        args = item.configuration["args"]
        assert isinstance(args, tuple)
        stop = cast("tuple[object, ...]", args)[1]
        assert isinstance(stop, Event)
        assert stop.is_set()
    assert messages == []
