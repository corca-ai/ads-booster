"""Canonical owner for an opt-in, approved remote Mac capture queue."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import (
    AgentRecordKind,
    AgentRun,
    ToolApproval,
    ToolExecutionDeferred,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.contracts.creative_work import CreativeAsset, CreativeScope
from ads_booster.contracts.models import ContractModel, Sha256Digest
from ads_booster.contracts.tool_capability import ToolDescriptor, ToolExecutionResult, ToolReadiness
from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.creative_asset_links import link_asset
from ads_booster.marketing.agent_service.creative_assets import SqliteCreativeAssetRepository
from ads_booster.marketing.agent_service.creative_capture import creative_capture_descriptor
from ads_booster.marketing.agent_service.creative_capture_contract import (
    CreativeCaptureInput,
    CreativeCaptureResult,
    build_creative_capture_contract,
    validate_native_capture_result,
)
from ads_booster.marketing.agent_service.remote_capture_contract import (
    RemoteCaptureJob,
    RemoteCaptureProfile,
    RemoteCaptureUpload,
)
from ads_booster.marketing.agent_service.remote_capture_store import (
    RemoteCaptureLease,
    RemoteCaptureRecord,
    RemoteCaptureStore,
)
from ads_booster.marketing.runtime import ApprovalGrant
from ads_booster.providers.codex_cli import read_review_images
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Callable

    from ads_booster.marketing.agent_service.application import MarketingAgentService

_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_HEARTBEAT_SECONDS = 60
_MIN_TOKEN_CHARS = 32
_MAX_CONFIG_BYTES = 16000


class RemoteCaptureConfig(ContractModel):
    profile: RemoteCaptureProfile
    token_sha256: Sha256Digest


@dataclass(frozen=True, slots=True)
class RemoteCaptureCoordinator:
    service: MarketingAgentService
    config: RemoteCaptureConfig
    store: RemoteCaptureStore
    assets: SqliteCreativeAssetRepository
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    on_completed: Callable[[str, str, str], None] | None = None

    @property
    def profile(self) -> RemoteCaptureProfile:
        return self.config.profile

    def authenticate(self, authorization: str | None) -> bool:
        if authorization is None or not authorization.startswith("Bearer "):
            return False
        token = authorization.removeprefix("Bearer ")
        return len(token) >= _MIN_TOKEN_CHARS and hmac.compare_digest(
            hashlib.sha256(token.encode()).hexdigest(), self.config.token_sha256
        )

    def heartbeat(
        self, *, profile_sha256: str, ready: bool, reason_code: str | None, now: datetime
    ) -> None:
        if profile_sha256 != contract_sha256(self.profile):
            raise ValueError("remote_capture_profile_changed")
        # Worker strings are observations, never a system instruction or raw exception log.
        code = None if ready else "remote_capture_worker_unavailable"
        _ = reason_code
        with closing(sqlite3.connect(self.store.database)) as db, db:
            _ = db.execute(
                """CREATE TABLE IF NOT EXISTS remote_capture_readiness
                (profile_sha256 TEXT PRIMARY KEY,ready INTEGER,observed_at TEXT,reason TEXT)"""
            )
            _ = db.execute(
                """INSERT INTO remote_capture_readiness VALUES (?,?,?,?)
                ON CONFLICT(profile_sha256) DO UPDATE SET ready=excluded.ready,
                observed_at=excluded.observed_at,reason=excluded.reason""",
                (profile_sha256, int(ready), now.isoformat(), code),
            )
        # Heartbeats also repair receipts when a worker is recovering local start uncertainty.
        # Such a worker cannot claim new jobs until exact terminal readback succeeds.
        with self.service.execution_lock:
            self._recover_completed(now=now)

    def readiness(self, *, now: datetime) -> ToolReadiness:
        with closing(sqlite3.connect(self.store.database)) as db:
            exists = (
                db.execute(
                    """SELECT 1 FROM sqlite_master
                    WHERE type='table' AND name='remote_capture_readiness'"""
                ).fetchone()
                is not None
            )
            row = cast(
                "tuple[int, str] | None",
                (
                    db.execute(
                        """SELECT ready,observed_at FROM remote_capture_readiness
                        WHERE profile_sha256=?""",
                        (contract_sha256(self.profile),),
                    ).fetchone()
                    if exists
                    else None
                ),
            )
        observed = datetime.fromisoformat(row[1]) if row else now
        ready = bool(row and row[0] and 0 <= (now - observed).total_seconds() <= _HEARTBEAT_SECONDS)
        return ToolReadiness(
            ready=ready,
            reason_code=None if ready else "remote_capture_worker_unavailable",
            observed_at=observed,
            max_age_seconds=_HEARTBEAT_SECONDS,
        )

    def execute(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> ToolExecutionDeferred:
        if (
            invocation.tenant_id != self.profile.tenant_id
            or descriptor.installation_id != "canonical-remote:" + contract_sha256(self.profile)
        ):
            raise ValueError("remote_capture_invocation_scope_invalid")
        digest = contract_sha256(invocation)
        operation = "capture-" + digest[:48]
        cached = self.store.get(self.profile.tenant_id, operation)
        if cached is None:
            run = self.service.repository.get(self.profile.tenant_id, invocation.run_id)
            if run is None:
                raise ValueError("remote_capture_run_missing")
            request = CreativeCaptureInput.model_validate(invocation.input)
            scope = CreativeScope(workspace_id=run.tenant_id, product_id="trace")
            source = self.assets.get(scope, request.background.asset_id)
            if source is None or self.assets.is_stale(scope, source.asset_id):
                raise ValueError("remote_capture_source_unavailable")
            approval = self._admitted_approval(invocation)
            image = read_review_images((self.assets.artifact_root / source.relative_path,))[0]
            if image.sha256 != source.sha256:
                raise ValueError("remote_capture_source_changed")
            job = RemoteCaptureJob(
                operation_id=operation,
                invocation=invocation,
                approval=approval,
                profile=self.profile,
                source=source,
                contract=build_creative_capture_contract(
                    run=run,
                    request=request,
                    source=source,
                    key=digest,
                    background_relative="inputs/background.png"
                    if image.format == "PNG"
                    else "inputs/background.jpg",
                    python_executable=self.profile.python_executable,
                    device=self.profile.device,
                    appium_server=self.profile.appium_server,
                    export_nonce=secrets.token_hex(32),
                ),
            )
            cached = self.store.enqueue(job, now=self.clock())
        if cached.job.invocation != invocation or cached.job.profile != self.profile:
            raise ValueError("remote_capture_invocation_conflict")
        return ToolExecutionDeferred(
            schema_version="trace.tool-deferred.v1",
            invocation_sha256=digest,
            operation_id=operation,
            executor_id=self.profile.worker_id,
        )

    def _admitted_approval(self, invocation: ToolInvocation) -> ToolApproval:
        session = self.service.runtime_store.load(invocation.run_id)
        if (
            session is None
            or not session.execution_started
            or session.pending_call is None
            or session.pending_call.call_id != invocation.invocation_id
        ):
            raise ValueError("remote_capture_admission_missing")
        for record in self.service.repository.records(self.profile.tenant_id, invocation.run_id):
            if record.kind is not AgentRecordKind.APPROVAL:
                continue
            approval = ToolApproval.model_validate(record.payload)
            if (
                approval.invocation_sha256 == contract_sha256(invocation)
                and approval.decision == "granted"
                and approval.expires_at is not None
            ):
                grant = ApprovalGrant(
                    approval.approval_id,
                    session.pending_call.digest,
                    approval.approver_id,
                    approval.expires_at,
                )
                if grant.digest == session.pending_grant_sha256:
                    return approval
        raise ValueError("remote_capture_admission_missing")

    def claim(self, *, now: datetime) -> RemoteCaptureLease | None:
        with self.service.execution_lock:
            self._recover_completed(now=now)
            if not self.readiness(now=now).ready:
                return None
            for record in self.store.list_for_worker(
                self.profile.tenant_id, self.profile.worker_id, states=("queued",), limit=32
            ):
                job = record.job
                if job.profile != self.profile:
                    continue
                acknowledged = any(
                    item.payload_schema_version == "trace.tool-deferred.v1"
                    and item.payload.get("operation_id") == job.operation_id
                    and item.payload.get("invocation_sha256") == contract_sha256(job.invocation)
                    and item.payload.get("executor_id") == self.profile.worker_id
                    for item in self.service.repository.records(
                        self.profile.tenant_id, job.invocation.run_id
                    )
                )
                if acknowledged:
                    _ = self.store.arm(
                        self.profile.tenant_id,
                        job.operation_id,
                        job_sha256=record.job_sha256,
                        now=now,
                    )
            return self.store.claim(self.profile.tenant_id, self.profile.worker_id, now=now)

    def _recover_completed(self, *, now: datetime) -> None:
        for completed in self.store.list_for_worker(
            self.profile.tenant_id,
            self.profile.worker_id,
            states=("completed",),
            unsettled_only=True,
            limit=32,
        ):
            _ = self._finish(completed, now=now)

    def _record(
        self, operation_id: str, lease_id: str, job_sha256: str | None = None
    ) -> RemoteCaptureRecord:
        record = self.store.get(self.profile.tenant_id, operation_id)
        if (
            record is None
            or record.job.profile != self.profile
            or record.lease_id != lease_id
            or (job_sha256 is not None and record.job_sha256 != job_sha256)
        ):
            raise ValueError("remote_capture_lease_invalid")
        return record

    def status(self, operation_id: str, *, lease_id: str, now: datetime) -> JsonObject:
        _ = now
        record = self._record(operation_id, lease_id)
        return {
            "operation_id": operation_id,
            "lease_id": lease_id,
            "job_sha256": record.job_sha256,
            "state": record.state,
            "canonical_settled": record.canonical_settled,
        }

    def source(self, operation_id: str, *, lease_id: str, now: datetime) -> JsonObject:
        record = self._record(operation_id, lease_id)
        if (
            record.state != "leased"
            or record.lease_expires_at is None
            or record.lease_expires_at <= now
        ):
            raise ValueError("remote_capture_lease_invalid")
        self._current_source(record.job)
        image = read_review_images((self.assets.artifact_root / record.job.source.relative_path,))[
            0
        ]
        if image.sha256 != record.job.source.sha256 or len(image.data) > _MAX_IMAGE_BYTES:
            raise ValueError("remote_capture_source_changed")
        return {
            "sha256": image.sha256,
            "image_base64": base64.b64encode(image.data).decode("ascii"),
        }

    def _current_source(self, job: RemoteCaptureJob) -> None:
        try:
            current = self.assets.get(job.source.scope, job.source.asset_id)
            stale = self.assets.is_stale(job.source.scope, job.source.asset_id)
        except (OSError, ValueError) as error:
            # Normalize only the frozen source read. Output/nonce/lease errors remain rejection.
            raise ValueError("remote_capture_source_changed") from error
        if current != job.source or stale:
            raise ValueError("remote_capture_source_changed")

    def start(
        self, operation_id: str, *, lease_id: str, job_sha256: str, now: datetime
    ) -> JsonObject:
        with self.service.execution_lock:
            record = self._record(operation_id, lease_id, job_sha256)
            if record.state != "leased":
                raise ValueError("remote_capture_start_already_attempted")
            reason = self._prestart_reason(record.job, now=now)
            if record.lease_expires_at is None or record.lease_expires_at <= now:
                reason = "remote_capture_lease_expired"
            if reason is not None:
                upload = RemoteCaptureUpload(
                    job_sha256=job_sha256,
                    lease_id=lease_id,
                    disposition="no_effect",
                    error_code=reason,
                )
                _ = self.complete(operation_id, upload=upload, now=now)
                return {"started": False, "accepted": True}
            # Changed human constraints invalidate an unstarted job. They remain in the Run.
            after_invocation = False
            for item in self.service.repository.records(
                self.profile.tenant_id, record.job.invocation.run_id
            ):
                if item.kind is AgentRecordKind.INVOCATION:
                    after_invocation = item.payload_sha256 == contract_sha256(record.job.invocation)
                elif after_invocation and item.payload.get("deferred_input") is True:
                    upload = RemoteCaptureUpload(
                        job_sha256=job_sha256,
                        lease_id=lease_id,
                        disposition="no_effect",
                        error_code="capture_request_changed",
                    )
                    _ = self.complete(operation_id, upload=upload, now=now)
                    return {"started": False, "accepted": True}
            _ = self.store.start(
                self.profile.tenant_id,
                operation_id,
                lease_id=lease_id,
                job_sha256=job_sha256,
                now=now,
            )
            return {"started": True}

    def _prestart_reason(self, job: RemoteCaptureJob, *, now: datetime) -> str | None:
        if not self.service.knowledge_is_current(self.profile.tenant_id, job.invocation.run_id):
            return "remote_capture_knowledge_changed"
        if job.approval.expires_at is None or job.approval.expires_at <= now:
            return "remote_capture_approval_expired"
        try:
            self._current_source(job)
        except ValueError:
            return "remote_capture_source_changed"
        if not self.readiness(now=now).ready:
            return "remote_capture_worker_unavailable"
        return None

    def uncertain(
        self, operation_id: str, *, lease_id: str, job_sha256: str, now: datetime
    ) -> AgentRun:
        with self.service.execution_lock:
            record = self._record(operation_id, lease_id, job_sha256)
            _ = self.store.mark_uncertain(
                self.profile.tenant_id,
                operation_id,
                lease_id=lease_id,
                job_sha256=job_sha256,
                now=now,
            )
            return self.service.mark_deferred_uncertain(
                self.profile.tenant_id,
                record.job.invocation.run_id,
                operation_id=operation_id,
                now=now,
            )

    def complete(
        self, operation_id: str, *, upload: RemoteCaptureUpload, now: datetime
    ) -> AgentRun:
        with self.service.execution_lock:
            record = self._record(operation_id, upload.lease_id, upload.job_sha256)
            submission = contract_sha256(upload)
            if record.result is not None:
                if record.submission_sha256 != submission:
                    raise ValueError("remote_capture_completion_conflict")
                result = record.result
            else:
                # Validate dispatch state before any result bytes or asset links are written.
                if upload.disposition == "no_effect":
                    # Expiration forbids starting, not cleanup of the still-owned unstarted job.
                    # _record already fenced the exact lease against reassignment.
                    allowed = record.state == "leased"
                else:
                    allowed = record.state in {"started", "uncertain"}
                if not allowed:
                    raise ValueError("remote_capture_completion_not_started")
                output: JsonObject
                disposition = upload.disposition
                if upload.disposition == "succeeded":
                    if upload.provenance is None:
                        raise ValueError("remote_capture_success_payload_required")
                    try:
                        output = self._capture_result(record.job, upload)
                    except ValueError as error:
                        if str(error) not in {
                            "remote_capture_source_changed",
                            "creative_parent_not_current",
                        }:
                            raise
                        disposition = "failed"
                        output = {
                            "status": "failed",
                            "reason_code": "remote_capture_source_changed",
                            "human_action": "원본이 바뀌었습니다. 새 원본으로 제작해 주세요.",
                            "source_sha256": record.job.source.sha256,
                            "native_provenance_sha256": contract_sha256(upload.provenance),
                        }
                else:
                    output = {
                        "status": upload.disposition,
                        "reason_code": upload.error_code,
                        "human_action": "원본과 실행 기록을 확인하고 다음 작업을 정해 주세요.",
                    }
                result = ToolExecutionResult(
                    schema_version="trace.tool-execution-result.v1",
                    invocation_sha256=contract_sha256(record.job.invocation),
                    executor_id=self.profile.worker_id,
                    disposition=disposition,
                    output=output,
                    actual_cost_units=0 if upload.disposition == "no_effect" else 20,
                )
                record = self.store.complete(
                    self.profile.tenant_id,
                    operation_id,
                    lease_id=upload.lease_id,
                    job_sha256=upload.job_sha256,
                    result=result,
                    submission_sha256=submission,
                    now=now,
                )
            return self._finish(record, now=now)

    def _finish(self, record: RemoteCaptureRecord, *, now: datetime) -> AgentRun:
        if record.result is None:
            raise ValueError("remote_capture_completion_missing")
        run = self.service.complete_deferred(
            self.profile.tenant_id,
            record.job.invocation.run_id,
            operation_id=record.job.operation_id,
            result=record.result,
            now=now,
        )
        if self.on_completed is not None:
            self.on_completed(
                self.profile.tenant_id,
                record.job.invocation.run_id,
                record.job.operation_id + ":" + record.job_sha256,
            )
        _ = self.store.acknowledge_completion(
            self.profile.tenant_id,
            record.job.operation_id,
            job_sha256=record.job_sha256,
            result_sha256=contract_sha256(record.result),
            now=now,
        )
        return run

    def _capture_result(self, job: RemoteCaptureJob, upload: RemoteCaptureUpload) -> JsonObject:
        if upload.provenance is None or upload.png_base64 is None:
            raise ValueError("remote_capture_success_payload_required")
        data = base64.b64decode(upload.png_base64, validate=True)
        if len(data) > _MAX_IMAGE_BYTES:
            raise ValueError("remote_capture_image_too_large")
        root = (self.assets.artifact_root / "remote-capture" / job.operation_id).resolve()
        if not root.is_relative_to(self.assets.artifact_root):
            raise ValueError("remote_capture_artifact_outside_root")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        output = root / "trace.png"
        with tempfile.NamedTemporaryFile(dir=root, suffix=".png", delete=False) as stream:
            temporary = Path(stream.name)
            _ = stream.write(data)
        try:
            image = read_review_images((temporary,))[0]
            validate_native_capture_result(
                contract=job.contract,
                provenance=upload.provenance,
                image_format=image.format,
                image_sha256=image.sha256,
                byte_size=len(image.data),
                width=image.width,
                height=image.height,
            )
            self._current_source(job)
            if output.exists():
                if read_review_images((output,))[0].sha256 != image.sha256:
                    raise ValueError("remote_capture_artifact_conflict")
            else:
                _ = temporary.replace(output)
            request = CreativeCaptureInput.model_validate(job.invocation.input)
            asset = CreativeAsset(
                asset_id=job.operation_id,
                revision=1,
                scope=job.source.scope,
                kind="native_trace_capture",
                relative_path=str(output.relative_to(self.assets.artifact_root)),
                sha256=image.sha256,
                parents=(request.background,),
                source="Native Trace export " + job.contract.request_sha256,
                use_terms=job.source.use_terms,
                data_permission=job.source.data_permission,
                permission_evidence=job.source.permission_evidence,
                origin="worker_receipt",
                receipt_sha256=contract_sha256(upload.provenance),
                preserve=request.preserve,
                change=request.change,
                locale=job.contract.locale,
            )
            self.assets.add(asset, actor_scope=job.source.scope)
            link_asset(
                self.store.database,
                tenant_id=self.profile.tenant_id,
                run_id=job.invocation.run_id,
                asset_id=asset.asset_id,
                revision=asset.revision,
                request_sha256=contract_sha256(job.invocation),
                actor_id=self.profile.worker_id,
            )
            return _JSON.validate_python(
                CreativeCaptureResult(
                    canonical_run_id=job.invocation.run_id,
                    asset=asset,
                    provenance=upload.provenance,
                ).model_dump(mode="json")
            )
        finally:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class RemoteCaptureCatalog:
    base: ToolRegistry
    coordinator: RemoteCaptureCoordinator

    def descriptors(self, *, now: datetime) -> tuple[ToolDescriptor, ...]:
        readiness = self.coordinator.readiness(now=now)
        descriptor = creative_capture_descriptor(
            now=now, ready=readiness.ready, reason_code=readiness.reason_code
        )
        output: JsonObject = {
            "anyOf": [
                descriptor.output_schema,
                {
                    "type": "object",
                    "required": ["status", "reason_code", "human_action"],
                    "properties": {
                        "status": {"enum": ["failed", "no_effect"]},
                        "reason_code": {"type": "string"},
                        "human_action": {"type": "string"},
                        "source_sha256": {"type": "string"},
                        "native_provenance_sha256": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            ]
        }
        # Keep nested Pydantic references rooted in this output document.
        if "$defs" in descriptor.output_schema:
            output["$defs"] = descriptor.output_schema["$defs"]
        descriptor = descriptor.model_copy(
            update={
                "readiness": readiness,
                "installation_id": "canonical-remote:" + contract_sha256(self.coordinator.profile),
                "output_schema": output,
                "output_schema_sha256": contract_sha256(output),
            }
        )
        return (*self.base.current_descriptors(now=now), descriptor)


def connect_remote_capture(
    service: MarketingAgentService,
    *,
    config_path: Path,
    now: datetime,
    on_completed: Callable[[str, str, str], None] | None = None,
) -> RemoteCaptureCoordinator:
    with config_path.open("rb") as stream:
        data = stream.read(_MAX_CONFIG_BYTES + 1)
    if len(data) > _MAX_CONFIG_BYTES:
        raise ValueError("remote_capture_configuration_too_large")
    config = RemoteCaptureConfig.model_validate_json(data)
    if "capture.appium" in service.tools:
        raise ValueError("capture_tool_already_registered")
    coordinator = RemoteCaptureCoordinator(
        service,
        config,
        RemoteCaptureStore(service.repository.database_path),
        SqliteCreativeAssetRepository(
            service.repository.database_path, service.repository.database_path.parent / "artifacts"
        ),
        on_completed=on_completed,
    )
    catalog = RemoteCaptureCatalog(service.registry, coordinator)
    service.tools = {**service.tools, "capture.appium": coordinator}
    service.registry = ToolRegistry(catalog.descriptors(now=now), provider=catalog)
    return coordinator
