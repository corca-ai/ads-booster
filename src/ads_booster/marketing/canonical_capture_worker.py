"""Bounded Mac worker with durable start intent and upload-only recovery."""

from __future__ import annotations

import base64
import fcntl
import json
import os
import re
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request

from pydantic import TypeAdapter

from ads_booster.capture.capture_safety import CaptureControl
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.tool_capability import ToolReadiness
from ads_booster.marketing.agent_service.creative_capture_contract import (
    validate_native_capture_result,
)
from ads_booster.marketing.agent_service.oauth import open_auth_request
from ads_booster.marketing.agent_service.remote_capture_contract import (
    RemoteCaptureLease,
    RemoteCaptureProfile,
    RemoteCaptureUpload,
)
from ads_booster.providers.codex_cli import read_review_images
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Callable

    from ads_booster.marketing.agent_service.creative_capture import CaptureWorker

_MAX_IMAGE = 10 * 1024 * 1024
_MAX_RESPONSE = 15 * 1024 * 1024
_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_ROW: TypeAdapter[tuple[str, str, str] | None] = TypeAdapter(tuple[str, str, str] | None)


class CaptureControlPlane(Protocol):
    def request(self, method: str, path: str, body: JsonObject | None = None) -> JsonObject: ...


class Response(Protocol):
    def read(self, size: int = -1) -> bytes: ...
    def geturl(self) -> str: ...
    def close(self) -> None: ...


def _open(request: Request, *, timeout: float) -> Response:
    return cast("Response", open_auth_request(request, timeout=timeout))


@dataclass(frozen=True, slots=True)
class RemoteCaptureHttp:
    origin: str
    token_env: str
    allow_loopback_http: bool = False
    opener: Callable[..., Response] = field(default=_open, repr=False)

    def __post_init__(self) -> None:
        """Accept only a pinned HTTPS origin or explicitly allowed local test transport."""
        url = urlsplit(self.origin)
        local = self.allow_loopback_http and url.hostname in {"127.0.0.1", "localhost", "::1"}
        if (
            (url.scheme != "https" and not (url.scheme == "http" and local))
            or not url.hostname
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError("remote_capture_origin_invalid")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.token_env):
            raise ValueError("remote_capture_token_env_invalid")

    def request(self, method: str, path: str, body: JsonObject | None = None) -> JsonObject:
        if (
            method not in {"GET", "POST"}
            or not path.startswith("/workers/capture/")
            or ".." in path
            or "#" in path
        ):
            raise ValueError("remote_capture_request_invalid")
        token = os.environ.get(self.token_env, "")
        if not token or "\r" in token or "\n" in token:
            raise ValueError("remote_capture_token_unavailable")
        data = None if body is None else json.dumps(body).encode()
        if data is not None and len(data) > _MAX_RESPONSE:
            raise ValueError("remote_capture_request_too_large")
        url = self.origin.rstrip("/") + path
        try:
            response = self.opener(
                Request(  # noqa: S310 - validated pinned HTTPS origin and fixed worker route.
                    url,
                    data=data,
                    method=method,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                ),
                timeout=30,
            )
            try:
                if response.geturl() != url:
                    raise ValueError("remote_capture_redirect_rejected")
                value = response.read(_MAX_RESPONSE + 1)
                if len(value) > _MAX_RESPONSE:
                    raise ValueError("remote_capture_response_too_large")
                return _JSON.validate_json(value)
            finally:
                response.close()
        except Exception:  # noqa: BLE001 - preserve uncertainty without exposing provider errors.
            raise ValueError("remote_capture_http_failed") from None


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class CanonicalCaptureWorker:
    root: Path
    profile: RemoteCaptureProfile
    http: CaptureControlPlane
    worker: CaptureWorker
    probe: Callable[[datetime], ToolReadiness]
    clock: Callable[[], datetime] = _now

    def doctor(self, *, check_server: bool = False) -> JsonObject:
        """Default doctor performs only the injected local read-only readiness check."""
        if check_server:
            remote = self.http.request("GET", "/workers/capture/profile")
            profile = RemoteCaptureProfile.model_validate(remote.get("profile", remote))
            if profile != self.profile:
                raise ValueError("remote_capture_profile_changed")
        if Path(self.profile.python_executable).resolve() != Path(sys.executable).resolve():
            return {
                "ready": False,
                "reason_code": "capture_python_profile_mismatch",
                "profile_sha256": contract_sha256(self.profile),
            }
        try:
            ready = self.probe(self.clock())
        except Exception:  # noqa: BLE001 - preserve uncertainty without exposing provider errors.
            return {
                "ready": False,
                "reason_code": "capture_probe_failed",
                "profile_sha256": contract_sha256(self.profile),
            }
        age = (self.clock() - ready.observed_at).total_seconds()
        if ready.ready and not 0 <= age <= ready.max_age_seconds:
            return {
                "ready": False,
                "reason_code": "capture_readiness_stale",
                "profile_sha256": contract_sha256(self.profile),
            }
        return {
            "ready": ready.ready,
            "reason_code": ready.reason_code,
            "profile_sha256": contract_sha256(self.profile),
        }

    def work_once(self) -> JsonObject:
        readiness = self.doctor(check_server=True)
        _ = self.http.request("POST", "/workers/capture/heartbeat", readiness)
        self._initialize()
        descriptor = os.open(
            self.root / "worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"state": "busy"}
            return self._work_once_locked(readiness)

    def _work_once_locked(self, readiness: JsonObject) -> JsonObject:
        pending = self._pending()
        if pending is not None:
            lease, stage, upload = pending
            if stage == "upload_pending":
                return self._upload(lease, RemoteCaptureUpload.model_validate_json(upload))
            return self._uncertain(lease)
        if readiness["ready"] is not True:
            return {"state": "unavailable", "reason_code": readiness["reason_code"]}
        response = self.http.request("POST", "/workers/capture/claim", {})
        raw = response.get("lease")
        if raw is None:
            return {"state": "idle"}
        return self._run_lease(RemoteCaptureLease.model_validate(raw))

    def _run_lease(self, lease: RemoteCaptureLease) -> JsonObject:  # noqa: PLR0911 - explicit effect boundaries.
        if lease.job.profile != self.profile:
            raise ValueError("remote_capture_job_profile_changed")
        approval = lease.job.approval
        now = self.clock()
        if lease.expires_at <= now:
            raise ValueError("remote_capture_lease_expired")
        cached = self._known(lease)
        if cached is not None:
            if cached == "done":
                return {"state": "done", "operation_id": lease.job.operation_id}
            raise ValueError("remote_capture_operation_already_started")
        if approval.expires_at is None or not approval.decided_at <= now < approval.expires_at:
            upload = RemoteCaptureUpload(
                job_sha256=contract_sha256(lease.job),
                lease_id=lease.lease_id,
                disposition="no_effect",
                error_code="capture_approval_expired",
            )
            self._save(lease, "upload_pending", upload.model_dump_json())
            return self._upload(lease, upload)
        try:
            job_root, background, output = self._prepare(lease)
        except Exception:  # noqa: BLE001 - preserve uncertainty without exposing provider errors.
            upload = RemoteCaptureUpload(
                job_sha256=contract_sha256(lease.job),
                lease_id=lease.lease_id,
                disposition="no_effect",
                error_code="capture_source_validation_failed",
            )
            self._save(lease, "upload_pending", upload.model_dump_json())
            return self._upload(lease, upload)
        self._save(lease, "start_intent", "")
        try:
            started = self.http.request("POST", "/workers/capture/start", self._identity(lease))
        except Exception:  # noqa: BLE001 - preserve uncertainty without exposing provider errors.
            return self._uncertain(lease)
        if started.get("started") is not True:
            if started.get("accepted") is True:
                self._save(lease, "done", "")
                return {"state": "not_started", "operation_id": lease.job.operation_id}
            return self._uncertain(lease)
        return self._execute_started(lease, job_root, background, output)

    def _execute_started(
        self, lease: RemoteCaptureLease, job_root: Path, background: Path, output: Path
    ) -> JsonObject:
        try:
            control = CaptureControl.start(
                self.profile.timeout_seconds, cancel_file=job_root / "cancel"
            )
            self.worker.ensure_ready(lease.job.contract, control)
            provenance = self.worker.execute(
                lease.job.contract,
                job_root=job_root,
                background=background,
                output=output,
                control=control,
            )
            image = read_review_images((output,))[0]
            validate_native_capture_result(
                contract=lease.job.contract,
                provenance=provenance,
                image_format=image.format,
                image_sha256=image.sha256,
                byte_size=len(image.data),
                width=image.width,
                height=image.height,
            )
            output.chmod(0o600)
            upload = RemoteCaptureUpload(
                job_sha256=contract_sha256(lease.job),
                lease_id=lease.lease_id,
                disposition="succeeded",
                provenance=provenance,
                png_base64=base64.b64encode(image.data).decode(),
            )
        except Exception:  # noqa: BLE001 - preserve uncertainty without exposing provider errors.
            return self._uncertain(lease)
        self._save(lease, "upload_pending", upload.model_dump_json())
        return self._upload(lease, upload)

    def _initialize(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)
        with closing(sqlite3.connect(self.root / "worker.sqlite3")) as db, db:
            _ = db.execute("""CREATE TABLE IF NOT EXISTS captures
                (operation TEXT PRIMARY KEY, job_digest TEXT NOT NULL, lease TEXT NOT NULL,
                 stage TEXT NOT NULL, upload TEXT NOT NULL)""")

    def _pending(self) -> tuple[RemoteCaptureLease, str, str] | None:
        with closing(sqlite3.connect(self.root / "worker.sqlite3")) as db:
            row = _ROW.validate_python(
                db.execute(
                    """SELECT lease,stage,upload FROM captures
                    WHERE stage!='done' ORDER BY rowid LIMIT 1"""
                ).fetchone()
            )
        if row is None:
            return None
        lease = RemoteCaptureLease.model_validate_json(row[0])
        if lease.job.profile != self.profile:
            raise ValueError("remote_capture_pending_profile_changed")
        return lease, row[1], row[2]

    def _known(self, lease: RemoteCaptureLease) -> str | None:
        with closing(sqlite3.connect(self.root / "worker.sqlite3")) as db:
            row = _ROW.validate_python(
                db.execute(
                    "SELECT job_digest,lease,stage FROM captures WHERE operation=?",
                    (lease.job.operation_id,),
                ).fetchone()
            )
        if row is None:
            return None
        if (
            row[0] != contract_sha256(lease.job)
            or RemoteCaptureLease.model_validate_json(row[1]) != lease
        ):
            raise ValueError("remote_capture_lease_conflict")
        return row[2]

    def _save(self, lease: RemoteCaptureLease, stage: str, upload: str) -> None:
        with closing(sqlite3.connect(self.root / "worker.sqlite3", timeout=1)) as db, db:
            _ = db.execute(
                """INSERT INTO captures VALUES (?,?,?,?,?)
                ON CONFLICT(operation) DO UPDATE SET stage=excluded.stage,upload=excluded.upload""",
                (
                    lease.job.operation_id,
                    contract_sha256(lease.job),
                    lease.model_dump_json(),
                    stage,
                    upload,
                ),
            )

    def _prepare(self, lease: RemoteCaptureLease) -> tuple[Path, Path, Path]:
        job = lease.job
        query = urlencode({"lease_id": lease.lease_id})
        source = self.http.request(
            "GET", f"/workers/capture/jobs/{quote(job.operation_id, safe='')}/source?{query}"
        )
        encoded = source.get("image_base64")
        if (
            not isinstance(encoded, str)
            or len(encoded) > 14 * 1024 * 1024
            or source.get("sha256") != job.source.sha256
        ):
            raise ValueError("remote_capture_source_invalid")
        data = base64.b64decode(encoded, validate=True)
        if len(data) > _MAX_IMAGE:
            raise ValueError("remote_capture_source_too_large")
        root = self.root.resolve()
        job_root = root / contract_sha256({"operation": job.operation_id, "lease": lease.lease_id})
        job_root.mkdir(mode=0o700, exist_ok=True)
        if job_root.is_symlink() or job_root.resolve().parent != root:
            raise ValueError("remote_capture_job_path_invalid")
        background = job_root / job.contract.prepared_background.path
        if not background.resolve().is_relative_to(job_root):
            raise ValueError("remote_capture_source_path_invalid")
        background.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            fd = os.open(background, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            if background.is_symlink() or background.read_bytes() != data:
                raise ValueError("remote_capture_existing_source_invalid") from None
        else:
            with os.fdopen(fd, "wb") as file:
                _ = file.write(data)
        image = read_review_images((background,))[0]
        if image.sha256 != job.source.sha256:
            raise ValueError("remote_capture_source_digest_mismatch")
        output = job_root / "outputs/trace_wallpaper.png"
        if output.is_symlink() or not output.resolve().is_relative_to(job_root):
            raise ValueError("remote_capture_output_path_invalid")
        output.parent.mkdir(mode=0o700, exist_ok=True)
        return job_root, background, output

    @staticmethod
    def _identity(lease: RemoteCaptureLease) -> JsonObject:
        return {
            "operation_id": lease.job.operation_id,
            "lease_id": lease.lease_id,
            "job_sha256": contract_sha256(lease.job),
        }

    def _upload(self, lease: RemoteCaptureLease, upload: RemoteCaptureUpload) -> JsonObject:
        try:
            response = self.http.request(
                "POST",
                "/workers/capture/complete",
                {
                    "operation_id": lease.job.operation_id,
                    "upload": _JSON.validate_python(upload.model_dump(mode="json")),
                },
            )
            if response.get("accepted") is not True:
                return {"state": "upload_pending", "operation_id": lease.job.operation_id}
        except Exception:  # noqa: BLE001 - preserve uncertainty without exposing provider errors.
            return {"state": "upload_pending", "operation_id": lease.job.operation_id}
        self._save(lease, "done", upload.model_dump_json())
        return {"state": "done", "operation_id": lease.job.operation_id}

    def _uncertain(self, lease: RemoteCaptureLease) -> JsonObject:
        self._save(lease, "uncertain", "")
        try:
            response = self.http.request(
                "POST", "/workers/capture/uncertain", self._identity(lease)
            )
            reported = response.get("accepted") is True
        except Exception:  # noqa: BLE001 - preserve uncertainty without exposing provider errors.
            reported = False
        try:
            status = self.http.request(
                "GET",
                f"/workers/capture/jobs/{quote(lease.job.operation_id, safe='')}/status?"
                + urlencode({"lease_id": lease.lease_id}),
            )
            if (
                all(status.get(key) == value for key, value in self._identity(lease).items())
                and status.get("state") == "completed"
                and status.get("canonical_settled") is True
            ):
                self._save(lease, "done", "")
                return {"state": "done", "operation_id": lease.job.operation_id}
        except Exception:  # noqa: BLE001, S110 - retain uncertainty; provider errors may contain secrets.
            pass
        return {"state": "uncertain", "operation_id": lease.job.operation_id, "reported": reported}
