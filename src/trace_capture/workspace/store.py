from __future__ import annotations

from typing import TYPE_CHECKING, final

from trace_capture.workspace.asset_store import AssetStore
from trace_capture.workspace.context_store import ContextStore
from trace_capture.workspace.database import (
    WorkspaceDatabase,
    WorkspaceRepositoryBase,
    default_agent_home,
)
from trace_capture.workspace.identity_store import IdentityStore
from trace_capture.workspace.private_session_store import PrivateSessionStore

if TYPE_CHECKING:
    from pathlib import Path


@final
class SqliteWorkspaceStore(IdentityStore, ContextStore, AssetStore, PrivateSessionStore):
    def __init__(self, home: Path | None = None) -> None:
        """Open or create workspace storage under TRACE_AGENT_HOME by default."""
        database = WorkspaceDatabase(default_agent_home() if home is None else home)
        WorkspaceRepositoryBase.__init__(self, _database=database)

    @property
    def database_path(self) -> Path:
        return self._database.path
