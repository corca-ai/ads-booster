"""Installed composition for browser login and a single approved Slack workspace."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from urllib.request import Request

from pydantic import Field, TypeAdapter

from ads_booster.contracts.models import ContractModel
from ads_booster.marketing.agent_service.browser_login import BrowserLogin, BrowserLoginConfig
from ads_booster.marketing.agent_service.jobs import AgentJobs
from ads_booster.marketing.agent_service.maintenance import MaintenanceGate
from ads_booster.marketing.agent_service.oauth import (
    AccessTokenAuthenticator,
    exchange_authorization_code,
    open_auth_request,
)
from ads_booster.marketing.channels.base import ChannelApplicationAdapter
from ads_booster.marketing.channels.contracts import (
    ChannelIdentityBinding,
    ChannelInstallation,
    ChannelKind,
)
from ads_booster.marketing.channels.slack import SlackRequestVerifier
from ads_booster.marketing.channels.slack_commands import SlackCommands
from ads_booster.marketing.channels.store import SqliteChannelStore
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Mapping
    from threading import Event

    from ads_booster.marketing.agent_service.application import MarketingAgentService
    from ads_booster.marketing.channels.slack_events import SlackEvents

_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_MAX_INSTALLATION_BYTES = 65536
_ID = Annotated[str, Field(min_length=1, max_length=120)]


class SlackMember(ContractModel):
    slack_user_id: _ID
    member_id: _ID
    can_approve: bool = False


class SlackInstallationConfig(ContractModel):
    app_id: _ID
    team_id: _ID
    tenant_id: _ID
    members: Annotated[list[SlackMember], Field(min_length=1, max_length=100)]


def browser_from_env(
    env: Mapping[str, str], auth: AccessTokenAuthenticator | None
) -> BrowserLogin | None:
    keys = (
        "TRACE_MARKETING_LOGIN_AUTHORIZE_URL",
        "TRACE_MARKETING_LOGIN_TOKEN_URL",
        "TRACE_MARKETING_LOGIN_CLIENT_ID",
        "TRACE_MARKETING_LOGIN_CLIENT_SECRET",
    )
    if not any(env.get(key) for key in keys):
        return None
    if auth is None:
        raise ValueError("agent_browser_login_requires_oauth")
    return BrowserLogin(
        BrowserLoginConfig(
            public_origin=required(env, "TRACE_MARKETING_PUBLIC_ORIGIN"),
            authorization_url=required(env, keys[0]),
            token_url=required(env, keys[1]),
            client_id=required(env, keys[2]),
            client_secret=required(env, keys[3]),
            scope=env.get("TRACE_MARKETING_LOGIN_SCOPE", "openid profile"),
        ),
        auth,
        exchange_authorization_code,
    )


def slack_from_env(
    env: Mapping[str, str], service: MarketingAgentService, *, tenant_id: str | None = None
) -> SlackCommands | None:
    keys = ("TRACE_MARKETING_SLACK_SIGNING_SECRET", "TRACE_MARKETING_SLACK_INSTALLATION")
    if not any(env.get(key) for key in keys):
        return None
    path = Path(required(env, keys[1])).expanduser()
    if path.stat().st_size > _MAX_INSTALLATION_BYTES:
        raise ValueError("agent_slack_installation_too_large")
    config = SlackInstallationConfig.model_validate_json(path.read_text())
    if tenant_id is not None and config.tenant_id != tenant_id:
        raise ValueError("agent_slack_workspace_mismatch")
    store = SqliteChannelStore(service.repository.database_path)
    installation_id = f"slack-{config.team_id}"
    # Stable metadata enables restarts without rewriting identity authority.
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    store.put_installation(
        ChannelInstallation(
            schema_version="trace.channel-installation.v1",
            installation_id=installation_id,
            channel=ChannelKind.SLACK,
            external_workspace_id=config.team_id,
            tenant_id=config.tenant_id,
            credential_reference="env:TRACE_MARKETING_SLACK_BOT_TOKEN",
            created_at=created_at,
        )
    )
    for member in config.members:
        store.put_identity(
            ChannelIdentityBinding(
                schema_version="trace.channel-identity-binding.v1",
                binding_id=f"{installation_id}-{member.slack_user_id}",
                installation_id=installation_id,
                external_user_id=member.slack_user_id,
                tenant_id=config.tenant_id,
                member_id=member.member_id,
                can_approve=member.can_approve,
                created_at=created_at,
            )
        )
    token = required(env, "TRACE_MARKETING_SLACK_BOT_TOKEN")

    def send(payload: JsonObject) -> JsonObject:
        request = Request(
            "https://slack.com/api/chat.postMessage",
            data=json.dumps(payload).encode(),
            headers={"authorization": f"Bearer {token}", "content-type": "application/json"},
            method="POST",
        )
        response = open_auth_request(request, timeout=15)
        return _JSON.validate_json(response.read())

    return SlackCommands(
        ChannelApplicationAdapter(service, store, required(env, "TRACE_MARKETING_PUBLIC_ORIGIN")),
        SlackRequestVerifier(required(env, keys[0]).encode()),
        config.app_id,
        config.team_id,
        required(env, "TRACE_MARKETING_SLACK_CHANNEL_ID"),
        frozenset(member.slack_user_id for member in config.members),
        send,
        public_links=env.get("TRACE_MARKETING_SLACK_ONLY") != "1",
    )


def run_slack_worker(
    commands: SlackCommands,
    stop: Event,
    gate: MaintenanceGate | None = None,
    events: SlackEvents | None = None,
) -> None:
    gate = gate or MaintenanceGate()
    recovered = False
    while not stop.is_set():
        try:
            with gate.work() as admitted:
                worked = False
                if admitted:
                    if not recovered:
                        commands.recover()
                        if events is not None:
                            events.recover()
                        recovered = True
                    worked = commands.work_once(now=datetime.now(UTC))
                    if events is not None:
                        worked = events.work_once(now=datetime.now(UTC)) or worked
        except Exception:  # noqa: BLE001 - durable background boundary; no blind mutation retry.  # Keep failures private and leave durable state for operator inspection.
            worked = False
        if not worked:
            _ = stop.wait(1)


def required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ValueError(f"agent_config_missing:{key}")
    return value


def run_web_jobs(jobs: AgentJobs, stop: Event, gate: MaintenanceGate | None = None) -> None:
    gate = gate or MaintenanceGate()
    recovered = False
    while not stop.is_set():
        try:
            with gate.work() as admitted:
                worked = False
                if admitted:
                    if not recovered:
                        jobs.recover()
                        recovered = True
                    worked = jobs.work_once(now=datetime.now(UTC))
        except Exception:  # noqa: BLE001 - durable background boundary; no blind mutation retry.
            worked = False
        if not worked:
            _ = stop.wait(1)
