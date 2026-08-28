from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Final, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.capture.appium_process import (
    build_codex_appium_process_arguments,
    canonical_json_digest,
)
from ads_booster.contracts import (  # noqa: TC001
    MarketingContextBundle,
    PreparedBackground,
)
from ads_booster.contracts.models import (
    ContractModel,
    DeviceTarget,
    Identifier,
    Locale,
    Sha256Digest,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject

_COUNTRY_OPERATIONAL_CONTEXT: Final = {
    "BR": ("pt-BR", "America/Sao_Paulo"),
    "DE": ("de-DE", "Europe/Berlin"),
    "FR": ("fr-FR", "Europe/Paris"),
    "JP": ("ja-JP", "Asia/Tokyo"),
    "KR": ("ko-KR", "Asia/Seoul"),
    "TW": ("zh-TW", "Asia/Taipei"),
    "US": ("en-US", "America/New_York"),
}
_EXPORT_FILES: Final = (
    "trace_wallpaper.png",
    "trace_wallpaper.manifest.json",
    "trace_wallpaper.error.json",
)
_UNKNOWN_IANA_TIME_ZONE = "unknown_iana_time_zone"
_UNKNOWN_IANA_TIME_ZONE_MESSAGE = "time_zone must be a known IANA time zone"
_JOB_CONTEXT_MISMATCH = "codex_appium_job_context_mismatch"
_JOB_CONTEXT_MISMATCH_MESSAGE = "job identity, context, device, locale, and time zone must agree"
_CALENDAR_NAMESPACE_MISMATCH = "codex_appium_job_calendar_namespace_mismatch"
_CALENDAR_NAMESPACE_MISMATCH_MESSAGE = "calendar namespace must be owned by the request identity"
_REQUEST_DIGEST_MISMATCH = "codex_appium_job_digest_mismatch"
_REQUEST_DIGEST_MISMATCH_MESSAGE = "request_sha256 must match the canonical v2 job payload"
_LAUNCH_ARGUMENTS_MISMATCH = "codex_appium_job_launch_arguments_mismatch"
_LAUNCH_ARGUMENTS_MISMATCH_MESSAGE = (
    "launch arguments must bind the canonical request digest and export nonce"
)
_EXPORT_FILES_MISMATCH = "codex_appium_job_export_files_mismatch"
_EXPORT_FILES_MISMATCH_MESSAGE = "job export files must be the native Trace export filenames"


def _require_iana_time_zone(value: str) -> str:
    try:
        _ = ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise PydanticCustomError(
            _UNKNOWN_IANA_TIME_ZONE,
            _UNKNOWN_IANA_TIME_ZONE_MESSAGE,
        ) from error
    return value


IanaTimeZone = Annotated[
    str,
    Field(min_length=1, max_length=128),
    AfterValidator(_require_iana_time_zone),
]


class CodexAppiumJobIdentity(ContractModel):
    task_id: Identifier
    run_id: Identifier
    request_id: Identifier
    idempotency_key: Annotated[str, Field(min_length=1, max_length=256)]
    candidate_id: Identifier
    candidate_revision: Annotated[int, Field(ge=1)]


class CodexAppiumJobContract(ContractModel):
    schema_version: Literal["trace.codex-appium-job.v2"]
    identity: CodexAppiumJobIdentity
    context: MarketingContextBundle
    prepared_background: PreparedBackground
    python_executable: str
    appium_server: str
    bundle_id: Literal["com.corca.Trace"]
    app_group_id: Literal["group.ai.corca.trace"]
    device: DeviceTarget
    locale: Locale
    time_zone: IanaTimeZone
    calendar_namespace: Identifier
    export_nonce: Sha256Digest
    request_sha256: Sha256Digest = ""
    launch_arguments: tuple[str, ...] = ()
    export_files: tuple[str, str, str] = _EXPORT_FILES

    @model_validator(mode="after")
    def require_operational_bindings(self) -> CodexAppiumJobContract:
        expected_locale, expected_time_zone = _COUNTRY_OPERATIONAL_CONTEXT.get(
            self.context.persona.country,
            ("", ""),
        )
        if (
            self.context.request_id != self.identity.request_id
            or self.context.device != self.device
            or self.context.persona.locale != self.locale
            or (self.locale, self.time_zone) != (expected_locale, expected_time_zone)
        ):
            raise PydanticCustomError(
                _JOB_CONTEXT_MISMATCH,
                _JOB_CONTEXT_MISMATCH_MESSAGE,
            )
        if not self.calendar_namespace.startswith(f"trace-{self.identity.request_id}"):
            raise PydanticCustomError(
                _CALENDAR_NAMESPACE_MISMATCH,
                _CALENDAR_NAMESPACE_MISMATCH_MESSAGE,
            )
        expected_digest = canonical_json_digest(self._digest_payload())
        if self.request_sha256 and self.request_sha256 != expected_digest:
            raise PydanticCustomError(
                _REQUEST_DIGEST_MISMATCH,
                _REQUEST_DIGEST_MISMATCH_MESSAGE,
            )
        expected_arguments = build_codex_appium_process_arguments(
            expected_digest,
            self.export_nonce,
            self.device.udid,
        )
        if self.launch_arguments and self.launch_arguments != expected_arguments:
            raise PydanticCustomError(
                _LAUNCH_ARGUMENTS_MISMATCH,
                _LAUNCH_ARGUMENTS_MISMATCH_MESSAGE,
            )
        if self.export_files != _EXPORT_FILES:
            raise PydanticCustomError(
                _EXPORT_FILES_MISMATCH,
                _EXPORT_FILES_MISMATCH_MESSAGE,
            )
        object.__setattr__(self, "request_sha256", expected_digest)
        object.__setattr__(self, "launch_arguments", expected_arguments)
        return self

    def _digest_payload(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "identity": self.identity.model_dump(mode="json"),
            "context": self.context.model_dump(mode="json"),
            "prepared_background": self.prepared_background.model_dump(mode="json"),
            "device": self.device.model_dump(mode="json"),
            "locale": self.locale,
            "time_zone": self.time_zone,
            "export_nonce": self.export_nonce,
            "calendar_namespace": self.calendar_namespace,
        }


def write_codex_appium_job_contract(
    destination: Path,
    contract: CodexAppiumJobContract,
) -> None:
    _ = destination.write_text(contract.model_dump_json(), encoding="utf-8")
    destination.chmod(0o600)


__all__ = [
    "CodexAppiumJobContract",
    "CodexAppiumJobIdentity",
    "write_codex_appium_job_contract",
]
