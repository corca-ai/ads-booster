from __future__ import annotations

import io
import json
import os
import subprocess
import tarfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from ads_booster.marketing.inbox import MarketingInbox
from ads_booster.marketing.models import MarketingTask, TaskKind, TaskResult, TaskStatus
from ads_booster.marketing.worker_update import (
    GitHubArtifactAttestationVerifier,
    GitHubReleaseAsset,
    GitHubReleasePayload,
    GitHubReleaseSource,
    HeartbeatReceipt,
    HeartbeatReceiptStore,
    InstalledRelease,
    MacWorkerReleaseManifest,
    MacWorkerUpdater,
    ManagedReleaseInstaller,
    ManagedWorkerPaths,
    ReleaseFile,
    UpdateStateStore,
    VerifiedRelease,
    WorkerRuntimeVerifier,
    WorkerService,
    WorkerUpdateError,
    current_installed_release,
    extract_release_bundle,
    inspect_worker_quiescence,
    update_drain_requested,
)
from ads_booster.transport.http import HttpResponse
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


def _manifest(version: str, *, bundle: bytes = b"bundle") -> MacWorkerReleaseManifest:
    bootstrap = b"bootstrap"
    return MacWorkerReleaseManifest(
        schema_version="trace.marketing-release.v1",
        version=version,
        tag=f"v{version}",
        commit_sha="a" * 40,
        platform="macos-arm64",
        python="3.14",
        package="trace-appium-capture",
        bundle=ReleaseFile(
            name=f"trace-marketing-macos-arm64-v{version}.tar.gz",
            sha256=sha256(bundle).hexdigest(),
            size=len(bundle),
        ),
        bootstrap=ReleaseFile(
            name="trace-marketing-bootstrap.py",
            sha256=sha256(bootstrap).hexdigest(),
            size=len(bootstrap),
        ),
    )


def _verified(version: str) -> VerifiedRelease:
    manifest = _manifest(version)
    manifest_bytes = manifest.model_dump_json().encode()
    manifest_asset = _asset("trace-marketing-release.json", manifest_bytes)
    bundle_asset = GitHubReleaseAsset(
        name=manifest.bundle.name,
        size=manifest.bundle.size,
        digest=f"sha256:{manifest.bundle.sha256}",
        browser_download_url="https://download.example/bundle",
    )
    bootstrap_asset = GitHubReleaseAsset(
        name=manifest.bootstrap.name,
        size=manifest.bootstrap.size,
        digest=f"sha256:{manifest.bootstrap.sha256}",
        browser_download_url="https://download.example/bootstrap",
    )
    return VerifiedRelease(
        manifest=manifest,
        release=GitHubReleasePayload(
            tag_name=manifest.tag,
            target_commitish=manifest.commit_sha,
            draft=False,
            prerelease=False,
            immutable=True,
            assets=(manifest_asset, bundle_asset, bootstrap_asset),
        ),
        manifest_asset=manifest_asset,
        bundle_asset=bundle_asset,
        bootstrap_asset=bootstrap_asset,
        manifest_bytes=manifest_bytes,
    )


def _asset(name: str, payload: bytes) -> GitHubReleaseAsset:
    return GitHubReleaseAsset(
        name=name,
        size=len(payload),
        digest=f"sha256:{sha256(payload).hexdigest()}",
        browser_download_url=f"https://download.example/{name}",
    )


@dataclass(slots=True)
class StubHttp:
    responses: dict[str, HttpResponse]

    def get(self, url: str, headers: Mapping[str, str]) -> HttpResponse:
        assert headers["user-agent"] == "trace-marketing-updater"
        return self.responses[url]

    def post_json(self, *_args: object, **_kwargs: object) -> HttpResponse:
        message = "unexpected POST"
        raise AssertionError(message)

    def post_form(self, *_args: object, **_kwargs: object) -> HttpResponse:
        message = "unexpected form POST"
        raise AssertionError(message)


def _response(payload: object) -> HttpResponse:
    content = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return HttpResponse(200, content, {})


@dataclass(slots=True)
class RecordingAttestationVerifier:
    verified: dict[str, bytes] | None = None

    def verify(self, release: VerifiedRelease, assets: Mapping[str, bytes]) -> None:
        assert release.manifest.commit_sha == "a" * 40
        self.verified = dict(assets)


def test_release_source_accepts_mutable_stable_metadata_then_verifies_all_artifacts() -> None:
    verified = _verified("1.2.3")
    manifest = verified.manifest
    release_payload = _JSON_OBJECT.validate_json(verified.release.model_dump_json())
    release_payload["immutable"] = False
    attestation_verifier = RecordingAttestationVerifier()
    http = StubHttp(
        {
            "https://api.github.com/repos/corca-ai/ads-booster/releases/latest": _response(
                release_payload
            ),
            verified.manifest_asset.browser_download_url: _response(verified.manifest_bytes),
            verified.bundle_asset.browser_download_url: _response(b"bundle"),
            verified.bootstrap_asset.browser_download_url: _response(b"bootstrap"),
            "https://api.github.com/repos/corca-ai/ads-booster/git/ref/tags/v1.2.3": _response(
                {"object": {"type": "commit", "sha": manifest.commit_sha}}
            ),
        }
    )

    source = GitHubReleaseSource(http, attestation_verifier)
    inspected = source.inspect_latest()
    bundle = source.download_bundle(inspected)

    assert inspected.manifest == manifest
    assert inspected.release.immutable is False
    assert bundle == b"bundle"
    assert attestation_verifier.verified == {
        "trace-marketing-release.json": inspected.manifest_bytes,
        inspected.manifest.bundle.name: b"bundle",
        "trace-marketing-bootstrap.py": b"bootstrap",
    }


@pytest.mark.parametrize(
    "mutation",
    ["draft", "prerelease", "extra-asset", "wrong-commit", "no-digest"],
)
def test_release_source_rejects_untrusted_release_metadata(mutation: str) -> None:
    verified = _verified("1.2.3")
    payload = _JSON_OBJECT.validate_json(verified.release.model_dump_json())
    if mutation == "draft":
        payload["draft"] = True
    elif mutation == "prerelease":
        payload["prerelease"] = True
    elif mutation == "extra-asset":
        assets = payload["assets"]
        assert isinstance(assets, list)
        assets.append(
            _JSON_OBJECT.validate_json(_asset("unexpected.txt", b"unexpected").model_dump_json())
        )
    elif mutation == "wrong-commit":
        payload["target_commitish"] = "b" * 40
    else:
        assets = payload["assets"]
        assert isinstance(assets, list)
        first_asset = assets[0]
        assert isinstance(first_asset, dict)
        first_asset["digest"] = None
    http = StubHttp(
        {
            "https://api.github.com/repos/corca-ai/ads-booster/releases/latest": _response(payload),
            verified.manifest_asset.browser_download_url: _response(verified.manifest_bytes),
            "https://api.github.com/repos/corca-ai/ads-booster/git/ref/tags/v1.2.3": _response(
                {"object": {"type": "commit", "sha": verified.manifest.commit_sha}}
            ),
        }
    )

    with pytest.raises(WorkerUpdateError):
        _ = GitHubReleaseSource(http, RecordingAttestationVerifier()).inspect_latest()


def test_attestation_verifier_pins_repository_workflow_ref_and_commit(tmp_path: Path) -> None:
    release = _verified("1.2.3")
    commands: list[tuple[str, ...]] = []

    def runner(
        arguments: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        _ = environment
        commands.append(tuple(arguments))
        return _completed(0)

    verifier = GitHubArtifactAttestationVerifier(tmp_path / "gh", runner)
    verifier.verify(
        release,
        {
            "trace-marketing-release.json": release.manifest_bytes,
            release.manifest.bundle.name: b"bundle",
            release.manifest.bootstrap.name: b"bootstrap",
        },
    )

    assert len(commands) == 3
    for command in commands:
        assert command[:3] == (str(tmp_path / "gh"), "attestation", "verify")
        assert command[command.index("--repo") + 1] == "corca-ai/ads-booster"
        assert command[command.index("--signer-workflow") + 1] == (
            "corca-ai/ads-booster/.github/workflows/release-mac-worker.yml"
        )
        assert command[command.index("--source-ref") + 1] == "refs/heads/main"
        assert command[command.index("--source-digest") + 1] == release.manifest.commit_sha
        assert "--deny-self-hosted-runners" in command


def test_attestation_verifier_fails_closed_on_missing_provenance(tmp_path: Path) -> None:
    release = _verified("1.2.3")

    def runner(
        arguments: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        _ = (arguments, environment)
        return subprocess.CompletedProcess([], 1, stdout="", stderr="no matching attestation")

    verifier = GitHubArtifactAttestationVerifier(tmp_path / "gh", runner)

    with pytest.raises(WorkerUpdateError, match="artifact attestation failed"):
        verifier.verify(
            release,
            {
                "trace-marketing-release.json": release.manifest_bytes,
                release.manifest.bundle.name: b"bundle",
                release.manifest.bootstrap.name: b"bootstrap",
            },
        )


def test_attestation_verifier_sanitizes_missing_github_cli(tmp_path: Path) -> None:
    release = _verified("1.2.3")

    def runner(
        arguments: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        _ = (arguments, environment)
        message = "fixture path and environment must not escape"
        raise OSError(message)

    verifier = GitHubArtifactAttestationVerifier(tmp_path / "missing-gh", runner)

    with pytest.raises(WorkerUpdateError, match="attestation could not run"):
        verifier.verify(
            release,
            {
                "trace-marketing-release.json": release.manifest_bytes,
                release.manifest.bundle.name: b"bundle",
                release.manifest.bootstrap.name: b"bootstrap",
            },
        )


def test_release_bundle_rejects_path_traversal(tmp_path: Path) -> None:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        payload = b"escape"
        info = tarfile.TarInfo("../escape")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    with pytest.raises(WorkerUpdateError, match="unsafe member"):
        extract_release_bundle(stream.getvalue(), tmp_path / "bundle")
    assert not (tmp_path / "escape").exists()


def test_only_live_recent_pid_guard_pauses_remote_claims(tmp_path: Path) -> None:
    guard = tmp_path / "guard.json"
    payload = {
        "schema_version": "trace.marketing-update-guard.v1",
        "pid": os.getpid(),
        "created_at": datetime.now(UTC).isoformat(),
    }
    _ = guard.write_text(json.dumps(payload))
    assert update_drain_requested(guard)

    payload["created_at"] = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    _ = guard.write_text(json.dumps(payload))
    assert update_drain_requested(guard) is False


def _task() -> MarketingTask:
    return MarketingTask(
        task_id="task-1",
        run_id="run-1",
        account_id="trace_kr",
        kind=TaskKind.CAPTURE,
        idempotency_key="capture:1",
        payload={},
        created_at=datetime.now(UTC),
    )


def test_quiescence_counts_inbox_outbox_and_ambiguous_codex_side_effects(
    tmp_path: Path,
) -> None:
    paths = ManagedWorkerPaths(tmp_path / "managed", tmp_path / "agent")
    inbox = MarketingInbox(paths.inbox_home)
    task = _task()
    _ = inbox.ingest(task)
    assert inbox.claim_next() == task
    _ = inbox.complete(task, TaskResult(status=TaskStatus.SUCCEEDED))
    run = paths.codex_runs / "run-1"
    run.mkdir(parents=True)
    _ = (run / "executing").write_text("native side effect started")

    snapshot = inspect_worker_quiescence(paths)

    assert snapshot.ready is False
    assert snapshot.inbox.pending_callbacks == 1
    assert snapshot.ambiguous_codex_runs == 1


def _install(paths: ManagedWorkerPaths, version: str) -> InstalledRelease:
    root = paths.releases / version
    executable = root / "bin" / "trace-marketing"
    executable.parent.mkdir(parents=True)
    executable.touch()
    executable.chmod(0o700)
    manifest = _manifest(version)
    _ = (root / "release-receipt.json").write_text(manifest.model_dump_json())
    return InstalledRelease(version=version, path=root.resolve(), receipt=manifest)


@dataclass(slots=True)
class FakeSource:
    release: VerifiedRelease
    download_count: int = 0

    def inspect_latest(self) -> VerifiedRelease:
        return self.release

    def download_bundle(self, release: VerifiedRelease) -> bytes:
        _ = release
        self.download_count += 1
        return b"bundle"


@dataclass(slots=True)
class FakeInstaller:
    candidate: InstalledRelease

    def stage(self, release: VerifiedRelease, bundle: bytes) -> InstalledRelease:
        _ = (release, bundle)
        return self.candidate


@dataclass(slots=True)
class FakeService:
    running: bool = True
    stop_count: int = 0
    start_count: int = 0

    def start(self) -> subprocess.CompletedProcess[str]:
        self.start_count += 1
        self.running = True
        return _completed(0)

    def stop(self) -> subprocess.CompletedProcess[str]:
        self.stop_count += 1
        self.running = False
        return _completed(0)

    def status(self) -> subprocess.CompletedProcess[str]:
        return _completed(0 if self.running else 1)

    @staticmethod
    def wait_until_stopped() -> bool:
        return True


@dataclass(slots=True)
class FakeVerifier:
    fail_started_version: str | None = None
    doctors: list[str] = field(default_factory=list)
    starts: list[str] = field(default_factory=list)

    def doctor(self, release: InstalledRelease) -> None:
        self.doctors.append(release.version)

    def start_and_verify(self, service: WorkerService, release: InstalledRelease) -> None:
        self.starts.append(release.version)
        assert service.start().returncode == 0
        if release.version == self.fail_started_version:
            message = "injected exact-version heartbeat failure"
            raise WorkerUpdateError(message)


def _completed(returncode: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout="", stderr="")


def test_staged_release_uses_a_relocatable_environment_before_promotion(
    tmp_path: Path,
) -> None:
    paths = ManagedWorkerPaths(tmp_path / "managed", tmp_path / "agent")
    release = _verified("1.2.3")
    commands: list[tuple[str, ...]] = []

    def runner(
        arguments: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        _ = environment
        command = tuple(arguments)
        commands.append(command)
        if len(command) > 1 and command[1] == "venv":
            candidate = Path(command[-1])
            (candidate / "bin").mkdir(parents=True)
            _ = (candidate / "bin" / "python").write_text("fixture", encoding="utf-8")
        elif len(command) > 1 and command[1] == "pip":
            python = Path(command[command.index("--python") + 1])
            executable = python.parent / "trace-marketing"
            _ = executable.write_text("fixture", encoding="utf-8")
        elif command[-2:] == ("version", "--json"):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"version":"1.2.3"}',
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
        for name, content in (
            ("wheelhouse/trace_appium_capture-1.2.3-py3-none-any.whl", b"wheel"),
            ("requirements.lock", b"trace-appium-capture==1.2.3\n"),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            bundle.addfile(member, io.BytesIO(content))

    installed = ManagedReleaseInstaller(paths, tmp_path / "uv", runner).stage(
        release,
        archive.getvalue(),
    )

    venv_command = next(command for command in commands if command[1] == "venv")
    assert "--relocatable" in venv_command
    assert installed.path == (paths.releases / "1.2.3").resolve()
    assert installed.executable.is_file()


def _updater(
    paths: ManagedWorkerPaths,
    candidate: InstalledRelease,
    service: FakeService,
    verifier: FakeVerifier,
    *,
    drain_timeout: float = 0,
) -> MacWorkerUpdater:
    return MacWorkerUpdater(
        paths=paths,
        source=FakeSource(_verified(candidate.version)),
        installer=FakeInstaller(candidate),
        service=service,
        verifier=verifier,
        state_store=UpdateStateStore(paths.state),
        drain_timeout_seconds=drain_timeout,
        drain_poll_seconds=0,
    )


def test_dry_run_verifies_candidate_artifacts_without_staging(tmp_path: Path) -> None:
    paths = ManagedWorkerPaths(tmp_path / "managed", tmp_path / "agent")
    paths.prepare()
    previous = _install(paths, "1.0.0")
    candidate = _install(paths, "1.1.0")
    paths.current.symlink_to(previous.path, target_is_directory=True)
    source = FakeSource(_verified(candidate.version))
    updater = MacWorkerUpdater(
        paths=paths,
        source=source,
        installer=FakeInstaller(candidate),
        service=FakeService(),
        verifier=FakeVerifier(),
        state_store=UpdateStateStore(paths.state),
    )

    attempt = updater.inspect()

    assert attempt.status == "eligible"
    assert source.download_count == 1
    assert current_installed_release(paths).version == "1.0.0"


def test_busy_worker_defers_without_stopping_launchd(tmp_path: Path) -> None:
    paths = ManagedWorkerPaths(tmp_path / "managed", tmp_path / "agent")
    paths.prepare()
    previous = _install(paths, "1.0.0")
    candidate = _install(paths, "1.1.0")
    paths.current.symlink_to(previous.path, target_is_directory=True)
    _ = MarketingInbox(paths.inbox_home).ingest(_task())
    service = FakeService()

    attempt = _updater(paths, candidate, service, FakeVerifier()).apply()

    assert attempt.status == "deferred"
    assert attempt.quiescence is not None
    assert attempt.quiescence.inbox.received_tasks == 1
    assert service.stop_count == 0
    assert current_installed_release(paths).version == "1.0.0"


def test_stopped_worker_is_recovered_without_switching(tmp_path: Path) -> None:
    paths = ManagedWorkerPaths(tmp_path / "managed", tmp_path / "agent")
    paths.prepare()
    previous = _install(paths, "1.0.0")
    candidate = _install(paths, "1.1.0")
    paths.current.symlink_to(previous.path, target_is_directory=True)
    service = FakeService(running=False)
    verifier = FakeVerifier()

    attempt = _updater(paths, candidate, service, verifier).apply()

    assert attempt.status == "failed"
    assert attempt.reason == "worker LaunchAgent is not running before switch"
    assert current_installed_release(paths).version == "1.0.0"
    assert service.running is True
    assert service.stop_count == 0
    assert verifier.starts == ["1.0.0"]


@pytest.mark.parametrize(
    ("fail_version", "expected_status", "expected_current"),
    [(None, "healthy", "1.1.0"), ("1.1.0", "rolled_back", "1.0.0")],
)
def test_atomic_switch_or_exact_version_failure_rollback(
    fail_version: str | None,
    expected_status: str,
    expected_current: str,
    tmp_path: Path,
) -> None:
    paths = ManagedWorkerPaths(tmp_path / "managed", tmp_path / "agent")
    paths.prepare()
    previous = _install(paths, "1.0.0")
    candidate = _install(paths, "1.1.0")
    paths.current.symlink_to(previous.path, target_is_directory=True)
    service = FakeService()
    verifier = FakeVerifier(fail_started_version=fail_version)

    attempt = _updater(paths, candidate, service, verifier).apply()

    assert attempt.status == expected_status
    assert current_installed_release(paths).version == expected_current
    assert service.stop_count == (1 if fail_version is None else 2)
    assert verifier.starts == (["1.1.0"] if fail_version is None else ["1.1.0", "1.0.0"])


def test_runtime_verifier_rejects_stale_or_wrong_version_heartbeat(tmp_path: Path) -> None:
    paths = ManagedWorkerPaths(tmp_path / "managed", tmp_path / "agent")
    paths.prepare()
    release = _install(paths, "1.2.3")
    _ = HeartbeatReceiptStore(paths.heartbeat).save(worker_id="worker-1", version="1.2.2")
    service = FakeService(running=False)

    def doctor_runner(
        arguments: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        _ = (arguments, environment)
        return subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps({"ready": True, "version": "1.2.3"}),
            stderr="",
        )

    verifier = WorkerRuntimeVerifier(
        paths=paths,
        command_runner=doctor_runner,
        heartbeat_timeout_seconds=0,
        heartbeat_poll_seconds=0,
    )

    with pytest.raises(WorkerUpdateError, match="exact-version worker heartbeat"):
        verifier.start_and_verify(service, release)


def test_heartbeat_receipt_rejects_naive_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat.json"
    _ = path.write_text(
        HeartbeatReceipt(
            worker_id="worker-1",
            version="1.2.3",
            accepted_at=datetime.now(UTC) - timedelta(seconds=1),
        ).model_dump_json()
    )
    payload = _JSON_OBJECT.validate_json(path.read_text())
    payload["accepted_at"] = "2026-08-27T12:00:00"
    _ = path.write_text(json.dumps(payload))

    assert HeartbeatReceiptStore(path).load() is None
