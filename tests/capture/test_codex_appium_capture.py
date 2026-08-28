from __future__ import annotations

import stat
from typing import TYPE_CHECKING

import pytest

from ads_booster.capture.appium_codex import CodexAppiumJobAdapter
from ads_booster.capture.capture_safety import (
    CaptureAdapterError,
    CaptureControl,
    UdidCaptureLeaseFactory,
)
from ads_booster.capture.codex_appium_job import (
    CodexAppiumJobContract,
    write_codex_appium_job_contract,
)
from ads_booster.contracts import PreparedBackground, TraceBackgroundSearchProvenance

from .codex_appium_support import (
    AcceptingEditorVerifier,
    RecordingCodexJob,
    RecordingPhotoImporter,
    RecordingReadiness,
    RecordingWallpaperCollector,
    V2JobInputs,
    completed_result,
    job_paths,
    v2_contract,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_v2_job_contract_when_serialized_then_it_is_planless_and_secret_free(
    tmp_path: Path,
) -> None:
    # Given a complete hosted operational context and prepared native background
    contract = v2_contract()
    destination = tmp_path / "codex-appium-job.json"

    # When the immutable job is written for the official Codex CLI
    write_codex_appium_job_contract(destination, contract)

    # Then the job is private, parseable, and carries the worker-owned execution inputs
    serialized = destination.read_text(encoding="utf-8")
    reloaded = CodexAppiumJobContract.model_validate_json(serialized)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert reloaded.context == contract.context
    assert reloaded.prepared_background == contract.prepared_background
    assert reloaded.appium_server == "http://127.0.0.1:4723"
    assert "CONTROL_PLANE_TOKEN" not in serialized
    assert "worker_credential" not in serialized


def test_v2_job_contract_when_bound_field_changes_then_request_digest_changes() -> None:
    # Given a valid operational job and an independently rebuilt variant for each binding field
    baseline = v2_contract()
    changed_digests = (
        v2_contract(V2JobInputs(task_id="task-2")).request_sha256,
        v2_contract(V2JobInputs(concept="Different complete context")).request_sha256,
        v2_contract(V2JobInputs(device_name="iPhone 17 Air")).request_sha256,
        v2_contract(
            V2JobInputs(country="JP", locale="ja-JP", time_zone="Asia/Tokyo")
        ).request_sha256,
        v2_contract(V2JobInputs(background_sha256="c" * 64)).request_sha256,
        v2_contract(V2JobInputs(export_nonce="d" * 64)).request_sha256,
        v2_contract(V2JobInputs(calendar_namespace="trace-request-1-retry")).request_sha256,
    )

    # Then every independently bound job has a different canonical request digest
    assert all(digest != baseline.request_sha256 for digest in changed_digests)


def test_v2_job_contract_when_locale_disagrees_with_country_then_validation_fails() -> None:
    # Given a hosted country whose resolved locale has been replaced by another market
    inputs = V2JobInputs(locale="ja-JP", time_zone="Asia/Tokyo")

    # When the v2 job binds the operational context before it reaches Codex
    with pytest.raises(ValueError, match="codex_appium_job_context_mismatch"):
        _ = v2_contract(inputs)

    # Then a country cannot be run with an unrelated locale or IANA time zone


def test_v2_job_contract_when_background_provenance_disagrees_then_validation_fails() -> None:
    # Given a prepared background whose recorded source path differs from its local path
    # When the job contract binds that background before Codex can be invoked
    with pytest.raises(ValueError, match="prepared_background_provenance_mismatch"):
        _ = PreparedBackground(
            path="inputs/background.png",
            sha256="a" * 64,
            provenance=TraceBackgroundSearchProvenance(
                schema_version="trace.background-search.v1",
                artifact_path="inputs/other-background.png",
                artifact_sha256="a" * 64,
                query="quiet Seoul desk at dawn",
                provider="google-images",
                image_url="https://images.pexels.com/photo/1",
                source_url="https://www.pexels.com/photo/1",
            ),
        )

    # Then the mismatched background is rejected before any Codex call exists


def test_codex_appium_job_single_turn_when_executed_then_collects_native_export(
    tmp_path: Path,
) -> None:
    # Given a fully prepared v2 job and readiness completed before execution admission
    calls: list[str] = []
    codex = RecordingCodexJob(calls, completed_result())
    readiness = RecordingReadiness(calls)
    adapter = CodexAppiumJobAdapter(
        codex=codex,
        simulator=RecordingPhotoImporter(calls),
        collector=RecordingWallpaperCollector(calls),
        lease_factory=UdidCaptureLeaseFactory(tmp_path / "leases"),
        readiness=readiness,
        editor_verifier=AcceptingEditorVerifier(),
    )
    job_root, background, output, background_sha256 = job_paths(tmp_path)
    contract = v2_contract(V2JobInputs(background_sha256=background_sha256))
    control = CaptureControl.start(timeout_seconds=30)

    adapter.ensure_ready(contract, control)

    # When post-barrier execution runs
    provenance = adapter.execute(
        contract,
        job_root=job_root,
        background=background,
        output=output,
        control=control,
    )

    # Then one Codex turn sits between native setup and independent collection
    assert calls == ["ready", "clear", "import", "codex", "clear", "collect"]
    assert provenance.native_export_binding_verified is True
    assert provenance.session_id == "appium-session-1"
    recorded_payload = codex.payloads[0]
    recorded_contract = CodexAppiumJobContract.model_validate_json(recorded_payload)
    assert recorded_contract.appium_server == "http://127.0.0.1:4723"
    assert recorded_contract.prepared_background.path == "inputs/background.png"
    assert stat.S_IMODE((job_root / "codex-appium-result.json").stat().st_mode) == 0o600
    assert "CONTROL_PLANE_TOKEN" not in recorded_payload
    assert "worker_credential" not in recorded_payload


def test_codex_appium_job_when_calendar_is_outside_namespace_then_rejects_before_collection(
    tmp_path: Path,
) -> None:
    # Given Codex reports a calendar outside the request-owned namespace
    calls: list[str] = []
    result = completed_result()
    result["created_calendar_titles"] = ["Personal"]
    adapter = CodexAppiumJobAdapter(
        codex=RecordingCodexJob(
            calls,
            result,
        ),
        simulator=RecordingPhotoImporter(calls),
        collector=RecordingWallpaperCollector(calls),
        lease_factory=UdidCaptureLeaseFactory(tmp_path / "leases"),
        editor_verifier=AcceptingEditorVerifier(),
    )
    job_root, background, output, background_sha256 = job_paths(tmp_path)
    contract = v2_contract(V2JobInputs(background_sha256=background_sha256))

    # When completion evidence is validated
    with pytest.raises(CaptureAdapterError, match="not verified before save"):
        _ = adapter.execute(
            contract,
            job_root=job_root,
            background=background,
            output=output,
            control=CaptureControl.start(timeout_seconds=30),
        )

    # Then no native artifact is accepted
    assert calls == ["clear", "import", "codex"]


def test_codex_appium_job_when_cleanup_is_incomplete_then_rejects_collected_provenance(
    tmp_path: Path,
) -> None:
    # Given Codex leaves one request-owned calendar behind
    calls: list[str] = []
    result = completed_result()
    result["remaining_calendar_titles"] = ["trace-request-1-calendar-1"]
    adapter = CodexAppiumJobAdapter(
        codex=RecordingCodexJob(calls, result),
        simulator=RecordingPhotoImporter(calls),
        collector=RecordingWallpaperCollector(calls),
        lease_factory=UdidCaptureLeaseFactory(tmp_path / "leases"),
        editor_verifier=AcceptingEditorVerifier(),
    )
    job_root, background, output, background_sha256 = job_paths(tmp_path)
    contract = v2_contract(V2JobInputs(background_sha256=background_sha256))

    # When completion evidence is validated
    with pytest.raises(CaptureAdapterError, match="did not complete cleanup"):
        _ = adapter.execute(
            contract,
            job_root=job_root,
            background=background,
            output=output,
            control=CaptureControl.start(timeout_seconds=30),
        )

    # Then the already-collected artifact is not accepted as a completed job
    assert calls == ["clear", "import", "codex", "clear", "collect"]


def test_codex_appium_job_when_background_digest_changes_then_fails_before_side_effect(
    tmp_path: Path,
) -> None:
    # Given a contract whose verified background changed after preparation
    calls: list[str] = []
    adapter = CodexAppiumJobAdapter(
        codex=RecordingCodexJob(calls, completed_result()),
        simulator=RecordingPhotoImporter(calls),
        collector=RecordingWallpaperCollector(calls),
        lease_factory=UdidCaptureLeaseFactory(tmp_path / "leases"),
        editor_verifier=AcceptingEditorVerifier(),
    )
    job_root, background, output, _ = job_paths(tmp_path)
    contract = v2_contract()

    # When execution rebinds the prepared artifact immediately before side effects
    with pytest.raises(CaptureAdapterError, match="prepared background digest"):
        _ = adapter.execute(
            contract,
            job_root=job_root,
            background=background,
            output=output,
            control=CaptureControl.start(timeout_seconds=30),
        )

    # Then neither Simulator nor Codex is touched
    assert calls == []


def test_codex_appium_job_when_workspace_has_symlink_then_fails_before_side_effect(
    tmp_path: Path,
) -> None:
    # Given an otherwise valid request workspace reached through a symlink
    calls: list[str] = []
    adapter = CodexAppiumJobAdapter(
        codex=RecordingCodexJob(calls, completed_result()),
        simulator=RecordingPhotoImporter(calls),
        collector=RecordingWallpaperCollector(calls),
        lease_factory=UdidCaptureLeaseFactory(tmp_path / "leases"),
        editor_verifier=AcceptingEditorVerifier(),
    )
    real_root, _, _, background_sha256 = job_paths(tmp_path / "real")
    linked_root = tmp_path / "linked-request"
    linked_root.symlink_to(real_root, target_is_directory=True)
    contract = v2_contract(V2JobInputs(background_sha256=background_sha256))

    # When the post-barrier executor validates its filesystem boundary
    with pytest.raises(CaptureAdapterError, match="symlink-free"):
        _ = adapter.execute(
            contract,
            job_root=linked_root,
            background=linked_root / "inputs" / "background.png",
            output=linked_root / "outputs" / "wallpaper.png",
            control=CaptureControl.start(timeout_seconds=30),
        )

    # Then no device or Codex side effect begins
    assert calls == []
