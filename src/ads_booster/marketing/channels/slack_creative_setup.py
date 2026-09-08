"""Optional Slack image tool composition, preserving the installed service lifecycle."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, cast
from urllib.request import Request

from pydantic import TypeAdapter

from ads_booster.contracts.tool_capability import ToolReadiness
from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.creative_assets import SqliteCreativeAssetRepository
from ads_booster.marketing.agent_service.oauth import open_auth_request
from ads_booster.marketing.agent_service.slack_asset_intake import (
    SlackAssetIntakeTool,
    slack_asset_import_descriptor,
    slack_file_inspect_descriptor,
)
from ads_booster.marketing.agent_service.slack_image_files import SlackImageFiles
from ads_booster.marketing.agent_service.slack_image_review import (
    SlackImageReviewTool,
    slack_image_review_descriptor,
)
from ads_booster.marketing.tool_adapters.compatibility import DelegatingToolAdapter
from ads_booster.providers.codex_cli import CodexCli
from ads_booster.providers.codex_reasoning import CodexReasoningProvider
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from ads_booster.contracts.tool_capability import ToolDescriptor
    from ads_booster.marketing.agent_service.application import MarketingAgentService


_READINESS_TTL = 60
_MAX_AUTH_BYTES = 64 * 1024
_MAX_SCOPE_HEADER = 4096
_AUTH_URL = "https://slack.com/api/auth.test"
_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class AuthResponse(Protocol):
    def read(self, size: int = -1) -> bytes: ...
    def geturl(self) -> str: ...
    def getheader(self, name: str, default: str = "") -> str: ...
    def close(self) -> None: ...


def _open(request: Request, *, timeout: float) -> AuthResponse:
    return cast("AuthResponse", open_auth_request(request, timeout=timeout))


@dataclass(frozen=True, slots=True)
class SlackImagePermissionProbe:
    """Observe actual token grants, not requested installation scopes.

    Slack documents x-oauth-scopes on Web API responses in installing-with-oauth;
    auth.test supplies the authenticated team and bot identity without requiring scopes.
    Missing headers, redirects or unavailable identity checks never imply readiness.
    """

    team_id: str
    token: str = field(repr=False)
    opener: Callable[..., AuthResponse] = field(default=_open, repr=False)

    def check(self, *, now: datetime) -> ToolReadiness:
        reason = "slack_image_permission_unavailable"
        try:
            response = self.opener(
                Request(
                    _AUTH_URL,
                    data=b"",
                    headers={"Authorization": f"Bearer {self.token}"},
                    method="POST",
                ),
                timeout=5,
            )
            try:
                if response.geturl() != _AUTH_URL:
                    raise ValueError("slack_permission_redirect")
                data = response.read(_MAX_AUTH_BYTES + 1)
                if len(data) > _MAX_AUTH_BYTES:
                    raise ValueError("slack_permission_response_too_large")
                body = _JSON.validate_json(data)
                scopes = response.getheader("x-oauth-scopes", "")
                if len(scopes) > _MAX_SCOPE_HEADER:
                    raise ValueError("slack_permission_header_invalid")
                bot_id = body.get("bot_id")
                if (
                    body.get("ok") is not True
                    or body.get("team_id") != self.team_id
                    or not isinstance(bot_id, str)
                    or not bot_id
                ):
                    reason = "slack_image_identity_unverified"
                elif "files:read" not in {scope.strip() for scope in scopes.split(",")}:
                    reason = "slack_image_files_read_missing"
                else:
                    return ToolReadiness(
                        ready=True, observed_at=now, max_age_seconds=_READINESS_TTL
                    )
            finally:
                response.close()
        except Exception:  # noqa: BLE001 - optional permission probe must not break onboarding.
            reason = "slack_image_permission_unavailable"
        return ToolReadiness(
            ready=False,
            reason_code=reason,
            observed_at=now,
            max_age_seconds=_READINESS_TTL,
        )


@dataclass(slots=True)
class SlackCreativeCatalog:
    base: ToolRegistry
    probe: SlackImagePermissionProbe
    monotonic: Callable[[], float] = field(default=time.monotonic, repr=False)
    _readiness: ToolReadiness | None = field(default=None, init=False)
    _checked_at: float = field(default=0, init=False)

    def descriptors(self, *, now: datetime) -> tuple[ToolDescriptor, ...]:
        tick = self.monotonic()
        readiness = self._readiness
        if (
            readiness is None
            or not 0 <= (now - readiness.observed_at).total_seconds() < _READINESS_TTL
            or not 0 <= tick - self._checked_at < _READINESS_TTL
        ):
            readiness = self.probe.check(now=now)
            self._readiness = readiness
            self._checked_at = self.monotonic()
        descriptor = slack_image_review_descriptor(now=readiness.observed_at, ready=readiness.ready)
        descriptor = descriptor.model_copy(update={"readiness": readiness})
        return (
            *self.base.current_descriptors(now=now),
            descriptor,
            slack_file_inspect_descriptor(
                now=readiness.observed_at, ready=readiness.ready
            ).model_copy(update={"readiness": readiness}),
            slack_asset_import_descriptor(
                now=readiness.observed_at, ready=readiness.ready
            ).model_copy(update={"readiness": readiness}),
        )


def connect_slack_creative(  # noqa: PLR0913 - identity, opt-in credentials and probe injection.
    service: MarketingAgentService,
    *,
    tenant_id: str,
    team_id: str,
    token: str,
    now: datetime,
    opener: Callable[..., AuthResponse] = _open,
) -> JsonObject:
    if "creative.image.review" in service.tools:
        return {"ready": False, "reason": "already_registered_check_current_catalog"}
    provider = service.reasoning
    if (
        not isinstance(provider, CodexReasoningProvider)
        or not isinstance(provider.codex, CodexCli)
        or not provider.codex.model
        or not tenant_id
        or not team_id
        or not token
    ):
        return {"ready": False, "reason": "slack_image_provider_unconfigured"}
    catalog = SlackCreativeCatalog(
        service.registry,
        SlackImagePermissionProbe(team_id, token, opener),
    )
    descriptors = catalog.descriptors(now=now)
    readiness = descriptors[-1].readiness
    if not readiness.ready:
        return {
            "ready": False,
            "reason": readiness.reason_code,
            "required_scope": "files:read",
            "next_action": "Add files:read, reinstall the app, confirm bot/team, and restart.",
        }
    root = service.repository.database_path.parent / "artifacts"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    tool = SlackImageReviewTool(
        database_path=service.repository.database_path,
        artifact_root=root,
        token=token,
        codex=provider.codex,
        tenant_id=tenant_id,
        expected_team_id=team_id,
    )
    intake = SlackAssetIntakeTool(
        repository=service.repository,
        assets=SqliteCreativeAssetRepository(service.repository.database_path, root),
        files=SlackImageFiles(
            database_path=service.repository.database_path,
            artifact_root=root,
            tenant_id=tenant_id,
            token=token,
            expected_team_id=team_id,
        ),
    )
    service.tools = {
        **service.tools,
        "creative.file.inspect": DelegatingToolAdapter(
            capability_id="creative.file.inspect",
            version="1",
            executor_id="slack-bound-file-inspect",
            executor=intake.inspect,
        ),
        "creative.asset.import": DelegatingToolAdapter(
            capability_id="creative.asset.import",
            version="1",
            executor_id="slack-approved-asset-import",
            executor=intake.import_asset,
        ),
        "creative.image.review": DelegatingToolAdapter(
            capability_id="creative.image.review",
            version="1",
            executor_id="official-codex-image-review",
            executor=tool.execute,
        ),
    }
    service.registry = ToolRegistry(descriptors, provider=catalog)
    return {"ready": True, "required_scope": "files:read", "readiness_ttl_seconds": _READINESS_TTL}
