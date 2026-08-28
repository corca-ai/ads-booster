from __future__ import annotations

from typing import TYPE_CHECKING, final

from ads_booster.workspace.account_store import SqliteMarketingAccountStore
from ads_booster.workspace.asset_store import AssetStore
from ads_booster.workspace.candidate_journey_store import CandidateStore
from ads_booster.workspace.context_store import ContextStore
from ads_booster.workspace.database import (
    WorkspaceDatabase,
    WorkspaceRepositoryBase,
    default_agent_home,
)
from ads_booster.workspace.identity_store import IdentityStore
from ads_booster.workspace.private_session_store import PrivateSessionStore

if TYPE_CHECKING:
    from pathlib import Path


@final
class SqliteWorkspaceStore(
    IdentityStore,
    ContextStore,
    AssetStore,
    CandidateStore,
    SqliteMarketingAccountStore,
    PrivateSessionStore,
):
    def __init__(self, home: Path | None = None) -> None:
        """Open or create workspace storage under TRACE_AGENT_HOME by default."""
        database = WorkspaceDatabase(default_agent_home() if home is None else home)
        WorkspaceRepositoryBase.__init__(self, _database=database)

    @property
    def database_path(self) -> Path:
        return self._database.path
