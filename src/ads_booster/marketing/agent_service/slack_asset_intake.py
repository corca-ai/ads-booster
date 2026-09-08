"""Signed Slack file inspection and exactly approved human-attributed asset intake."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Annotated, Literal, cast

from pydantic import Field, TypeAdapter

from ads_booster.contracts.agent_run import (
    AgentRecordKind,
    BoundedId,
    ToolApproval,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.contracts.creative_work import AssetParent, CreativeAsset, CreativeScope
from ads_booster.contracts.models import ContractModel, Identifier, Sha256Digest
from ads_booster.contracts.tool_capability import ToolDescriptor
from ads_booster.marketing.agent_service.creative_asset_links import link_asset
from ads_booster.marketing.agent_service.creative_assets import SqliteCreativeAssetRepository
from ads_booster.marketing.agent_service.slack_image_files import SlackImageFiles
from ads_booster.marketing.agent_service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.marketing.tool_adapters.compatibility import DelegatedToolResult
from ads_booster.marketing.tool_adapters.descriptors import (
    image_generation_descriptor,
    research_descriptor,
)
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Callable

Text = Annotated[str, Field(min_length=1, max_length=2000)]
_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class InspectSlackFile(ContractModel):
    file_id: Annotated[str, Field(pattern=r"^F[A-Z0-9]{1,79}$")]


class ImportSlackAsset(InspectSlackFile):
    expected_sha256: Sha256Digest
    asset_id: Identifier
    revision: Annotated[int, Field(ge=1)] = 1
    kind: Literal["native_trace_capture", "edited_promotion", "background_asset", "phone_mockup"]
    source: Text = Field(
        description="Human-confirmed source declaration; upload alone does not establish origin."
    )
    use_terms: Text = Field(
        description="Human-declared use terms; upload does not establish usage rights."
    )
    data_permission: Literal["synthetic", "explicitly_permitted"]
    permission_evidence: Text
    preserve: Annotated[tuple[Text, ...], Field(max_length=32)] = ()
    change: Annotated[tuple[Text, ...], Field(max_length=32)] = ()
    locale: Annotated[str, Field(min_length=2, max_length=35)] | None = None
    parents: Annotated[tuple[AssetParent, ...], Field(max_length=16)] = ()


class InspectedSlackFile(ContractModel):
    file_id: str
    channel_id: str
    sha256: Sha256Digest
    byte_size: Annotated[int, Field(ge=1)]
    visual_quality_verified: Literal[False] = False
    usage_rights_verified: Literal[False] = False
    product_support_verified: Literal[False] = False


class ImportedSlackAsset(ContractModel):
    asset: CreativeAsset
    file_id: str
    approval_id: BoundedId
    approver_id: BoundedId
    approval_sha256: Sha256Digest
    invocation_sha256: Sha256Digest
    human_review_required: Literal[True] = True
    usage_rights_verified: Literal[False] = False
    product_support_verified: Literal[False] = False


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class SlackAssetIntakeTool:
    repository: SqliteAgentRunRepository
    assets: SqliteCreativeAssetRepository
    files: SlackImageFiles
    clock: Callable[[], datetime] = _now

    def __post_init__(self) -> None:
        """Bind one canonical database and immutable artifact root."""
        if (
            self.assets.database != self.repository.database_path
            or self.files.database_path != self.repository.database_path
        ):
            raise ValueError("slack_asset_database_mismatch")
        if self.files.artifact_root.resolve() != self.assets.artifact_root:
            raise ValueError("slack_asset_root_mismatch")

    def _scope(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor, capability: str
    ) -> CreativeScope:
        if (
            invocation.tenant_id != self.files.tenant_id
            or invocation.tenant_id is None
            or invocation.tenant_id.startswith("slack-private-")
        ):
            raise ValueError("slack_asset_tenant_denied")
        if (
            descriptor.capability_id != capability
            or contract_sha256(descriptor) != invocation.descriptor_sha256
            or not descriptor.enabled
            or not descriptor.readiness.ready
        ):
            raise ValueError("slack_asset_descriptor_invalid")
        if contract_sha256(invocation.input) != invocation.input_sha256:
            raise ValueError("slack_asset_input_digest_invalid")
        if self.repository.get(invocation.tenant_id, invocation.run_id) is None:
            raise ValueError("slack_asset_run_missing")
        return CreativeScope(workspace_id=invocation.tenant_id, product_id="trace")

    def inspect(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> DelegatedToolResult:
        _ = self._scope(invocation, descriptor, "creative.file.inspect")
        request = InspectSlackFile.model_validate(invocation.input)
        file = self.files.fetch(invocation.run_id, request.file_id)
        return DelegatedToolResult(
            disposition="no_effect",
            actual_cost_units=1,
            output={
                "file_id": file.file_id,
                "channel_id": file.channel_id,
                "sha256": file.sha256,
                "byte_size": file.byte_size,
                "visual_quality_verified": False,
                "usage_rights_verified": False,
                "product_support_verified": False,
            },
        )

    def _approval(self, invocation: ToolInvocation) -> ToolApproval:
        digest = contract_sha256(invocation)
        now = self.clock()
        for record in reversed(
            self.repository.records(invocation.tenant_id or "", invocation.run_id)
        ):
            if record.kind != AgentRecordKind.APPROVAL:
                continue
            approval = ToolApproval.model_validate(record.payload)
            if approval.invocation_sha256 != digest:
                continue
            if (
                approval.decision != "granted"
                or approval.expires_at is None
                or not approval.decided_at <= now < approval.expires_at
            ):
                raise ValueError("slack_asset_approval_not_current")
            return approval
        raise ValueError("slack_asset_approval_required")

    def import_asset(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> DelegatedToolResult:
        scope = self._scope(invocation, descriptor, "creative.asset.import")
        request = ImportSlackAsset.model_validate(invocation.input)
        approval = self._approval(invocation)
        file = self.files.fetch(invocation.run_id, request.file_id)
        path = file.path.resolve(strict=True)
        if not path.is_relative_to(self.assets.artifact_root):
            raise ValueError("slack_asset_file_outside_root")
        if (
            file.file_id != request.file_id
            or file.sha256 != request.expected_sha256
            or sha256(path.read_bytes()).hexdigest() != request.expected_sha256
        ):
            raise ValueError("slack_asset_approved_bytes_changed")
        asset = CreativeAsset(
            asset_id=request.asset_id,
            revision=request.revision,
            kind=request.kind,
            source=request.source,
            use_terms=request.use_terms,
            data_permission=request.data_permission,
            permission_evidence=request.permission_evidence,
            preserve=request.preserve,
            change=request.change,
            locale=request.locale,
            parents=request.parents,
            scope=scope,
            relative_path=str(path.relative_to(self.assets.artifact_root)),
            sha256=file.sha256,
            origin="human_reported",
        )
        digest = contract_sha256(invocation)
        key = contract_sha256(
            {
                "tenant": scope.workspace_id,
                "run": invocation.run_id,
                "invocation": invocation.invocation_id,
            }
        )
        with closing(sqlite3.connect(self.repository.database_path)) as db, db:
            _ = db.execute(
                """CREATE TABLE IF NOT EXISTS slack_asset_imports
                (key TEXT PRIMARY KEY, digest TEXT NOT NULL)"""
            )
            _ = db.execute("BEGIN IMMEDIATE")
            row = cast(
                "tuple[str] | None",
                db.execute("SELECT digest FROM slack_asset_imports WHERE key=?", (key,)).fetchone(),
            )
            if row is not None and row[0] != digest:
                raise ValueError("slack_asset_import_conflict")
            _ = db.execute("INSERT OR IGNORE INTO slack_asset_imports VALUES (?,?)", (key, digest))
        # Register immutable metadata before exposing the Run link. A conflicting existing
        # asset must never become visible through a failed import; replay repairs a missing link.
        self.assets.add(asset, actor_scope=scope)
        link_asset(
            self.repository.database_path,
            tenant_id=scope.workspace_id,
            run_id=invocation.run_id,
            asset_id=asset.asset_id,
            revision=asset.revision,
            request_sha256=digest,
            actor_id=approval.approver_id,
        )
        return DelegatedToolResult(
            disposition="succeeded",
            actual_cost_units=1,
            output=_JSON.validate_python(
                {
                    "asset": asset.model_dump(mode="json"),
                    "file_id": file.file_id,
                    "approval_id": approval.approval_id,
                    "approver_id": approval.approver_id,
                    "approval_sha256": contract_sha256(approval),
                    "invocation_sha256": digest,
                    "human_review_required": True,
                    "usage_rights_verified": False,
                    "product_support_verified": False,
                }
            ),
        )


def _descriptor(now: datetime, ready: bool, *, importing: bool) -> ToolDescriptor:
    template = (
        image_generation_descriptor(observed_at=now)
        if importing
        else research_descriptor(
            installation_id="installed:slack-asset-intake", observed_at=now, ready=ready
        )
    )
    schema = _JSON.validate_python(
        (ImportSlackAsset if importing else InspectSlackFile).model_json_schema()
    )
    output = _JSON.validate_python(
        (ImportedSlackAsset if importing else InspectedSlackFile).model_json_schema()
    )
    return template.model_copy(
        update={
            "capability_id": "creative.asset.import" if importing else "creative.file.inspect",
            "owner": "ads_booster.marketing.agent_service.slack_asset_intake",
            "installation_id": "installed:slack-asset-intake",
            "readiness": template.readiness.model_copy(
                update={"ready": ready, "reason_code": None if ready else "adapter_unavailable"}
            ),
            "input_schema": schema,
            "input_schema_sha256": contract_sha256(schema),
            "output_schema": output,
            "output_schema_sha256": contract_sha256(output),
            "cost": template.cost.model_copy(
                update={
                    "worst_case_units": 1,
                    "unit": "asset_registration" if importing else "file_inspection",
                }
            ),
        }
    )


def slack_file_inspect_descriptor(*, now: datetime, ready: bool) -> ToolDescriptor:
    return _descriptor(now, ready, importing=False)


def slack_asset_import_descriptor(*, now: datetime, ready: bool) -> ToolDescriptor:
    return _descriptor(now, ready, importing=True)
