from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.models import ContractModel, RelativePath, Sha256Digest

_PREPARED_BACKGROUND_PROVENANCE_MISMATCH = "prepared_background_provenance_mismatch"
_PREPARED_BACKGROUND_PROVENANCE_MISMATCH_MESSAGE = (
    "prepared background path and digest must match its provenance"
)


class NativeExportContract(ContractModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid", strict=True)


class TraceBackgroundSearchProvenance(NativeExportContract):
    schema_version: Literal["trace.background-search.v1"]
    artifact_path: RelativePath
    artifact_sha256: Sha256Digest
    query: Annotated[str, Field(min_length=1, max_length=500)]
    provider: Annotated[str, Field(min_length=1, max_length=120)]
    image_url: Annotated[str, Field(min_length=1, max_length=4_096)]
    source_url: Annotated[str, Field(min_length=1, max_length=4_096)]


class PreparedBackground(NativeExportContract):
    path: RelativePath
    sha256: Sha256Digest
    provenance: TraceBackgroundSearchProvenance

    @model_validator(mode="after")
    def require_provenance_matches_prepared_artifact(self) -> PreparedBackground:
        if (
            self.path != self.provenance.artifact_path
            or self.sha256 != self.provenance.artifact_sha256
        ):
            raise PydanticCustomError(
                _PREPARED_BACKGROUND_PROVENANCE_MISMATCH,
                _PREPARED_BACKGROUND_PROVENANCE_MISMATCH_MESSAGE,
            )
        return self


class WallpaperExportManifest(NativeExportContract):
    schema_version: Literal["trace.wallpaper-export-manifest.v1"]
    request_sha256: Sha256Digest
    export_nonce: Sha256Digest
    bundle_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9.-]+$")]
    device_udid: Annotated[str, Field(pattern=r"^[A-F0-9-]{36}$")]
    role: Literal["trace_wallpaper"]
    artifact_sha256: Sha256Digest
    width: Annotated[int, Field(gt=0, le=8192)]
    height: Annotated[int, Field(gt=0, le=8192)]


__all__ = [
    "NativeExportContract",
    "PreparedBackground",
    "TraceBackgroundSearchProvenance",
    "WallpaperExportManifest",
]
