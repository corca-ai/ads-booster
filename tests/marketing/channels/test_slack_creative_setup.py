from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ads_booster.marketing.agent_core.registry import CapabilityPolicy, ToolRegistry
from ads_booster.marketing.channels.slack_creative_setup import (
    SlackCreativeCatalog,
    SlackImagePermissionProbe,
    connect_slack_creative,
)
from ads_booster.providers.codex_cli import CodexCli
from ads_booster.providers.codex_reasoning import CodexReasoningProvider
from tests.marketing.agent_service.test_application import (
    NOW,
    AskThenStopReasoning,
    _service,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from urllib.request import Request


class Response:
    def __init__(self, *, ok: bool, team: str, scopes: str) -> None:
        self.data: bytes = json.dumps({"ok": ok, "team_id": team, "bot_id": "B123"}).encode()
        self.scopes: str = scopes
        self.closed: bool = False

    def read(self, size: int = -1) -> bytes:
        return self.data[:size]

    def geturl(self) -> str:
        return "https://slack.com/api/auth.test"

    def getheader(self, name: str, default: str = "") -> str:
        assert name == "x-oauth-scopes"
        _ = default
        return self.scopes

    def close(self) -> None:
        self.closed = True


class AuthHTTP:
    def __init__(self) -> None:
        self.ok: bool = True
        self.team: str = "T123"
        self.scopes: str = "chat:write, files:read"
        self.unavailable: bool = False
        self.responses: list[Response] = []

    def open(self, request: Request, *, timeout: float) -> Response:
        assert request.full_url == "https://slack.com/api/auth.test"
        assert request.get_method() == "POST"
        assert request.headers["Authorization"] == "Bearer synthetic-token"
        assert timeout == 5
        if self.unavailable:
            message = "fixture HTTP unavailable with private detail"
            raise OSError(message)
        response = Response(ok=self.ok, team=self.team, scopes=self.scopes)
        self.responses.append(response)
        return response


def test_connect_requires_observed_bot_scope_before_exposing_image_tool(tmp_path: Path) -> None:
    service = _service(tmp_path / "state.db", AskThenStopReasoning())
    service.reasoning = CodexReasoningProvider(
        CodexCli(Path("/synthetic/codex"), model="fixture-model"),
        tmp_path / "reasoning",
        model_id="fixture-model",
    )
    http = AuthHTTP()
    status = connect_slack_creative(
        service,
        tenant_id="trace",
        team_id="T123",
        token="synthetic-token",  # noqa: S106 - synthetic fixture only.
        now=NOW,
        opener=http.open,
    )
    assert status["ready"] is True
    assert {
        "creative.image.review",
        "creative.file.inspect",
        "creative.asset.import",
    } <= service.tools.keys()
    assert len(http.responses) == 1
    assert all(response.closed for response in http.responses)
    current = next(
        item
        for item in service.registry.current_descriptors(now=NOW)
        if item.capability_id == "creative.image.review"
    )
    assert current.readiness.ready
    assert len(http.responses) == 1


@pytest.mark.parametrize("failure", ["missing-scope", "token-revoked", "wrong-team", "network"])
def test_optional_probe_failure_preserves_onboarding_and_hides_tool(
    tmp_path: Path,
    failure: str,
) -> None:
    service = _service(tmp_path / "state.db", AskThenStopReasoning())
    service.reasoning = CodexReasoningProvider(
        CodexCli(Path("/synthetic/codex"), model="fixture-model"),
        tmp_path / "reasoning",
        model_id="fixture-model",
    )
    http = AuthHTTP()
    if failure == "missing-scope":
        http.scopes = "chat:write"
    elif failure == "token-revoked":
        http.ok = False
    elif failure == "wrong-team":
        http.team = "TOTHER"
    else:
        http.unavailable = True
    registry = service.registry
    status = connect_slack_creative(
        service,
        tenant_id="trace",
        team_id="T123",
        token="synthetic-token",  # noqa: S106 - synthetic fixture only.
        now=NOW,
        opener=http.open,
    )
    assert status["ready"] is False
    assert status["required_scope"] == "files:read"
    assert "synthetic-token" not in str(status)
    assert "private detail" not in str(status)
    assert "creative.image.review" not in service.tools
    assert service.registry is registry
    assert not (tmp_path / "artifacts").exists()
    assert all(response.closed for response in http.responses)


@pytest.mark.parametrize("tool_index", [0, 1, 2])
def test_catalog_retains_observation_time_and_revokes_dispatch_after_scope_loss(
    tool_index: int,
) -> None:
    http = AuthHTTP()
    catalog = SlackCreativeCatalog(
        ToolRegistry(()), SlackImagePermissionProbe("T123", "synthetic-token", http.open)
    )
    frozen = catalog.descriptors(now=NOW)[tool_index]
    assert frozen.readiness.observed_at == NOW
    http.scopes = "chat:write"
    cached = catalog.descriptors(now=NOW + timedelta(seconds=10))[tool_index]
    assert cached.readiness.observed_at == NOW
    assert len(http.responses) == 1
    registry = ToolRegistry((frozen,), provider=catalog)
    with pytest.raises(ValueError, match="tool_dispatch_no_longer_available"):
        _ = registry.require_current_dispatch(
            frozen, policy=CapabilityPolicy(), now=NOW + timedelta(seconds=61)
        )
    assert len(http.responses) == 2
    current = catalog.descriptors(now=NOW + timedelta(seconds=61))[tool_index]
    assert current.readiness.ready is False
    assert current.readiness.reason_code == "slack_image_files_read_missing"


def test_monotonic_ttl_refreshes_even_when_caller_reuses_run_start_time() -> None:
    http = AuthHTTP()
    tick = [0.0]
    catalog = SlackCreativeCatalog(
        ToolRegistry(()),
        SlackImagePermissionProbe("T123", "synthetic-token", http.open),
        monotonic=lambda: tick[0],
    )
    assert catalog.descriptors(now=NOW)[0].readiness.ready
    http.ok = False
    tick[0] = 61
    assert catalog.descriptors(now=NOW)[0].readiness.ready is False
    assert len(http.responses) == 2
