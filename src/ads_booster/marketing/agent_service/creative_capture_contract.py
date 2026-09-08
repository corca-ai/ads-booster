"""Pure native capture contracts shared by local and explicitly bound remote workers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol

from pydantic import Field, model_validator

from ads_booster.capture.appium_endpoint import validate_appium_server_url
from ads_booster.capture.codex_appium_job import CodexAppiumJobContract, CodexAppiumJobIdentity
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.creative_work import AssetParent, CreativeAsset
from ads_booster.contracts.generation import (
    MarketingContextBundle,
    PersonaProfile,
    PromotionMaterial,
)
from ads_booster.contracts.models import (
    CaptureProvenance,
    ContractModel,
    DeviceTarget,
    TraceScheduleItem,
)
from ads_booster.contracts.native_export import (
    PreparedBackground,
    TraceSuppliedBackgroundProvenance,
)

_COUNTRIES = {
    "KR": ("ko-KR", "Asia/Seoul"),
    "JP": ("ja-JP", "Asia/Tokyo"),
    "US": ("en-US", "America/New_York"),
}
Text = Annotated[str, Field(min_length=1, max_length=2000)]


class CreativeCaptureInput(ContractModel):
    background: AssetParent
    country: Literal["KR", "JP", "US"]
    reference_date: datetime
    trace_items: Annotated[tuple[TraceScheduleItem, ...], Field(min_length=1, max_length=24)]
    concept: Text
    creative_direction: Text
    preserve: Annotated[tuple[Text, ...], Field(max_length=16)] = ()
    change: Annotated[tuple[Text, ...], Field(max_length=16)] = ()
    data_permission: Literal["synthetic"]

    @model_validator(mode="after")
    def utc_reference(self) -> CreativeCaptureInput:
        if self.reference_date.tzinfo is None or self.reference_date.utcoffset() != UTC.utcoffset(
            self.reference_date
        ):
            raise ValueError("capture_reference_must_be_utc")
        return self


class CreativeCaptureResult(ContractModel):
    canonical_run_id: Annotated[str, Field(min_length=1, max_length=160)]
    asset: CreativeAsset
    provenance: CaptureProvenance
    human_review_required: Literal[True] = True
    product_support_verified: Literal[False] = False


class CaptureRunIdentity(Protocol):
    @property
    def tenant_id(self) -> str: ...

    @property
    def run_id(self) -> str: ...


def build_creative_capture_contract(  # noqa: PLR0913 - explicit worker and source bindings.
    *,
    run: CaptureRunIdentity,
    request: CreativeCaptureInput,
    source: CreativeAsset,
    key: str,
    background_relative: str,
    python_executable: str,
    device: DeviceTarget,
    appium_server: str,
    export_nonce: str,
) -> CodexAppiumJobContract:
    """Build deterministically without reading host paths, generating nonces or device effects."""
    _ = validate_appium_server_url(appium_server)
    if (
        request.background
        != AssetParent(asset_id=source.asset_id, revision=source.revision, sha256=source.sha256)
        or source.scope.workspace_id != run.tenant_id
    ):
        raise ValueError("capture_contract_source_mismatch")
    request_id = f"capture-{key[:40]}"
    locale, timezone = _COUNTRIES[request.country]
    prepared = PreparedBackground(
        path=background_relative,
        sha256=source.sha256,
        provenance=TraceSuppliedBackgroundProvenance(
            schema_version="trace.supplied-background.v1",
            artifact_path=background_relative,
            artifact_sha256=source.sha256,
            asset_id=source.asset_id,
            asset_revision=source.revision,
            source=source.source,
            use_terms=source.use_terms,
            data_permission=source.data_permission,
            permission_evidence=source.permission_evidence,
        ),
    )
    context = MarketingContextBundle(
        schema_version="trace.marketing-context.v1",
        request_id=request_id,
        persona=PersonaProfile(persona_id=request_id, country=request.country, locale=locale),
        promotion_material=PromotionMaterial(
            promotion_material_id=request_id,
            concept=request.concept,
            creative_direction="\n".join(
                (
                    request.creative_direction,
                    "Preserve: " + "; ".join(request.preserve),
                    "Change only: " + "; ".join(request.change),
                )
            ),
            trace_items=request.trace_items,
        ),
        reference_date=request.reference_date,
        device=device,
    )
    return CodexAppiumJobContract(
        schema_version="trace.codex-appium-job.v2",
        identity=CodexAppiumJobIdentity(
            task_id=request_id,
            run_id="run-"
            + contract_sha256({"tenant_id": run.tenant_id, "run_id": run.run_id})[:40],
            request_id=request_id,
            idempotency_key=key,
            candidate_id=source.asset_id,
            candidate_revision=source.revision,
        ),
        context=context,
        prepared_background=prepared,
        python_executable=python_executable,
        appium_server=appium_server,
        bundle_id="com.corca.Trace",
        app_group_id="group.ai.corca.trace",
        device=device,
        locale=locale,
        time_zone=timezone,
        calendar_namespace=f"trace-{request_id}",
        todo_calendar_namespace=f"trace-{request_id}-todos",
        export_nonce=export_nonce,
    )


def validate_native_capture_result(  # noqa: PLR0913 - independently decoded bytes and metadata.
    *,
    contract: CodexAppiumJobContract,
    provenance: CaptureProvenance,
    image_format: str,
    image_sha256: str,
    byte_size: int,
    width: int,
    height: int,
) -> None:
    """Compare trusted decoded file metadata with the exact requested native export."""
    if (
        image_format != "PNG"
        or provenance.request_sha256 != contract.request_sha256
        or provenance.artifact_sha256 != image_sha256
        or provenance.byte_size != byte_size
        or (provenance.width, provenance.height) != (width, height)
        or provenance.bundle_id != contract.bundle_id
        or provenance.device_udid != contract.device.udid
        or provenance.native_export_nonce != contract.export_nonce
        or not provenance.native_export_binding_verified
    ):
        raise ValueError("capture_native_provenance_invalid")
