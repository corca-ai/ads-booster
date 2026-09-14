from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from ads_booster.agent.service.trace_post import (
    _bundle_digest,  # pyright: ignore[reportPrivateUsage]
    trace_post_descriptor,
)
from ads_booster.contracts.creative_work import AssetParent
from ads_booster.contracts.trace_post import (
    TracePostAsset,
    TracePostCaption,
    TracePostInput,
    TracePostSuccess,
)

_DIGEST = "a" * 64


def test_trace_post_input_rejects_paths_models_and_unknown_choices() -> None:
    with pytest.raises(ValidationError):
        _ = TracePostInput.model_validate(
            {
                "schema_version": "trace.trace-post-input.v1",
                "concept": "cute",
                "model": "user-selected",
            }
        )
    with pytest.raises(ValidationError):
        _ = TracePostInput.model_validate(
            {
                "schema_version": "trace.trace-post-input.v1",
                "concept": "cute",
                "motif": "outside-card",
            }
        )


def test_trace_post_success_requires_six_country_role_assets() -> None:
    reference = AssetParent(asset_id="asset", revision=1, sha256=_DIGEST)
    assets = tuple(
        TracePostAsset(country=country, role=role, asset=reference)
        for role in ("final", "scene")
        for country in ("kr", "jp", "tw")
    )
    captions = tuple(
        TracePostCaption(country=country, text="caption", reply_link="open", tutorial="open")
        for country in ("kr", "jp", "tw")
    )
    payload = {
        "schema_version": "trace.trace-post-success.v1",
        "assets": [item.model_dump(mode="json") for item in assets],
        "captions": [item.model_dump(mode="json") for item in captions],
        "run_summary_sha256": _DIGEST,
        "bundle_sha256": _DIGEST,
        "recorded_image_call_count": 7,
        "human_review_required": True,
    }
    result = TracePostSuccess.model_validate(payload)
    assert len(result.assets) == 6
    with pytest.raises(ValidationError):
        _ = TracePostSuccess.model_validate(
            {**payload, "assets": [assets[0].model_dump(mode="json")] * 6}
        )


def test_trace_post_descriptor_binds_approval_cost_and_manual_reconciliation() -> None:
    descriptor = trace_post_descriptor(now=datetime(2026, 9, 11, tzinfo=UTC))
    assert descriptor.capability_id == "creative.trace_post"
    assert descriptor.approval_policy.mode == "required"
    assert descriptor.cost.worst_case_units == 14
    assert descriptor.reconciliation.mode == "manual"


def test_frozen_bundle_digest_rejects_changed_source(tmp_path: Path) -> None:
    installed = Path(str(files("ads_booster").joinpath("trace_post_bundle")))
    frozen = tmp_path / "repo"
    _ = shutil.copytree(installed, frozen)
    assert _bundle_digest(frozen) == _bundle_digest(installed)
    target = frozen / "context/RUN-POLICY.md"
    _ = target.write_text(target.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    with pytest.raises(ValueError, match="trace_post_bundle_digest_invalid"):
        _ = _bundle_digest(frozen)


def test_frozen_bundle_digest_requires_every_packaged_source(tmp_path: Path) -> None:
    installed = Path(str(files("ads_booster").joinpath("trace_post_bundle")))
    frozen = tmp_path / "repo"
    _ = shutil.copytree(installed, frozen)
    provenance = frozen / "provenance.json"
    value = cast("dict[str, object]", json.loads(provenance.read_text(encoding="utf-8")))
    manifest = cast("dict[str, str]", value["files"])
    _ = manifest.pop("scripts/image_call.py")
    _ = provenance.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="trace_post_bundle_manifest_incomplete"):
        _ = _bundle_digest(frozen)
