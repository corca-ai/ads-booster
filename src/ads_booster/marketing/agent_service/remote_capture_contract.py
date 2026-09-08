"""Frozen canonical remote capture envelopes; native digest alone is not worker authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - Pydantic datetime field schema.
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ads_booster.capture.appium_endpoint import validate_appium_server_url
from ads_booster.capture.capture_safety import CaptureAdapterError
from ads_booster.capture.codex_appium_job import CodexAppiumJobContract
from ads_booster.contracts.agent_run import BoundedId, ToolApproval, ToolInvocation, contract_sha256
from ads_booster.contracts.creative_work import CreativeAsset
from ads_booster.contracts.models import (
    CaptureProvenance,
    ContractModel,
    DeviceKind,
    DeviceTarget,
    Sha256Digest,
)
from ads_booster.marketing.agent_service.creative_capture_contract import (
    CreativeCaptureInput,
    build_creative_capture_contract,
)


@dataclass(frozen=True, slots=True)
class _RunIdentity:
    tenant_id: str
    run_id: str


class RemoteCaptureProfile(ContractModel):
    worker_id: BoundedId
    tenant_id: BoundedId
    device: DeviceTarget
    python_executable: Annotated[str, Field(min_length=1, max_length=4096)]
    appium_server: str
    timeout_seconds: Annotated[int, Field(ge=30, le=3600)] = 300

    @model_validator(mode="after")
    def valid_worker(self) -> Self:
        path = PurePosixPath(self.python_executable)
        if (
            self.device.kind is not DeviceKind.SIMULATOR
            or not path.is_absolute()
            or ".." in path.parts
            or "\x00" in self.python_executable
        ):
            raise ValueError("remote_capture_profile_invalid")
        try:
            _ = validate_appium_server_url(self.appium_server)
        except CaptureAdapterError as error:
            raise ValueError("remote_capture_profile_invalid") from error
        return self


class RemoteCaptureJob(ContractModel):
    schema_version: Literal["trace.remote-capture-job.v1"] = "trace.remote-capture-job.v1"
    operation_id: BoundedId
    invocation: ToolInvocation
    approval: ToolApproval
    profile: RemoteCaptureProfile
    source: CreativeAsset
    contract: CodexAppiumJobContract

    @model_validator(mode="after")
    def valid_job(self) -> Self:
        digest = contract_sha256(self.invocation)
        if (
            self.operation_id != "capture-" + digest[:48]
            or self.invocation.tenant_id is None
            or self.invocation.tenant_id != self.profile.tenant_id
            or self.profile.tenant_id.startswith("slack-private-")
            or self.approval.decision != "granted"
            or self.approval.invocation_sha256 != digest
            or self.source.scope.workspace_id != self.profile.tenant_id
            or self.source.kind != "background_asset"
            or self.source.scope.product_id != "trace"
            or self.source.scope.member_id is not None
            or self.source.scope.session_id is not None
            or contract_sha256(self.invocation.input) != self.invocation.input_sha256
        ):
            raise ValueError("remote_capture_job_binding_invalid")
        expected = build_creative_capture_contract(
            run=_RunIdentity(self.profile.tenant_id, self.invocation.run_id),
            request=CreativeCaptureInput.model_validate(self.invocation.input),
            source=self.source,
            key=digest,
            background_relative=self.contract.prepared_background.path,
            python_executable=self.profile.python_executable,
            device=self.profile.device,
            appium_server=self.profile.appium_server,
            export_nonce=self.contract.export_nonce,
        )
        if expected != self.contract:
            raise ValueError("remote_capture_job_contract_mismatch")
        return self


class RemoteCaptureUpload(ContractModel):
    schema_version: Literal["trace.remote-capture-upload.v1"] = "trace.remote-capture-upload.v1"
    job_sha256: Sha256Digest
    lease_id: BoundedId
    disposition: Literal["succeeded", "failed", "no_effect"]
    provenance: CaptureProvenance | None = None
    png_base64: Annotated[str, Field(min_length=1, max_length=14 * 1024 * 1024)] | None = None
    error_code: (
        Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[a-z][a-z0-9_]*$")] | None
    ) = None

    @model_validator(mode="after")
    def valid_upload(self) -> Self:
        if self.disposition == "succeeded":
            if self.provenance is None or self.png_base64 is None or self.error_code is not None:
                raise ValueError("remote_capture_success_payload_required")
        elif self.provenance is not None or self.png_base64 is not None or self.error_code is None:
            raise ValueError("remote_capture_failure_payload_invalid")
        return self


class RemoteCaptureLease(ContractModel):
    job: RemoteCaptureJob
    lease_id: str
    expires_at: datetime

    @property
    def job_sha256(self) -> str:
        return contract_sha256(self.job)
