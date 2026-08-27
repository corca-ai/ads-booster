from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, override

from ads_booster.auth.browser import BrowserOAuthOptions
from ads_booster.auth.codex import OAuthError
from ads_booster.providers.errors import ProviderError

if TYPE_CHECKING:
    from collections.abc import Callable

    from ads_booster.agent.session import AgentSession
    from ads_booster.auth.codex import CodexOAuth
    from ads_booster.config.settings import AgentSettings
    from ads_booster.providers.codex import CodexResponsesClient
    from ads_booster.providers.models import ProviderModel

OAUTH_TIMEOUT_SECONDS: Final = 900.0


class AgentControlError(RuntimeError):
    code: str
    message: str

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class AgentControlPort(Protocol):
    def set_session(self, session: AgentSession) -> None: ...

    def oauth_login(
        self,
        on_auth: Callable[[str], None],
    ) -> str | None: ...

    def auth_status(self) -> str: ...

    def auth_logout(self) -> None: ...

    def model(self) -> str: ...

    def reasoning(self) -> str | None: ...

    def models(self) -> tuple[ProviderModel, ...]: ...

    def workspace(self) -> str: ...

    def set_model(self, value: str) -> str: ...

    def set_reasoning(self, value: str | None) -> str | None: ...

    def set_workspace(self, value: str) -> str: ...


@dataclass(slots=True)  # noqa: MUTABLE_OK
class AgentControl:
    settings: AgentSettings
    oauth: CodexOAuth
    client: CodexResponsesClient
    session: AgentSession

    def set_session(self, session: AgentSession) -> None:
        self.session = session

    def oauth_login(
        self,
        on_auth: Callable[[str], None],
    ) -> str | None:
        options = BrowserOAuthOptions(
            open_browser=True,
            timeout_seconds=OAUTH_TIMEOUT_SECONDS,
        )
        try:
            credential = self.oauth.login_browser(
                options=options,
                on_auth=on_auth,
            )
        except OAuthError as error:
            msg = "oauth_failed"
            raise AgentControlError(msg, str(error)) from error
        return credential.account_id

    def auth_status(self) -> str:
        credential = self.oauth.store.load()
        if credential is None:
            return "not logged in"
        account = credential.account_id or "unreported"
        return f"logged in · account={account} · expires_at={credential.expires_at:.0f}"

    def auth_logout(self) -> None:
        self.oauth.logout()

    def model(self) -> str:
        return self.settings.model

    def reasoning(self) -> str | None:
        return self.client.reasoning_effort

    def models(self) -> tuple[ProviderModel, ...]:
        try:
            return self.client.available_models()
        except (OAuthError, ProviderError) as error:
            raise AgentControlError(error.code, error.message) from error

    def workspace(self) -> str:
        return str(self.settings.workspace)

    def set_model(self, value: str) -> str:
        model = value.strip()
        if not model:
            msg = "Model name is required"
            code = "model_empty"
            raise AgentControlError(code, msg)
        self.client.model = model
        object.__setattr__(self.session, "client", self.client)
        self.settings = replace(self.settings, model=model)
        return model

    def set_reasoning(self, value: str | None) -> str | None:
        effort = value.strip() if value is not None else None
        self.client.reasoning_effort = effort or None
        self.settings = replace(self.settings, reasoning_effort=self.client.reasoning_effort)
        return self.client.reasoning_effort

    def set_workspace(self, value: str) -> str:
        raw_path = value.strip()
        if not raw_path:
            msg = "Workspace path is required"
            code = "workspace_empty"
            raise AgentControlError(code, msg)
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = self.settings.workspace / candidate
        workspace = candidate.resolve()
        if not workspace.is_dir():
            msg = f"Workspace directory does not exist: {workspace}"
            code = "workspace_invalid"
            raise AgentControlError(code, msg)
        self.session.context.workspace = workspace
        self.settings = replace(self.settings, workspace=workspace)
        return str(workspace)
