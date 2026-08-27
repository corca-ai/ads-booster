from __future__ import annotations

import fcntl
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Annotated, ClassVar, Final, Literal, Protocol
from urllib.parse import quote
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from ads_booster.marketing.inbox import InboxQuiescence, MarketingInbox
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping, Sequence

    from ads_booster.transport.http import HttpClient

_REPOSITORY: Final = "corca-ai/ads-booster"
_API_ROOT: Final = "https://api.github.com"
_MANIFEST_ASSET: Final = "trace-marketing-release.json"
_BOOTSTRAP_ASSET: Final = "trace-marketing-bootstrap.py"
_PACKAGE_NAME: Final = "trace-appium-capture"
_PLATFORM: Final = "macos-arm64"
_SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
_HTTP_SUCCESS_MIN: Final = 200
_HTTP_SUCCESS_MAX: Final = 300
_MAX_BUNDLE_BYTES: Final = 1_073_741_824
_MAX_ARCHIVE_FILES: Final = 512
_MAX_ARCHIVE_MEMBER_BYTES: Final = 256 * 1024 * 1024
_MAX_GUARD_AGE_SECONDS: Final = 7200
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class WorkerUpdateError(RuntimeError):
    pass


class UpdateBusyError(WorkerUpdateError):
    pass


class ReleaseFile(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=240)
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    size: int = Field(gt=0, le=_MAX_BUNDLE_BYTES)


class MacWorkerReleaseManifest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["trace.marketing-release.v1"]
    version: Annotated[str, Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")]
    tag: Annotated[
        str,
        Field(pattern=r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"),
    ]
    commit_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    platform: Literal["macos-arm64"]
    python: Literal["3.14"]
    package: Literal["trace-appium-capture"]
    bundle: ReleaseFile
    bootstrap: ReleaseFile

    @model_validator(mode="after")
    def validate_linkage(self) -> MacWorkerReleaseManifest:
        if self.tag != f"v{self.version}":
            raise ValueError("release manifest tag does not match version")
        expected_bundle = f"trace-marketing-macos-arm64-v{self.version}.tar.gz"
        if self.bundle.name != expected_bundle:
            raise ValueError("release manifest bundle name does not match version")
        if self.bootstrap.name != _BOOTSTRAP_ASSET:
            raise ValueError("release manifest bootstrap name is invalid")
        return self


class GitHubReleaseAsset(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    name: str
    size: int
    digest: str | None = None
    browser_download_url: str


class GitHubReleasePayload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    tag_name: str
    target_commitish: str
    draft: bool
    prerelease: bool
    immutable: bool
    assets: tuple[GitHubReleaseAsset, ...]


@dataclass(frozen=True, slots=True)
class VerifiedRelease:
    manifest: MacWorkerReleaseManifest
    release: GitHubReleasePayload
    manifest_asset: GitHubReleaseAsset
    bundle_asset: GitHubReleaseAsset
    bootstrap_asset: GitHubReleaseAsset
    manifest_bytes: bytes


@dataclass(frozen=True, slots=True)
class ManagedWorkerPaths:
    root: Path
    agent_home: Path

    @property
    def releases(self) -> Path:
        return self.root / "releases"

    @property
    def staging(self) -> Path:
        return self.root / "staging"

    @property
    def current(self) -> Path:
        return self.root / "current"

    @property
    def state(self) -> Path:
        return self.root / "update-state.json"

    @property
    def lock(self) -> Path:
        return self.root / "update.lock"

    @property
    def guard(self) -> Path:
        return self.agent_home / "marketing-worker" / "update-requested.json"

    @property
    def heartbeat(self) -> Path:
        return self.agent_home / "marketing-worker" / "heartbeat.json"

    @property
    def inbox_home(self) -> Path:
        return self.agent_home / "marketing-worker" / "runtime"

    @property
    def codex_runs(self) -> Path:
        return self.agent_home / "codex-runs"

    def prepare(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        for path in (self.releases, self.staging, self.guard.parent):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.chmod(0o700)


class UpdateState(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["trace.marketing-update-state.v1"] = "trace.marketing-update-state.v1"
    status: Literal[
        "never_run",
        "up_to_date",
        "eligible",
        "staging",
        "deferred",
        "switching",
        "healthy",
        "failed",
        "rolled_back",
        "rollback_failed",
    ] = "never_run"
    current_version: str | None = None
    last_known_good_version: str | None = None
    candidate_version: str | None = None
    commit_sha: str | None = None
    reason: str | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class UpdateStateStore:
    path: Path

    def load(self) -> UpdateState:
        if not self.path.is_file():
            return UpdateState()
        try:
            return UpdateState.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as error:
            raise WorkerUpdateError("worker update state is invalid") from error

    def save(self, state: UpdateState) -> None:
        _atomic_private_text(self.path, state.model_dump_json(indent=2))


class HeartbeatReceipt(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["trace.marketing-heartbeat-receipt.v1"] = (
        "trace.marketing-heartbeat-receipt.v1"
    )
    worker_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    accepted_at: datetime

    @field_validator("accepted_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("heartbeat receipt timestamp must include a timezone")
        return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class HeartbeatReceiptStore:
    path: Path

    def save(self, *, worker_id: str, version: str) -> HeartbeatReceipt:
        receipt = HeartbeatReceipt(
            worker_id=worker_id,
            version=version,
            accepted_at=datetime.now(UTC),
        )
        _atomic_private_text(self.path, receipt.model_dump_json(indent=2))
        return receipt

    def load(self) -> HeartbeatReceipt | None:
        if not self.path.is_file():
            return None
        try:
            return HeartbeatReceipt.model_validate_json(self.path.read_text(encoding="utf-8"))
        except OSError, ValidationError:
            return None


class GitHubReleaseSource:
    def __init__(
        self,
        http: HttpClient,
        *,
        repository: str = _REPOSITORY,
        api_root: str = _API_ROOT,
    ) -> None:
        self.http: HttpClient = http
        self.repository: str = repository
        self.api_root: str = api_root.rstrip("/")

    def inspect_latest(self) -> VerifiedRelease:
        try:
            payload = GitHubReleasePayload.model_validate(
                self._get_json(f"/repos/{self.repository}/releases/latest")
            )
        except ValidationError as error:
            raise WorkerUpdateError("latest GitHub Release response is invalid") from error
        if payload.draft or payload.prerelease or not payload.immutable:
            raise WorkerUpdateError("latest GitHub Release is not stable and immutable")
        assets_by_name = {asset.name: asset for asset in payload.assets}
        manifest_asset = assets_by_name.get(_MANIFEST_ASSET)
        if manifest_asset is None:
            raise WorkerUpdateError("release manifest asset is missing")
        manifest_bytes = self._get_bytes(manifest_asset.browser_download_url)
        _verify_asset_bytes(manifest_asset, manifest_bytes, operation="manifest")
        try:
            manifest = MacWorkerReleaseManifest.model_validate_json(manifest_bytes)
        except ValidationError as error:
            raise WorkerUpdateError("release manifest contract is invalid") from error
        expected_names = {_MANIFEST_ASSET, manifest.bundle.name, manifest.bootstrap.name}
        if set(assets_by_name) != expected_names:
            raise WorkerUpdateError("release assets do not match the manifest envelope")
        bundle_asset = assets_by_name[manifest.bundle.name]
        bootstrap_asset = assets_by_name[manifest.bootstrap.name]
        _verify_asset_metadata(bundle_asset, manifest.bundle, operation="bundle")
        _verify_asset_metadata(bootstrap_asset, manifest.bootstrap, operation="bootstrap")
        if payload.tag_name != manifest.tag or payload.target_commitish != manifest.commit_sha:
            raise WorkerUpdateError("release tag or target commit does not match manifest")
        if self._resolve_tag_commit(manifest.tag) != manifest.commit_sha:
            raise WorkerUpdateError("Git tag does not resolve to the manifest commit")
        return VerifiedRelease(
            manifest=manifest,
            release=payload,
            manifest_asset=manifest_asset,
            bundle_asset=bundle_asset,
            bootstrap_asset=bootstrap_asset,
            manifest_bytes=manifest_bytes,
        )

    def download_bundle(self, release: VerifiedRelease) -> bytes:
        bundle = self._get_bytes(release.bundle_asset.browser_download_url)
        _verify_asset_bytes(release.bundle_asset, bundle, operation="bundle")
        if sha256(bundle).hexdigest() != release.manifest.bundle.sha256:
            raise WorkerUpdateError("downloaded bundle digest does not match manifest")
        return bundle

    def _resolve_tag_commit(self, tag: str) -> str:
        ref = self._get_json(f"/repos/{self.repository}/git/ref/tags/{quote(tag, safe='')}")
        raw_object = ref.get("object")
        if not isinstance(raw_object, dict):
            raise WorkerUpdateError("Git tag reference is invalid")
        object_type = raw_object.get("type")
        object_sha = raw_object.get("sha")
        if not isinstance(object_sha, str) or not _COMMIT_SHA.fullmatch(object_sha):
            raise WorkerUpdateError("Git tag reference SHA is invalid")
        if object_type == "commit":
            return object_sha
        if object_type != "tag":
            raise WorkerUpdateError("Git tag reference does not resolve to a commit")
        annotated = self._get_json(f"/repos/{self.repository}/git/tags/{object_sha}")
        annotated_object = annotated.get("object")
        if not isinstance(annotated_object, dict) or annotated_object.get("type") != "commit":
            raise WorkerUpdateError("annotated Git tag does not resolve directly to a commit")
        commit_sha = annotated_object.get("sha")
        if not isinstance(commit_sha, str) or not _COMMIT_SHA.fullmatch(commit_sha):
            raise WorkerUpdateError("annotated Git tag commit SHA is invalid")
        return commit_sha

    def _get_json(self, path: str) -> JsonObject:
        try:
            return _JSON_OBJECT.validate_json(self._get_bytes(f"{self.api_root}{path}"))
        except ValidationError as error:
            raise WorkerUpdateError("GitHub release response is not a JSON object") from error

    def _get_bytes(self, url: str) -> bytes:
        try:
            response = self.http.get(url, self._headers())
        except Exception as error:
            raise WorkerUpdateError("GitHub release request failed") from error
        if not _HTTP_SUCCESS_MIN <= response.status_code < _HTTP_SUCCESS_MAX:
            message = f"GitHub release request failed with HTTP {response.status_code}"
            raise WorkerUpdateError(message)
        return response.content

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "accept": "application/vnd.github+json",
            "user-agent": "trace-marketing-updater",
            "x-github-api-version": "2026-03-10",
        }


def default_install_root() -> Path:
    configured = os.environ.get("TRACE_MARKETING_INSTALL_ROOT")
    return (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".local" / "share" / "trace-marketing"
    )


def update_drain_requested(path: Path) -> bool:
    try:
        payload = _JSON_OBJECT.validate_json(path.read_text(encoding="utf-8"))
    except OSError, ValidationError:
        return False
    if payload.get("schema_version") != "trace.marketing-update-guard.v1":
        return False
    pid = payload.get("pid")
    created_at = payload.get("created_at")
    if not isinstance(created_at, str):
        return False
    try:
        created = datetime.fromisoformat(created_at)
    except ValueError:
        return False
    if created.tzinfo is None or created.utcoffset() is None:
        return False
    age = (datetime.now(UTC) - created.astimezone(UTC)).total_seconds()
    return (
        isinstance(pid, int)
        and not isinstance(pid, bool)
        and 0 <= age <= _MAX_GUARD_AGE_SECONDS
        and _pid_alive(pid)
    )


@contextmanager
def updater_lock(paths: ManagedWorkerPaths) -> Generator[None]:
    paths.prepare()
    paths.lock.touch(mode=0o600, exist_ok=True)
    paths.lock.chmod(0o600)
    with paths.lock.open("r+", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise UpdateBusyError("another worker update is already running") from error
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def drain_guard(paths: ManagedWorkerPaths) -> Generator[None]:
    _atomic_private_text(
        paths.guard,
        json.dumps(
            {
                "schema_version": "trace.marketing-update-guard.v1",
                "pid": os.getpid(),
                "created_at": datetime.now(UTC).isoformat(),
            },
            sort_keys=True,
        ),
    )
    try:
        yield
    finally:
        with suppress(FileNotFoundError):
            paths.guard.unlink()


@dataclass(frozen=True, slots=True)
class WorkerQuiescence:
    inbox: InboxQuiescence
    ambiguous_codex_runs: int

    @property
    def ready(self) -> bool:
        return self.inbox.ready and self.ambiguous_codex_runs == 0

    def as_json(self) -> JsonObject:
        return {
            "ready": self.ready,
            "received_tasks": self.inbox.received_tasks,
            "running_tasks": self.inbox.running_tasks,
            "pending_callbacks": self.inbox.pending_callbacks,
            "pending_approvals": self.inbox.pending_approvals,
            "ambiguous_codex_runs": self.ambiguous_codex_runs,
        }


def inspect_worker_quiescence(paths: ManagedWorkerPaths) -> WorkerQuiescence:
    inbox = MarketingInbox(paths.inbox_home).quiescence()
    ambiguous = 0
    if paths.codex_runs.is_dir():
        for run_root in paths.codex_runs.iterdir():
            if (
                run_root.is_dir()
                and (run_root / "executing").is_file()
                and not (run_root / "result.json").is_file()
            ):
                ambiguous += 1
    return WorkerQuiescence(inbox=inbox, ambiguous_codex_runs=ambiguous)


def wait_for_quiescence(
    paths: ManagedWorkerPaths,
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> WorkerQuiescence:
    deadline = time.monotonic() + timeout_seconds
    while True:
        snapshot = inspect_worker_quiescence(paths)
        if snapshot.ready or time.monotonic() >= deadline:
            return snapshot
        time.sleep(poll_seconds)


class CommandRunner(Protocol):
    def __call__(
        self,
        arguments: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]: ...


@dataclass(frozen=True, slots=True)
class InstalledRelease:
    version: str
    path: Path
    receipt: MacWorkerReleaseManifest

    @property
    def executable(self) -> Path:
        return self.path / "bin" / "trace-marketing"


@dataclass(frozen=True, slots=True)
class ManagedReleaseInstaller:
    paths: ManagedWorkerPaths
    uv_executable: Path
    command_runner: CommandRunner

    def stage(self, release: VerifiedRelease, bundle: bytes) -> InstalledRelease:
        self.paths.prepare()
        existing = self.paths.releases / release.manifest.version
        if existing.exists():
            return read_installed_release(existing, expected=release.manifest)
        attempt = self.paths.staging / uuid4().hex
        bundle_root = attempt / "bundle"
        staged_release = attempt / "release"
        attempt.mkdir(parents=True, mode=0o700)
        try:
            extract_release_bundle(bundle, bundle_root)
            wheelhouse = bundle_root / "wheelhouse"
            project_wheels = tuple(
                wheelhouse.glob(f"trace_appium_capture-{release.manifest.version}-*.whl")
            )
            if len(project_wheels) != 1:
                raise WorkerUpdateError("release wheelhouse does not contain one project wheel")
            self._run(
                (
                    str(self.uv_executable),
                    "venv",
                    "--python",
                    release.manifest.python,
                    "--no-python-downloads",
                    str(staged_release),
                ),
                operation="create staged environment",
            )
            self._run(
                (
                    str(self.uv_executable),
                    "pip",
                    "install",
                    "--python",
                    str(staged_release / "bin" / "python"),
                    "--no-index",
                    "--find-links",
                    str(wheelhouse),
                    f"{_PACKAGE_NAME}=={release.manifest.version}",
                ),
                operation="install staged release",
            )
            executable = staged_release / "bin" / "trace-marketing"
            if probe_installed_version(executable, self.command_runner) != release.manifest.version:
                raise WorkerUpdateError("staged executable version does not match release")
            _atomic_private_text(
                staged_release / "release-receipt.json",
                release.manifest.model_dump_json(indent=2),
            )
            try:
                _ = staged_release.replace(existing)
            except FileExistsError:
                return read_installed_release(existing, expected=release.manifest)
            return read_installed_release(existing, expected=release.manifest)
        finally:
            shutil.rmtree(attempt, ignore_errors=True)

    def _run(self, arguments: Sequence[str], *, operation: str) -> None:
        completed = self.command_runner(arguments, environment=None)
        if completed.returncode != 0:
            raise WorkerUpdateError(
                f"failed to {operation}: {_sanitized_process_detail(completed)}"
            )


def extract_release_bundle(bundle: bytes, destination: Path) -> None:
    if not bundle or len(bundle) > _MAX_BUNDLE_BYTES:
        raise WorkerUpdateError("release bundle size is invalid")
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    total_size = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as archive:
            members = archive.getmembers()
            if not members or len(members) > _MAX_ARCHIVE_FILES:
                raise WorkerUpdateError("release bundle file count is invalid")
            for member in members:
                target = _release_bundle_target(destination, member)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                if not member.isfile():
                    raise WorkerUpdateError("release bundle contains an unsupported member")
                total_size += member.size
                if total_size > _MAX_BUNDLE_BYTES:
                    raise WorkerUpdateError("release bundle expands beyond the size limit")
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                source = archive.extractfile(member)
                if source is None:
                    raise WorkerUpdateError("release bundle member could not be read")
                with source, target.open("wb") as stream:
                    shutil.copyfileobj(source, stream)
                target.chmod(0o600)
    except (OSError, tarfile.TarError) as error:
        raise WorkerUpdateError("release bundle archive is invalid") from error
    wheelhouse = destination / "wheelhouse"
    lock = destination / "requirements.lock"
    wheels = tuple(wheelhouse.glob("*.whl")) if wheelhouse.is_dir() else ()
    if not wheels or not lock.is_file() or not lock.read_text(encoding="utf-8").strip():
        raise WorkerUpdateError("release bundle wheelhouse is incomplete")


def _release_bundle_target(destination: Path, member: tarfile.TarInfo) -> Path:
    relative = PurePosixPath(member.name)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not relative.parts
        or relative.parts[0] not in {"wheelhouse", "requirements.lock"}
        or member.issym()
        or member.islnk()
        or member.isdev()
        or member.size > _MAX_ARCHIVE_MEMBER_BYTES
    ):
        raise WorkerUpdateError("release bundle contains an unsafe member")
    return destination.joinpath(*relative.parts)


def read_installed_release(
    path: Path,
    *,
    expected: MacWorkerReleaseManifest | None = None,
) -> InstalledRelease:
    try:
        manifest = MacWorkerReleaseManifest.model_validate_json(
            (path / "release-receipt.json").read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as error:
        raise WorkerUpdateError(f"installed release receipt is invalid under {path}") from error
    if expected is not None and manifest != expected:
        raise WorkerUpdateError("installed release receipt does not match candidate release")
    installed = InstalledRelease(version=manifest.version, path=path.resolve(), receipt=manifest)
    if not installed.executable.is_file():
        raise WorkerUpdateError("installed release executable is missing")
    return installed


def current_installed_release(paths: ManagedWorkerPaths) -> InstalledRelease:
    if not paths.current.is_symlink():
        raise WorkerUpdateError("managed current release is not bootstrapped")
    resolved = paths.current.resolve(strict=True)
    releases_root = paths.releases.resolve()
    if not resolved.is_relative_to(releases_root) or resolved.parent != releases_root:
        raise WorkerUpdateError("managed current symlink escapes the releases root")
    return read_installed_release(resolved)


def atomic_switch(paths: ManagedWorkerPaths, release: InstalledRelease) -> None:
    releases_root = paths.releases.resolve()
    target = release.path.resolve(strict=True)
    if not target.is_relative_to(releases_root) or target.parent != releases_root:
        raise WorkerUpdateError("atomic switch target is outside the releases root")
    temporary = paths.root / f".current-{uuid4().hex}"
    temporary.symlink_to(target)
    try:
        _ = temporary.replace(paths.current)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def probe_installed_version(executable: Path, command_runner: CommandRunner) -> str:
    completed = command_runner((str(executable), "version", "--json"), environment=None)
    if completed.returncode != 0:
        raise WorkerUpdateError("installed version probe failed")
    try:
        payload = _JSON_OBJECT.validate_json(completed.stdout)
    except ValidationError as error:
        raise WorkerUpdateError("installed version probe returned invalid JSON") from error
    version = payload.get("version")
    if not isinstance(version, str) or not _SEMVER.fullmatch(version):
        raise WorkerUpdateError("installed version probe returned an invalid version")
    return version


def run_command(
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        tuple(arguments),
        check=False,
        capture_output=True,
        text=True,
        env=None if environment is None else dict(environment),
    )


def _sanitized_process_detail(completed: subprocess.CompletedProcess[str]) -> str:
    detail = (completed.stderr or completed.stdout).strip().splitlines()
    return detail[-1][:300] if detail else f"exit {completed.returncode}"


class ReleaseSource(Protocol):
    def inspect_latest(self) -> VerifiedRelease: ...

    def download_bundle(self, release: VerifiedRelease) -> bytes: ...


class WorkerService(Protocol):
    def start(self) -> subprocess.CompletedProcess[str]: ...

    def stop(self) -> subprocess.CompletedProcess[str]: ...

    def status(self) -> subprocess.CompletedProcess[str]: ...

    def wait_until_stopped(self) -> bool: ...


class ReleaseInstaller(Protocol):
    def stage(self, release: VerifiedRelease, bundle: bytes) -> InstalledRelease: ...


class RuntimeVerifier(Protocol):
    def doctor(self, release: InstalledRelease) -> None: ...

    def start_and_verify(self, service: WorkerService, release: InstalledRelease) -> None: ...


@dataclass(frozen=True, slots=True)
class UpdateAttempt:
    status: str
    current_version: str | None
    candidate_version: str | None
    reason: str | None = None
    rollback_version: str | None = None
    quiescence: WorkerQuiescence | None = None

    def as_json(self) -> JsonObject:
        return {
            "status": self.status,
            "current_version": self.current_version,
            "candidate_version": self.candidate_version,
            "reason": self.reason,
            "rollback_version": self.rollback_version,
            "quiescence": None if self.quiescence is None else self.quiescence.as_json(),
        }


@dataclass(frozen=True, slots=True)
class WorkerRuntimeVerifier:
    paths: ManagedWorkerPaths
    command_runner: CommandRunner
    heartbeat_timeout_seconds: float = 90.0
    heartbeat_poll_seconds: float = 1.0

    def doctor(self, release: InstalledRelease) -> None:
        completed = self.command_runner(
            (str(release.executable), "worker", "doctor"),
            environment=None,
        )
        if completed.returncode != 0:
            raise WorkerUpdateError("worker doctor failed")
        try:
            payload = _JSON_OBJECT.validate_json(completed.stdout)
        except ValidationError as error:
            raise WorkerUpdateError("worker doctor returned invalid JSON") from error
        if payload.get("ready") is not True or payload.get("version") != release.version:
            raise WorkerUpdateError("worker doctor did not confirm the exact release version")

    def start_and_verify(self, service: WorkerService, release: InstalledRelease) -> None:
        started_after = datetime.now(UTC)
        started = service.start()
        if started.returncode != 0:
            raise WorkerUpdateError("worker LaunchAgent failed to start")
        if service.status().returncode != 0:
            raise WorkerUpdateError("worker LaunchAgent status is not running")
        self.doctor(release)
        deadline = time.monotonic() + self.heartbeat_timeout_seconds
        receipts = HeartbeatReceiptStore(self.paths.heartbeat)
        while True:
            receipt = receipts.load()
            if (
                receipt is not None
                and receipt.version == release.version
                and receipt.accepted_at >= started_after
            ):
                return
            if time.monotonic() >= deadline:
                raise WorkerUpdateError("exact-version worker heartbeat was not accepted")
            time.sleep(self.heartbeat_poll_seconds)


@dataclass(frozen=True, slots=True)
class MacWorkerUpdater:
    paths: ManagedWorkerPaths
    source: ReleaseSource
    installer: ReleaseInstaller
    service: WorkerService
    verifier: RuntimeVerifier
    state_store: UpdateStateStore
    drain_timeout_seconds: float = 900.0
    drain_poll_seconds: float = 1.0

    def inspect(self) -> UpdateAttempt:
        current = current_installed_release(self.paths)
        release = self.source.inspect_latest()
        comparison = _compare_versions(release.manifest.version, current.version)
        if comparison <= 0:
            return UpdateAttempt(
                status="up_to_date",
                current_version=current.version,
                candidate_version=release.manifest.version,
            )
        return UpdateAttempt(
            status="eligible",
            current_version=current.version,
            candidate_version=release.manifest.version,
        )

    def apply(self) -> UpdateAttempt:
        with updater_lock(self.paths):
            current = current_installed_release(self.paths)
            previous_state = self.state_store.load()
            if previous_state.last_known_good_version not in {None, current.version}:
                message = "managed current release differs from last-known-good state"
                raise WorkerUpdateError(message)
            release = self.source.inspect_latest()
            candidate_version = release.manifest.version
            if _compare_versions(candidate_version, current.version) <= 0:
                self._save_state(
                    "up_to_date",
                    current=current.version,
                    last_known_good=current.version,
                    candidate=candidate_version,
                    commit_sha=release.manifest.commit_sha,
                )
                return UpdateAttempt("up_to_date", current.version, candidate_version)

            self._save_state(
                "staging",
                current=current.version,
                last_known_good=current.version,
                candidate=candidate_version,
                commit_sha=release.manifest.commit_sha,
            )
            try:
                bundle = self.source.download_bundle(release)
                candidate = self.installer.stage(release, bundle)
                self.verifier.doctor(candidate)
            except WorkerUpdateError as error:
                self._save_state(
                    "failed",
                    current=current.version,
                    last_known_good=current.version,
                    candidate=candidate_version,
                    commit_sha=release.manifest.commit_sha,
                    reason=str(error),
                )
                return UpdateAttempt("failed", current.version, candidate_version, str(error))

            with drain_guard(self.paths):
                quiescence = wait_for_quiescence(
                    self.paths,
                    timeout_seconds=self.drain_timeout_seconds,
                    poll_seconds=self.drain_poll_seconds,
                )
                if not quiescence.ready:
                    reason = "worker still owns durable or ambiguous work"
                    self._save_state(
                        "deferred",
                        current=current.version,
                        last_known_good=current.version,
                        candidate=candidate.version,
                        commit_sha=release.manifest.commit_sha,
                        reason=reason,
                    )
                    return UpdateAttempt(
                        "deferred",
                        current.version,
                        candidate.version,
                        reason,
                        quiescence=quiescence,
                    )
                return self._switch(current, candidate, release, quiescence)

    def _switch(
        self,
        current: InstalledRelease,
        candidate: InstalledRelease,
        release: VerifiedRelease,
        quiescence: WorkerQuiescence,
    ) -> UpdateAttempt:
        if self.service.status().returncode != 0:
            reason = self._recover_unswitched_service(
                current,
                "worker LaunchAgent is not running before switch",
            )
            self._save_state(
                "failed",
                current=current.version,
                last_known_good=current.version,
                candidate=candidate.version,
                commit_sha=release.manifest.commit_sha,
                reason=reason,
            )
            return UpdateAttempt(
                "failed",
                current.version,
                candidate.version,
                reason,
                quiescence=quiescence,
            )
        stopped = self.service.stop()
        if not self.service.wait_until_stopped():
            failure = (
                "worker LaunchAgent could not be stopped"
                if stopped.returncode != 0
                else "worker LaunchAgent did not unload before switch"
            )
            reason = self._recover_unswitched_service(current, failure)
            self._save_state(
                "failed",
                current=current.version,
                last_known_good=current.version,
                candidate=candidate.version,
                commit_sha=release.manifest.commit_sha,
                reason=reason,
            )
            return UpdateAttempt(
                "failed",
                current.version,
                candidate.version,
                reason,
                quiescence=quiescence,
            )

        self._save_state(
            "switching",
            current=current.version,
            last_known_good=current.version,
            candidate=candidate.version,
            commit_sha=release.manifest.commit_sha,
        )
        try:
            atomic_switch(self.paths, candidate)
            self.verifier.start_and_verify(self.service, candidate)
        except (OSError, WorkerUpdateError) as error:
            return self._rollback(
                current,
                candidate,
                release,
                quiescence,
                failure=str(error),
            )
        self._save_state(
            "healthy",
            current=candidate.version,
            last_known_good=candidate.version,
            candidate=candidate.version,
            commit_sha=release.manifest.commit_sha,
        )
        return UpdateAttempt(
            "healthy",
            candidate.version,
            candidate.version,
            quiescence=quiescence,
        )

    def _recover_unswitched_service(
        self,
        current: InstalledRelease,
        failure: str,
    ) -> str:
        if self.service.status().returncode == 0:
            return failure
        try:
            self.verifier.start_and_verify(self.service, current)
        except WorkerUpdateError as recovery_error:
            return f"{failure}; previous worker recovery failed: {recovery_error}"
        return failure

    def _rollback(
        self,
        previous: InstalledRelease,
        candidate: InstalledRelease,
        release: VerifiedRelease,
        quiescence: WorkerQuiescence,
        *,
        failure: str,
    ) -> UpdateAttempt:
        _ = self.service.stop()
        _ = self.service.wait_until_stopped()
        try:
            atomic_switch(self.paths, previous)
            self.verifier.start_and_verify(self.service, previous)
        except (OSError, WorkerUpdateError) as rollback_error:
            reason = f"candidate failed: {failure}; rollback failed: {rollback_error}"
            self._save_state(
                "rollback_failed",
                current=previous.version,
                last_known_good=previous.version,
                candidate=candidate.version,
                commit_sha=release.manifest.commit_sha,
                reason=reason,
            )
            return UpdateAttempt(
                "rollback_failed",
                previous.version,
                candidate.version,
                reason,
                rollback_version=previous.version,
                quiescence=quiescence,
            )
        reason = f"candidate failed and was rolled back: {failure}"
        self._save_state(
            "rolled_back",
            current=previous.version,
            last_known_good=previous.version,
            candidate=candidate.version,
            commit_sha=release.manifest.commit_sha,
            reason=reason,
        )
        return UpdateAttempt(
            "rolled_back",
            previous.version,
            candidate.version,
            reason,
            rollback_version=previous.version,
            quiescence=quiescence,
        )

    def _save_state(  # noqa: PLR0913 - mirrors the persisted transition tuple.
        self,
        status: Literal[
            "up_to_date",
            "staging",
            "deferred",
            "switching",
            "healthy",
            "failed",
            "rolled_back",
            "rollback_failed",
        ],
        *,
        current: str,
        last_known_good: str,
        candidate: str,
        commit_sha: str,
        reason: str | None = None,
    ) -> None:
        self.state_store.save(
            UpdateState(
                status=status,
                current_version=current,
                last_known_good_version=last_known_good,
                candidate_version=candidate,
                commit_sha=commit_sha,
                reason=reason,
            )
        )


def _compare_versions(left: str, right: str) -> int:
    left_match = _SEMVER.fullmatch(left)
    right_match = _SEMVER.fullmatch(right)
    if left_match is None or right_match is None:
        raise WorkerUpdateError("release version is not strict semantic versioning")
    left_parts = tuple(map(int, left_match.groups()))
    right_parts = tuple(map(int, right_match.groups()))
    return (left_parts > right_parts) - (left_parts < right_parts)


def _verify_asset_metadata(
    asset: GitHubReleaseAsset,
    expected: ReleaseFile,
    *,
    operation: str,
) -> None:
    if asset.size != expected.size or _asset_digest(asset) != expected.sha256:
        raise WorkerUpdateError(f"release {operation} metadata does not match manifest")


def _verify_asset_bytes(asset: GitHubReleaseAsset, payload: bytes, *, operation: str) -> None:
    if len(payload) != asset.size or sha256(payload).hexdigest() != _asset_digest(asset):
        raise WorkerUpdateError(f"release {operation} bytes do not match GitHub digest")


def _asset_digest(asset: GitHubReleaseAsset) -> str:
    if not isinstance(asset.digest, str) or not asset.digest.startswith("sha256:"):
        raise WorkerUpdateError(f"release asset {asset.name!r} has no SHA-256 digest")
    digest = asset.digest.removeprefix("sha256:")
    if not _SHA256.fullmatch(digest):
        raise WorkerUpdateError(f"release asset {asset.name!r} digest is invalid")
    return digest


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _atomic_private_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_suffix(f"{path.suffix}.{uuid4().hex}.tmp")
    _ = temporary.write_text(value, encoding="utf-8")
    temporary.chmod(0o600)
    _ = temporary.replace(path)
