"""Inspect official Codex executable identity for provider research canaries."""

from __future__ import annotations

import subprocess
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

from ads_booster.contracts.models import ContractModel, Sha256Digest

if TYPE_CHECKING:
    from pathlib import Path

_PACKAGE_NAME = "trace-appium-capture"


class CodexRuntimeIdentityError(ValueError):
    """The local executable or installed package identity cannot be verified."""


class MarketingJudgmentRuntimeIdentity(ContractModel):
    """Observed local executable/package identity plus the explicitly requested model."""

    schema_version: Literal["trace.marketing-judgment-runtime.v1"]
    provider_id: Annotated[str, Field(min_length=1, max_length=120)]
    requested_model_id: Annotated[str, Field(min_length=1, max_length=240)]
    executable_name: Annotated[str, Field(min_length=1, max_length=255)]
    executable_sha256: Sha256Digest
    executable_version: Annotated[str, Field(min_length=1, max_length=500)]
    package_version: Annotated[str, Field(min_length=1, max_length=120)]


def inspect_marketing_judgment_runtime(
    executable: Path,
    *,
    requested_model_id: str,
    provider_id: str = "openai-codex-cli",
) -> MarketingJudgmentRuntimeIdentity:
    """Inspect the requested Codex executable without claiming served-model attestation."""
    try:
        resolved = executable.resolve(strict=True)
        executable_sha256 = _file_sha256(resolved)
        completed = subprocess.run(  # noqa: S603
            [str(resolved), "--version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        message = "judgment_canary_runtime_identity_unavailable"
        raise CodexRuntimeIdentityError(message) from error
    reported_version = completed.stdout.strip() or completed.stderr.strip()
    if completed.returncode != 0 or not reported_version:
        message = "judgment_canary_runtime_identity_unavailable"
        raise CodexRuntimeIdentityError(message)
    try:
        package_version = version(_PACKAGE_NAME)
    except PackageNotFoundError as error:
        message = "judgment_canary_package_identity_unavailable"
        raise CodexRuntimeIdentityError(message) from error
    return MarketingJudgmentRuntimeIdentity(
        schema_version="trace.marketing-judgment-runtime.v1",
        provider_id=provider_id,
        requested_model_id=requested_model_id,
        executable_name=resolved.name,
        executable_sha256=executable_sha256,
        executable_version=reported_version,
        package_version=package_version,
    )


def _file_sha256(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        message = "judgment_canary_runtime_identity_unavailable"
        raise CodexRuntimeIdentityError(message) from error
    return digest.hexdigest()
