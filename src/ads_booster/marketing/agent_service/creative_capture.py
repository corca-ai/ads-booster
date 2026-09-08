"""Approved small native captures reuse the existing Mac worker and export verification."""

from __future__ import annotations

import secrets
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from pydantic import TypeAdapter

from ads_booster.capture.appium_endpoint import validate_appium_server_url
from ads_booster.capture.capture_safety import CaptureControl
from ads_booster.capture.codex_appium_job import CodexAppiumJobContract
from ads_booster.contracts.agent_run import AgentRun, ToolInvocation, contract_sha256
from ads_booster.contracts.creative_work import AssetParent, CreativeAsset, CreativeScope
from ads_booster.contracts.models import CaptureProvenance, DeviceKind, DeviceTarget
from ads_booster.contracts.tool_capability import ToolDescriptor
from ads_booster.marketing.agent_service.creative_asset_links import link_asset
from ads_booster.marketing.agent_service.creative_assets import SqliteCreativeAssetRepository
from ads_booster.marketing.agent_service.creative_capture_contract import (
    CreativeCaptureInput as CreativeCaptureInput,  # noqa: PLC0414 - public compatibility re-export.
)
from ads_booster.marketing.agent_service.creative_capture_contract import (
    CreativeCaptureResult as CreativeCaptureResult,  # noqa: PLC0414 - public compatibility re-export.
)
from ads_booster.marketing.agent_service.creative_capture_contract import (
    build_creative_capture_contract,
    validate_native_capture_result,
)
from ads_booster.marketing.agent_service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.marketing.tool_adapters.compatibility import DelegatedToolResult
from ads_booster.marketing.tool_adapters.descriptors import appium_descriptor
from ads_booster.providers.codex_cli import read_review_images
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from pathlib import Path

_MAX_TIMEOUT = 3600
_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class CaptureWorker(Protocol):
    def ensure_ready(self, contract: CodexAppiumJobContract, control: CaptureControl) -> None: ...
    def execute(
        self,
        contract: CodexAppiumJobContract,
        *,
        job_root: Path,
        background: Path,
        output: Path,
        control: CaptureControl,
    ) -> CaptureProvenance: ...


@dataclass(frozen=True, slots=True)
class CreativeCaptureTool:
    repository: SqliteAgentRunRepository
    assets: SqliteCreativeAssetRepository
    job_root: Path
    worker: CaptureWorker
    device: DeviceTarget
    appium_server: str
    scope_for_run: Callable[[AgentRun], CreativeScope]
    timeout_seconds: float = 300

    def __post_init__(self) -> None:
        """Validate trusted worker configuration without starting the device."""
        _ = validate_appium_server_url(self.appium_server)
        if (
            self.device.kind is not DeviceKind.SIMULATOR
            or not 0 < self.timeout_seconds <= _MAX_TIMEOUT
        ):
            raise ValueError("capture_worker_config_invalid")
        if self.assets.database != self.repository.database_path:
            raise ValueError("capture_database_mismatch")
        if not self.job_root.resolve().is_relative_to(self.assets.artifact_root):
            raise ValueError("capture_job_root_outside_artifacts")

    def execute(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> DelegatedToolResult:
        if (
            descriptor.capability_id != "capture.appium"
            or invocation.tenant_id is None
            or not descriptor.enabled
            or not descriptor.readiness.ready
        ):
            raise ValueError("capture_invocation_context_required")
        run = self.repository.get(invocation.tenant_id, invocation.run_id)
        if run is None:
            raise ValueError("capture_run_not_found")
        scope = self.scope_for_run(run)
        if scope.workspace_id != run.tenant_id:
            raise ValueError("capture_scope_mismatch")
        request = CreativeCaptureInput.model_validate(invocation.input)
        source = self._source(scope, request.background)
        key = contract_sha256(
            {
                "tenant_id": run.tenant_id,
                "run_id": run.run_id,
                "invocation_id": invocation.invocation_id,
            }
        )
        cached = self._claim(key, contract_sha256(invocation))
        if cached is not None:
            result = CreativeCaptureResult.model_validate_json(cached)
            current = self.assets.get(scope, result.asset.asset_id)
            if (
                current is None
                or current != result.asset
                or self.assets.is_stale(scope, current.asset_id)
            ):
                raise ValueError("capture_cached_asset_stale")
            return self._result(result, descriptor)
        # A committed claim precedes all worker preparation; any interruption stays uncertain.
        result = self._capture(run, scope, request, source, key)
        self.assets.add(result.asset, actor_scope=scope)
        link_asset(
            self.repository.database_path,
            tenant_id=run.tenant_id,
            run_id=run.run_id,
            asset_id=result.asset.asset_id,
            revision=result.asset.revision,
            request_sha256=contract_sha256(invocation),
            actor_id="local-mac-codex-appium",
        )
        with closing(sqlite3.connect(self.repository.database_path)) as db, db:
            _ = db.execute(
                "UPDATE creative_capture_invocations SET output=? WHERE key=?",
                (result.model_dump_json(), key),
            )
        return self._result(result, descriptor)

    def _source(self, scope: CreativeScope, parent: AssetParent) -> CreativeAsset:
        source = self.assets.get(scope, parent.asset_id)
        if source is None or source.revision != parent.revision or source.sha256 != parent.sha256:
            raise ValueError("capture_source_not_current")
        if source.kind != "background_asset" or self.assets.is_stale(scope, source.asset_id):
            raise ValueError("capture_source_not_background_or_stale")
        return source

    def _claim(self, key: str, digest: str) -> str | None:
        with closing(sqlite3.connect(self.repository.database_path, timeout=1)) as db, db:
            _ = db.execute(
                """CREATE TABLE IF NOT EXISTS creative_capture_invocations
                (key TEXT PRIMARY KEY, digest TEXT NOT NULL, output TEXT NOT NULL)"""
            )
            _ = db.execute("BEGIN IMMEDIATE")
            row = cast(
                "tuple[str, str] | None",
                db.execute(
                    "SELECT digest,output FROM creative_capture_invocations WHERE key=?", (key,)
                ).fetchone(),
            )
            if row is not None:
                if row[0] != digest:
                    raise ValueError("capture_invocation_conflict")
                if not row[1]:
                    raise ValueError("capture_reconciliation_required")
                return row[1]
            _ = db.execute(
                "INSERT INTO creative_capture_invocations VALUES (?,?,?)", (key, digest, "")
            )
        return None

    def _capture(
        self,
        run: AgentRun,
        scope: CreativeScope,
        request: CreativeCaptureInput,
        source: CreativeAsset,
        key: str,
    ) -> CreativeCaptureResult:
        job = self.job_root.resolve() / key
        job.mkdir(mode=0o700, parents=True, exist_ok=False)
        (job / "inputs").mkdir(mode=0o700)
        (job / "outputs").mkdir(mode=0o700)
        source_path = (self.assets.artifact_root / source.relative_path).resolve(strict=True)
        if not source_path.is_relative_to(self.assets.artifact_root):
            raise ValueError("capture_source_outside_root")
        original = read_review_images((source_path,))[0]
        if original.sha256 != source.sha256:
            raise ValueError("capture_source_bytes_changed")
        background_relative = (
            "inputs/background.png" if original.format == "PNG" else "inputs/background.jpg"
        )
        background = job / background_relative
        _ = background.write_bytes(original.data)
        background.chmod(0o600)
        contract = build_creative_capture_contract(
            run=run,
            request=request,
            source=source,
            key=key,
            background_relative=background_relative,
            python_executable=sys.executable,
            device=self.device,
            appium_server=self.appium_server,
            export_nonce=secrets.token_hex(32),
        )
        output = job / "outputs/trace_wallpaper.png"
        control = CaptureControl.start(self.timeout_seconds, cancel_file=job / "cancel")
        self.worker.ensure_ready(contract, control)
        provenance = self.worker.execute(
            contract, job_root=job, background=background, output=output, control=control
        )
        image = read_review_images((output,))[0]
        output.chmod(0o600)
        validate_native_capture_result(
            contract=contract,
            provenance=provenance,
            image_format=image.format,
            image_sha256=image.sha256,
            byte_size=len(image.data),
            width=image.width,
            height=image.height,
        )
        # Source can change during a device session; never register stale parentage as current.
        _ = self._source(scope, request.background)
        asset = CreativeAsset(
            asset_id=f"capture-{key[:40]}",
            revision=1,
            scope=scope,
            kind="native_trace_capture",
            relative_path=str(output.relative_to(self.assets.artifact_root)),
            sha256=image.sha256,
            parents=(request.background,),
            source=f"Native Trace export {contract.request_sha256}",
            use_terms=source.use_terms,
            data_permission=source.data_permission,
            permission_evidence=source.permission_evidence,
            origin="worker_receipt",
            receipt_sha256=contract_sha256(provenance),
            preserve=request.preserve,
            change=request.change,
            locale=contract.locale,
        )
        return CreativeCaptureResult(
            canonical_run_id=run.run_id, asset=asset, provenance=provenance
        )

    def _result(
        self, result: CreativeCaptureResult, descriptor: ToolDescriptor
    ) -> DelegatedToolResult:
        return DelegatedToolResult(
            disposition="succeeded",
            output=_JSON.validate_python(result.model_dump(mode="json")),
            actual_cost_units=descriptor.cost.worst_case_units,
        )


def creative_capture_descriptor(
    *, now: datetime, ready: bool, reason_code: str | None = None
) -> ToolDescriptor:
    template = appium_descriptor(
        installation_id="installed:canonical-capture",
        observed_at=now,
        ready=ready,
        reason_code=reason_code,
    )
    schema = _JSON.validate_python(CreativeCaptureInput.model_json_schema())
    output = _JSON.validate_python(CreativeCaptureResult.model_json_schema())
    return template.model_copy(
        update={
            "input_schema": schema,
            "input_schema_sha256": contract_sha256(schema),
            "output_schema": output,
            "output_schema_sha256": contract_sha256(output),
        }
    )
