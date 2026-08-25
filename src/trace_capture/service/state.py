from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final, final, override

from pydantic import BaseModel, ConfigDict, ValidationError

from trace_capture.workspace import MemberId, SqliteWorkspaceStore, WorkspaceId  # noqa: TC001

if TYPE_CHECKING:
    from pathlib import Path

_STATE_FILENAME: Final = "service.json"
_MAX_WORKSPACE_NAME_LENGTH: Final = 80


@dataclass(frozen=True, slots=True)
class ServiceStateError(Exception):
    path: Path
    detail: str

    @override
    def __str__(self) -> str:
        return f"service state at {self.path} is unavailable: {self.detail}"


class ServiceState(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    workspace_id: WorkspaceId
    member_id: MemberId
    host: str = "127.0.0.1"
    port: int = 8765
    tunnel: str = "none"
    public_url: str | None = None


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    state: ServiceState
    workspace_code: str | None = None
    member_code: str | None = None


@final
class ServiceStateStore:
    home: Path
    path: Path

    def __init__(self, home: Path) -> None:
        """Use one protected state file inside the configured agent home."""
        self.home = home.expanduser().resolve()
        self.path = self.home / _STATE_FILENAME

    def load(self) -> ServiceState | None:
        if not self.path.exists():
            return None
        try:
            return ServiceState.model_validate_json(self.path.read_bytes())
        except (OSError, ValidationError) as error:
            raise ServiceStateError(self.path, str(error)) from error

    def save(self, state: ServiceState) -> None:
        try:
            self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.home.chmod(0o700)
            temporary = self.path.with_suffix(".tmp")
            _ = temporary.write_text(state.model_dump_json(indent=2), encoding="utf-8")
            temporary.chmod(0o600)
            _ = temporary.replace(self.path)
        except OSError as error:
            raise ServiceStateError(self.path, str(error)) from error


def ensure_workspace(
    store: SqliteWorkspaceStore,
    state_store: ServiceStateStore,
    *,
    workspace_name: str | None = None,
) -> BootstrapResult:
    existing = state_store.load()
    if existing is not None:
        workspace = store.get_workspace(existing.workspace_id)
        if workspace_name is not None:
            normalized_name = _normalize_workspace_name(workspace_name, state_store.path)
            if workspace.name != normalized_name:
                _ = store.rename_workspace(existing.workspace_id, normalized_name)
        _ = store.get_member(existing.workspace_id, existing.member_id)
        return BootstrapResult(state=existing)
    normalized_name = _normalize_workspace_name(workspace_name, state_store.path)
    workspace = store.create_workspace(normalized_name)
    member = store.create_member(workspace.workspace.workspace_id, "Owner")
    state = ServiceState(
        workspace_id=workspace.workspace.workspace_id,
        member_id=member.member.member_id,
    )
    state_store.save(state)
    return BootstrapResult(
        state=state,
        workspace_code=workspace.access_code,
        member_code=member.invite_code,
    )


def _normalize_workspace_name(value: str | None, path: Path) -> str:
    if value is None:
        raise ServiceStateError(path, "a workspace name is required for first setup")
    normalized = value.strip()
    if not 1 <= len(normalized) <= _MAX_WORKSPACE_NAME_LENGTH:
        raise ServiceStateError(path, "workspace name must contain 1 to 80 characters")
    return normalized
