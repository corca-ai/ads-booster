"""Worker-token-only transport; user, Slack and operator credentials grant no worker authority."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Protocol
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, ValidationError

from ads_booster.contracts.models import ContractModel
from ads_booster.marketing.agent_service.remote_capture_contract import RemoteCaptureUpload

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.contracts.agent_run import AgentRun
    from ads_booster.marketing.agent_service.remote_capture_contract import RemoteCaptureProfile
    from ads_booster.transport.json_types import JsonObject

_MAX_REQUEST_BYTES = 16 * 1024
MAX_CAPTURE_UPLOAD_BYTES = 14 * 1024 * 1024
_PREFIX = "/workers/capture/"
_SOURCE = re.compile(r"^/workers/capture/jobs/([A-Za-z0-9][A-Za-z0-9._:-]{0,159})/(source|status)$")


class CaptureApiOwner(Protocol):
    @property
    def profile(self) -> RemoteCaptureProfile: ...
    def authenticate(self, authorization: str | None) -> bool: ...
    def heartbeat(
        self, *, profile_sha256: str, ready: bool, reason_code: str | None, now: datetime
    ) -> None: ...
    def claim(self, *, now: datetime) -> ContractModel | None: ...
    def start(
        self, operation_id: str, *, lease_id: str, job_sha256: str, now: datetime
    ) -> JsonObject: ...
    def source(self, operation_id: str, *, lease_id: str, now: datetime) -> JsonObject: ...
    def status(self, operation_id: str, *, lease_id: str, now: datetime) -> JsonObject: ...
    def complete(
        self, operation_id: str, *, upload: RemoteCaptureUpload, now: datetime
    ) -> AgentRun: ...
    def uncertain(
        self, operation_id: str, *, lease_id: str, job_sha256: str, now: datetime
    ) -> AgentRun: ...


class Heartbeat(ContractModel):
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ready: bool
    reason_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,119}$")


class StartCapture(ContractModel):
    operation_id: str = Field(
        min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
    )
    lease_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    job_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CompleteCapture(ContractModel):
    operation_id: str = Field(
        min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
    )
    upload: RemoteCaptureUpload


class EmptyRequest(ContractModel):
    pass


def capture_body_limit(
    method: str, target: str, owner: CaptureApiOwner | None, authorization: str | None
) -> int | None:
    """Only authenticated exact completion receives the larger wire-body allowance."""
    if not urlsplit(target).path.startswith(_PREFIX):
        return None
    if method == "POST" and target == _PREFIX + "complete" and owner is not None:
        try:
            if owner.authenticate(authorization):
                return MAX_CAPTURE_UPLOAD_BYTES
        except Exception:  # noqa: BLE001 - authorization failures never expose credentials.
            return _MAX_REQUEST_BYTES
    return _MAX_REQUEST_BYTES


def dispatch_remote_capture(  # noqa: PLR0913,PLR0911,C901,PLR0912 - explicit worker route boundary.
    method: str,
    target: str,
    body: bytes,
    *,
    authorization: str | None,
    owner: CaptureApiOwner | None,
    now: datetime,
) -> tuple[int, JsonObject] | None:
    route = urlsplit(target)
    if not route.path.startswith(_PREFIX):
        return None
    if owner is None:
        return 404, {"error": "remote_capture_not_configured"}
    try:
        if not owner.authenticate(authorization):
            return 401, {"error": "remote_capture_unauthorized"}
        maximum = (
            MAX_CAPTURE_UPLOAD_BYTES
            if method == "POST" and route.path == _PREFIX + "complete"
            else _MAX_REQUEST_BYTES
        )
        if len(body) > maximum:
            return 413, {"error": "remote_capture_body_too_large"}
        if route.fragment:
            return 400, {"error": "remote_capture_request_invalid"}
        source = _SOURCE.fullmatch(route.path)
        if method == "GET" and source is not None:
            try:
                values = parse_qs(
                    route.query, strict_parsing=True, keep_blank_values=True, max_num_fields=1
                )
            except ValueError:
                return 400, {"error": "remote_capture_request_invalid"}
            if set(values) != {"lease_id"} or len(values["lease_id"]) != 1:
                return 400, {"error": "remote_capture_source_lease_required"}
            lease = values["lease_id"][0]
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", lease):
                return 400, {"error": "remote_capture_request_invalid"}
            if source[2] == "status":
                return 200, owner.status(source[1], lease_id=lease, now=now)
            return 200, owner.source(source[1], lease_id=lease, now=now)
        if route.query:
            return 400, {"error": "remote_capture_request_invalid"}
        if method == "GET" and route.path == _PREFIX + "profile":
            return 200, {"profile": owner.profile.model_dump(mode="json")}
        if method != "POST":
            return 405, {"error": "remote_capture_method_not_allowed"}
        return _post(owner, route.path, body, now)
    except ValidationError:
        return 400, {"error": "remote_capture_request_invalid"}
    except ValueError:
        return 409, {"error": "remote_capture_request_conflict"}
    except Exception:  # noqa: BLE001 - owner failures are sanitized and never automatically retried.
        return 503, {"error": "remote_capture_owner_unavailable"}


def _post(owner: CaptureApiOwner, path: str, body: bytes, now: datetime) -> tuple[int, JsonObject]:
    if path == _PREFIX + "heartbeat":
        heartbeat = Heartbeat.model_validate_json(body)
        owner.heartbeat(
            profile_sha256=heartbeat.profile_sha256,
            ready=heartbeat.ready,
            reason_code=heartbeat.reason_code,
            now=now,
        )
        return 200, {"accepted": True}
    if path == _PREFIX + "claim":
        _ = EmptyRequest.model_validate_json(body)
        lease = owner.claim(now=now)
        return 200, {"lease": None if lease is None else lease.model_dump(mode="json")}
    if path in {_PREFIX + "start", _PREFIX + "uncertain"}:
        request = StartCapture.model_validate_json(body)
        if path == _PREFIX + "start":
            return 200, owner.start(
                request.operation_id,
                lease_id=request.lease_id,
                job_sha256=request.job_sha256,
                now=now,
            )
        run = owner.uncertain(
            request.operation_id, lease_id=request.lease_id, job_sha256=request.job_sha256, now=now
        )
        return 200, {"accepted": True, "run_id": run.run_id, "state": run.state.value}
    if path == _PREFIX + "complete":
        completion = CompleteCapture.model_validate_json(body)
        run = owner.complete(completion.operation_id, upload=completion.upload, now=now)
        return 200, {"accepted": True, "run_id": run.run_id, "state": run.state.value}
    return 404, {"error": "remote_capture_route_not_found"}
