from __future__ import annotations

import stat
import subprocess
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from ads_booster.cli.marketing import app
from ads_booster.marketing.service import (
    CredentialProvider,
    MarketingBridgeConfigStore,
    MarketingBridgeServiceConfig,
    resolve_bridge_credentials,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _config(
    *,
    credential_provider: CredentialProvider = CredentialProvider.ENVIRONMENT,
    credential_command: tuple[str, ...] = (),
) -> MarketingBridgeServiceConfig:
    return MarketingBridgeServiceConfig(
        account_id="cf-account",
        queue_id="queue-id",
        control_plane_url="https://worker.example.test",
        executor="simulation",
        poll_seconds=2.0,
        credential_provider=credential_provider,
        credential_command=credential_command,
    )


def test_bridge_config_is_portable_and_contains_no_credentials(tmp_path: Path) -> None:
    store = MarketingBridgeConfigStore(tmp_path / "agent-home")

    store.save(_config())

    assert store.load() == _config()
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    serialized = store.path.read_text()
    assert "queue_token" not in serialized
    assert "worker_token" not in serialized


def test_environment_credentials_can_be_injected_on_any_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACE_MARKETING_QUEUE_TOKEN", "queue-secret")
    monkeypatch.setenv("TRACE_MARKETING_WORKER_TOKEN", "worker-secret")

    credentials = resolve_bridge_credentials(_config())

    assert credentials.queue_token == "queue-secret"  # noqa: S105 - inert fixture.
    assert credentials.worker_token == "worker-secret"  # noqa: S105 - inert fixture.


def test_external_secret_command_is_executed_without_a_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout='{"queue_token":"queue-secret","worker_token":"worker-secret"}',
            stderr="",
        )

    monkeypatch.setattr("ads_booster.marketing.service.subprocess.run", fake_run)
    config = _config(
        credential_provider=CredentialProvider.COMMAND,
        credential_command=("/usr/local/bin/secret-provider", "trace-marketing"),
    )

    credentials = resolve_bridge_credentials(config)

    assert credentials.queue_token == "queue-secret"  # noqa: S105 - inert fixture.
    assert calls == [["/usr/local/bin/secret-provider", "trace-marketing"]]


def test_bridge_configure_writes_only_non_secret_worker_enrollment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "agent-home"
    values = {
        "CLOUDFLARE_ACCOUNT_ID": "cf-account",
        "TRACE_MARKETING_QUEUE_ID": "queue-id",
        "TRACE_MARKETING_CONTROL_PLANE_URL": "https://worker.example.test",
        "TRACE_AGENT_HOME": str(home),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    result = CliRunner().invoke(app, ["bridge-configure"])

    assert result.exit_code == 0, result.stdout + str(result.exception)
    config = MarketingBridgeConfigStore(home).load()
    assert config.executor == "simulation"
    assert config.credential_provider == "environment"
    assert "token" not in result.stdout.lower()
