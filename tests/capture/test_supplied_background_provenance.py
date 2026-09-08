"""Supplied assets retain truthful lineage in the existing native capture contract."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING

import pytest

from ads_booster.capture.appium_codex_validation import validate_execution_paths
from ads_booster.capture.capture_safety import CaptureAdapterError
from ads_booster.capture.codex_appium_job import CodexAppiumJobContract
from ads_booster.contracts.native_export import PreparedBackground

from .codex_appium_contract_support import v2_contract

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject


def supplied_payload(digest: str = "a" * 64) -> JsonObject:
    return {
        "path": "inputs/background.png",
        "sha256": digest,
        "provenance": {
            "schema_version": "trace.supplied-background.v1",
            "artifact_path": "inputs/background.png",
            "artifact_sha256": digest,
            "asset_id": "figma-background",
            "asset_revision": 2,
            "source": "Figma export by team member",
            "use_terms": "Team-created marketing asset",
            "data_permission": "synthetic",
            "permission_evidence": "No personal calendar data",
        },
    }


def test_existing_search_serialization_and_worker_digest_are_unchanged() -> None:
    contract = v2_contract()
    assert (
        contract.request_sha256
        == "357ee9c0f4e87b21a0b9486b18cea20df17881f37fbd2c64f712cfc95420d4d6"
    )
    assert (
        sha256(contract.prepared_background.model_dump_json().encode()).hexdigest()
        == "2a36320b2e86a4397cd6f48f29f257096ebdebd6e56f69253a297980d010dd1b"
    )


def test_supplied_asset_roundtrips_through_worker_and_checks_actual_bytes(tmp_path: Path) -> None:
    data = b"fixture source bytes; no visual quality claim"
    prepared = PreparedBackground.model_validate(supplied_payload(sha256(data).hexdigest()))
    original = v2_contract()
    payload = original.model_dump(mode="json")
    payload["prepared_background"] = prepared.model_dump(mode="json")
    del payload["request_sha256"]
    del payload["launch_arguments"]
    contract = CodexAppiumJobContract.model_validate(payload)
    assert contract.request_sha256 != original.request_sha256
    assert CodexAppiumJobContract.model_validate_json(contract.model_dump_json()) == contract
    job_root = tmp_path.resolve()
    background = job_root / prepared.path
    background.parent.mkdir()
    _ = background.write_bytes(data)
    output = job_root / "outputs/result.png"
    validate_execution_paths(contract, job_root, background, output)
    _ = background.write_bytes(b"changed")
    with pytest.raises(CaptureAdapterError, match="digest does not match"):
        validate_execution_paths(contract, job_root, background, output)


@pytest.mark.parametrize(("field", "value"), [("path", "inputs/other.png"), ("sha256", "b" * 64)])
def test_supplied_asset_rejects_provenance_mismatch(field: str, value: str) -> None:
    payload = supplied_payload()
    payload[field] = value
    with pytest.raises(ValueError, match="prepared_background_provenance_mismatch"):
        _ = PreparedBackground.model_validate(payload)
