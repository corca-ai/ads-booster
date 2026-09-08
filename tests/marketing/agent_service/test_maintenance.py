from dataclasses import replace
from threading import Event, Thread
from typing import TYPE_CHECKING

from ads_booster.agent.service.maintenance import MaintenanceGate
from tests.marketing.agent_service.test_http_api import _api  # pyright: ignore[reportPrivateUsage]
from tests.marketing.channels.test_slack_commands import NOW, request, setup_commands

if TYPE_CHECKING:
    from pathlib import Path


def test_maintenance_drains_existing_work_and_blocks_new_requests(tmp_path: Path) -> None:
    gate = MaintenanceGate(tmp_path / "maintenance", "candidate-sha")
    started, finish = Event(), Event()

    def existing_request() -> None:
        with gate.work() as admitted:
            assert admitted
            started.set()
            assert finish.wait(5)

    thread = Thread(target=existing_request)
    thread.start()
    assert started.wait(5)
    api = replace(_api(tmp_path), maintenance=gate)
    try:
        (tmp_path / "maintenance").touch()
        health = api.dispatch("GET", "/health", authorization=None)
        assert isinstance(health.body, dict)
        assert health.body["active"] == 1
        assert health.body["maintenance"] is True
        response = api.dispatch("POST", "/v1/runs", authorization="Bearer secret")
        assert response.status == 503
        assert api.service.repository.list_runs("trace") == ()
    finally:
        finish.set()
        thread.join(5)
    assert gate.health()["active"] == 0
    (tmp_path / "maintenance").unlink()
    assert api.dispatch("GET", "/v1/runs", authorization="Bearer secret").status == 200


def test_slack_only_mode_never_exposes_web_or_bearer_api(tmp_path: Path) -> None:
    api = replace(_api(tmp_path), slack_only=True)
    for path in ["/", "/runs/one", "/auth/config", "/v1/runs"]:
        assert api.dispatch("GET", path, authorization="Bearer secret").status == 404
    assert api.dispatch("GET", "/health", authorization=None).status == 200
    assert api.dispatch("POST", "/channels/slack/commands", authorization=None).status == 404


def test_signed_slack_request_works_without_oauth_but_forgery_fails(tmp_path: Path) -> None:

    commands = setup_commands(tmp_path)
    api = replace(_api(tmp_path), slack_only=True, slack_commands=commands, bearer_token="")
    body, headers = request()
    assert (
        api.dispatch(
            "POST",
            "/channels/slack/commands",
            authorization=None,
            body=body,
            headers=headers,
            now=NOW,
        ).status
        == 200
    )
    assert (
        api.dispatch(
            "POST",
            "/channels/slack/commands",
            authorization=None,
            body=body + b"x",
            headers=headers,
            now=NOW,
        ).status
        == 403
    )
