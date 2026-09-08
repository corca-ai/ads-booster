from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING

from PIL import Image

from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentGoal,
    AgentRun,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.contracts.creative_work import CreativeAsset, CreativeScope
from ads_booster.marketing.agent_service.creative_assets import SqliteCreativeAssetRepository
from ads_booster.marketing.agent_service.sqlite_repository import SqliteAgentRunRepository

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject

NOW = datetime(2026, 9, 7, tzinfo=UTC)


def png() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (32, 64), "white").save(stream, format="PNG")
    return stream.getvalue()


@dataclass(frozen=True, slots=True)
class CreativeFixture:
    repository: SqliteAgentRunRepository
    assets: SqliteCreativeAssetRepository
    scope: CreativeScope


def setup_assets(tmp_path: Path) -> tuple[CreativeFixture, ToolInvocation]:
    database = tmp_path / "service.db"
    repository = SqliteAgentRunRepository(database)
    assets = SqliteCreativeAssetRepository(database, tmp_path / "artifacts")
    scope = CreativeScope(workspace_id="tenant-a", product_id="trace")
    _ = (assets.artifact_root / "background.png").write_bytes(png())
    asset = CreativeAsset(
        asset_id="background",
        revision=1,
        scope=scope,
        kind="background_asset",
        relative_path="background.png",
        sha256=sha256(png()).hexdigest(),
        source="Team Figma export",
        use_terms="Team-owned synthetic example",
        data_permission="synthetic",
        permission_evidence="Synthetic fixture",
        origin="human_reported",
    )
    assets.add(asset, actor_scope=scope)
    _ = repository.create(
        AgentRun(
            schema_version="trace.agent-run.v1",
            run_id="run-a",
            tenant_id="tenant-a",
            goal=AgentGoal(objective="Review this background", success_criteria=("Readable",)),
            budget=AgentBudget(max_tool_calls=4, max_cost_units=40),
            created_at=NOW,
            updated_at=NOW,
        )
    )
    payload: JsonObject = {
        "background": {"asset_id": asset.asset_id, "revision": 1, "sha256": asset.sha256}
    }
    invocation = ToolInvocation(
        schema_version="trace.tool-invocation.v1",
        invocation_id="invoke-a",
        run_id="run-a",
        tenant_id="tenant-a",
        step_id="step-a",
        intent_sha256="a" * 64,
        capability_snapshot_sha256="b" * 64,
        descriptor_sha256="c" * 64,
        idempotency_key="key",
        input=payload,
        input_sha256=contract_sha256(payload),
    )
    return CreativeFixture(repository, assets, scope), invocation
