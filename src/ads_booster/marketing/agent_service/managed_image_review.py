"""Review same-Run managed assets without uploading them back to Slack."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Literal, Self, cast

from pydantic import Field, TypeAdapter, model_validator

from ads_booster.contracts.agent_run import ToolInvocation, contract_sha256
from ads_booster.contracts.creative_work import AssetParent, CreativeScope
from ads_booster.contracts.marketing_delivery import ReviewAsset
from ads_booster.contracts.models import ContractModel
from ads_booster.contracts.tool_capability import ToolDescriptor
from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.creative_asset_links import asset_links
from ads_booster.marketing.agent_service.creative_asset_verifier import CreativeAssetVerifier
from ads_booster.marketing.agent_service.creative_assets import SqliteCreativeAssetRepository
from ads_booster.marketing.agent_service.image_review import review_images
from ads_booster.marketing.agent_service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.marketing.tool_adapters.compatibility import DelegatedToolResult
from ads_booster.marketing.tool_adapters.descriptors import research_descriptor
from ads_booster.providers.codex_cli import CodexCli
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_CAPABILITY = "creative.asset.review"
_COST = 4


class ManagedImageReviewInput(ContractModel):
    assets: Annotated[tuple[AssetParent, ...], Field(min_length=1, max_length=4)]
    request: Annotated[str, Field(min_length=1, max_length=20000)]

    @model_validator(mode="after")
    def distinct_sources(self) -> Self:
        if len({a.asset_id for a in self.assets}) != len(self.assets) or not self.request.strip():
            raise ValueError("managed_image_review_input_invalid")
        return self


class ManagedImageReviewResult(ContractModel):
    sources: tuple[AssetParent, ...]
    review: JsonObject
    final_approval: Literal[False] = False
    product_support_verified: Literal[False] = False


def managed_image_review_descriptor(*, now: datetime, ready: bool) -> ToolDescriptor:
    template = research_descriptor(
        installation_id="installed:creative.asset.review",
        observed_at=now,
        ready=ready,
        reason_code=None if ready else "managed_image_review_unavailable",
    )
    schema = _JSON.validate_python(ManagedImageReviewInput.model_json_schema())
    output = _JSON.validate_python(ManagedImageReviewResult.model_json_schema())
    return template.model_copy(
        update={
            "capability_id": _CAPABILITY,
            "owner": "ads_booster.marketing.agent_service.managed_image_review",
            "input_schema": schema,
            "input_schema_sha256": contract_sha256(schema),
            "output_schema": output,
            "output_schema_sha256": contract_sha256(output),
            "cost": template.cost.model_copy(
                update={"worst_case_units": _COST, "unit": "visual_review"}
            ),
        }
    )


@dataclass(frozen=True, slots=True)
class ManagedImageReviewTool:
    repository: SqliteAgentRunRepository
    assets: SqliteCreativeAssetRepository
    codex: CodexCli

    def __post_init__(self) -> None:
        """Keep authorization links and asset bytes under the same canonical owner."""
        if self.repository.database_path != self.assets.database:
            raise ValueError("managed_image_review_database_mismatch")

    def ready(self) -> bool:
        return bool(
            self.codex.model
            and self.codex.executable.is_file()
            and os.access(self.codex.executable, os.X_OK)
        )

    def execute(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> DelegatedToolResult:
        if (
            descriptor.capability_id != _CAPABILITY
            or not descriptor.enabled
            or not descriptor.readiness.ready
            or not self.codex.model
        ):
            raise ValueError("managed_image_review_unavailable")
        request = ManagedImageReviewInput.model_validate(invocation.input)
        paths = self._authorize(invocation, request)
        digest = contract_sha256(invocation)
        with closing(sqlite3.connect(self.repository.database_path)) as db, db:
            _ = db.execute("""CREATE TABLE IF NOT EXISTS managed_image_reviews (
                tenant TEXT NOT NULL,run TEXT NOT NULL,invocation TEXT NOT NULL,
                digest TEXT NOT NULL,output TEXT,
                PRIMARY KEY(tenant,run,invocation))""")
            _ = db.execute("BEGIN IMMEDIATE")
            prior = cast(
                "tuple[str,str | None] | None",
                db.execute(
                    """SELECT digest,output FROM managed_image_reviews
                    WHERE tenant=? AND run=? AND invocation=?""",
                    (invocation.tenant_id, invocation.run_id, invocation.invocation_id),
                ).fetchone(),
            )
            if prior is not None:
                if prior[0] != digest or prior[1] is None:
                    raise ValueError("managed_image_review_outcome_unresolved")
                output = ManagedImageReviewResult.model_validate_json(prior[1])
                return self._result(_JSON.validate_python(output.model_dump(mode="json")))
            _ = db.execute(
                "INSERT INTO managed_image_reviews VALUES(?,?,?,?,NULL)",
                (
                    invocation.tenant_id,
                    invocation.run_id,
                    invocation.invocation_id,
                    digest,
                ),
            )
        try:
            workspace = self.assets.artifact_root / "managed-reviews" / digest
            if not workspace.resolve().is_relative_to(self.assets.artifact_root):
                raise ValueError("managed_image_review_workspace_invalid")  # noqa: TRY301
            review = review_images(
                self.codex,
                images=paths,
                request=request.request,
                workspace_root=workspace,
                timeout_seconds=120,
            )
            _ = self._authorize(invocation, request)
            receipt = review.get("receipt")
            if not isinstance(receipt, dict) or receipt.get("source_sha256s") != [
                a.sha256 for a in request.assets
            ]:
                raise ValueError("managed_image_review_source_changed")  # noqa: TRY301
            result = ManagedImageReviewResult(sources=request.assets, review=review)
        except Exception:  # noqa: BLE001 - started inference remains unresolved, without secret output.
            raise ValueError("managed_image_review_failed") from None
        with closing(sqlite3.connect(self.repository.database_path)) as db, db:
            _ = db.execute(
                """UPDATE managed_image_reviews SET output=?
                WHERE tenant=? AND run=? AND invocation=?""",
                (
                    result.model_dump_json(),
                    invocation.tenant_id,
                    invocation.run_id,
                    invocation.invocation_id,
                ),
            )
        return self._result(_JSON.validate_python(result.model_dump(mode="json")))

    def _authorize(
        self,
        invocation: ToolInvocation,
        request: ManagedImageReviewInput,
    ) -> tuple[Path, ...]:
        tenant = invocation.tenant_id
        if tenant is None or tenant.startswith("slack-private-"):
            raise ValueError("managed_image_review_shared_tenant_required")
        if self.repository.get(tenant, invocation.run_id) is None:
            raise ValueError("managed_image_review_run_missing")
        with asset_links(self.repository.database_path) as db:
            for asset in request.assets:
                if (
                    db.execute(
                        """SELECT 1 FROM creative_run_assets
                    WHERE tenant_id=? AND run_id=? AND asset_id=? AND revision=?""",
                        (tenant, invocation.run_id, asset.asset_id, asset.revision),
                    ).fetchone()
                    is None
                ):
                    raise ValueError("managed_image_review_asset_not_bound")
        scope = CreativeScope(workspace_id=tenant, product_id="trace")
        CreativeAssetVerifier(self.assets).verify(
            scope, tuple(ReviewAsset.model_validate(a.model_dump()) for a in request.assets)
        )
        paths: list[Path] = []
        for ref in request.assets:
            asset = self.assets.get(scope, ref.asset_id)
            if asset is None:
                raise ValueError("managed_image_review_asset_missing")
            paths.append(self.assets.artifact_root / asset.relative_path)
        return tuple(paths)

    @staticmethod
    def _result(output: JsonObject) -> DelegatedToolResult:
        return DelegatedToolResult(disposition="no_effect", actual_cost_units=_COST, output=output)


@dataclass(frozen=True, slots=True)
class ManagedImageReviewCatalog:
    base: ToolRegistry
    tool: ManagedImageReviewTool

    def descriptors(self, *, now: datetime) -> tuple[ToolDescriptor, ...]:
        return (
            *self.base.current_descriptors(now=now),
            managed_image_review_descriptor(now=now, ready=self.tool.ready()),
        )
