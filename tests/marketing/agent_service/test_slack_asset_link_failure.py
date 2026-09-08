"""An unsuccessful import cannot expose a different existing asset through its Run."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.creative_work import CreativeScope
from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.agent.service.application import MarketingAgentService
from ads_booster.channels.http.http_api import MarketingAgentApi
from ads_booster.agent.runtime import SqliteSessionStore

from .creative_fixtures import NOW
from .test_slack_asset_intake import approve, setup
from .test_slack_asset_intake_flow import IntakeReasoning

if TYPE_CHECKING:
    from pathlib import Path


def test_conflicting_import_does_not_link_preexisting_asset(tmp_path: Path) -> None:
    tool, _, invocation, descriptor = setup(tmp_path)
    scope = CreativeScope(workspace_id="tenant-a", product_id="trace")
    previous = tool.assets.get(scope, "background")
    assert previous is not None
    tool.assets.add(previous.model_copy(update={"asset_id": "imported"}), actor_scope=scope)
    _ = approve(tool, invocation)
    with pytest.raises(ValueError, match="creative_revision_conflict"):
        _ = tool.import_asset(invocation, descriptor)
    service = MarketingAgentService(
        repository=tool.repository,
        registry=ToolRegistry(()),
        reasoning=IntakeReasoning(),
        tools={},
        runtime_store=SqliteSessionStore(tool.repository.database_path),
    )
    api = MarketingAgentApi(service, "tenant-a", "reviewer", "synthetic-local-token")
    response = api.dispatch(
        "GET",
        "/v1/runs/run-a/assets/imported",
        authorization="Bearer synthetic-local-token",
        now=NOW,
    )
    assert response.status == 404
