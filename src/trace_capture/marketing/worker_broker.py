from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from trace_capture.marketing.cloudflare_queue import CloudflareQueueError
from trace_capture.marketing.models import MarketingTask, QueueLease, ReviewApproval, TaskCallback
from trace_capture.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from trace_capture.transport.http import HttpClient, HttpResponse

_HTTP_SUCCESS_MIN: Final = 200
_HTTP_SUCCESS_MAX: Final = 300
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class MacWorkerConfig(BaseModel):
    """Non-secret identity and routing written during one-time enrollment."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    worker_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)
    pool: str = Field(default="appium", min_length=1, max_length=40)
    control_plane_url: str = Field(pattern=r"^https://[^\s]+$")
    poll_seconds: float = Field(default=2.0, ge=0.5, le=60.0)

    @field_validator("control_plane_url")
    @classmethod
    def validate_control_plane_origin(cls, value: str) -> str:
        return normalize_control_plane_origin(value)


class MacWorkerCredential(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    worker_token: str = Field(min_length=1, max_length=512)


class MacWorkerEnrollment(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    worker_id: str
    worker_token: str
    display_name: str
    pool: str
    state: Literal["active"]


@dataclass(frozen=True, slots=True)
class MacWorkerStore:
    home: Path

    @property
    def directory(self) -> Path:
        return self.home / "marketing-worker"

    @property
    def config_path(self) -> Path:
        return self.directory / "config.json"

    @property
    def credential_path(self) -> Path:
        return self.directory / "credential.json"

    def save(self, config: MacWorkerConfig, credential: MacWorkerCredential) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        _atomic_private_write(self.config_path, config.model_dump_json(indent=2))
        _atomic_private_write(self.credential_path, credential.model_dump_json(indent=2))

    def load(self) -> tuple[MacWorkerConfig, MacWorkerCredential]:
        try:
            config = MacWorkerConfig.model_validate_json(self.config_path.read_text())
            credential = MacWorkerCredential.model_validate_json(self.credential_path.read_text())
        except (OSError, ValidationError) as error:
            message = f"Mac worker enrollment is unavailable under {self.directory}"
            raise CloudflareQueueError(message) from error
        return config, credential


@dataclass(frozen=True, slots=True)
class WorkerBrokerClient:
    """Worker-scoped D1 lease client; no Cloudflare account or Queue token is required."""

    http: HttpClient
    config: MacWorkerConfig
    credential: MacWorkerCredential
    heartbeat: Callable[[], JsonObject]

    def pull(self) -> tuple[QueueLease, ...]:
        payload = self.heartbeat()
        response = _post_json(
            self.http,
            self._url("/v1/workers/tasks/claim"),
            payload,
            self._headers(),
        )
        body = _response_payload(response, operation="worker task claim")
        raw_leases = body.get("leases", [])
        if not isinstance(raw_leases, list):
            raise CloudflareQueueError("worker task claim returned invalid leases")
        leases: list[QueueLease] = []
        for raw in raw_leases:
            if not isinstance(raw, dict):
                raise CloudflareQueueError("worker task claim returned an invalid lease")
            try:
                leases.append(
                    QueueLease(
                        message_id=str(raw["message_id"]),
                        lease_id=str(raw["lease_id"]),
                        attempts=_integer(raw.get("attempts", 0)),
                        task=MarketingTask.model_validate(raw["task"]),
                    )
                )
            except (KeyError, TypeError, ValueError, ValidationError) as error:
                raise CloudflareQueueError("worker lease failed contract validation") from error
        return tuple(leases)

    def acknowledge(
        self,
        *,
        ack_lease_ids: tuple[str, ...] = (),
        retry_lease_ids: tuple[str, ...] = (),
    ) -> None:
        response = _post_json(
            self.http,
            self._url("/v1/workers/tasks/ack"),
            {
                "acks": list(ack_lease_ids),
                "retries": list(retry_lease_ids),
            },
            self._headers(),
        )
        _ = _response_payload(response, operation="worker task acknowledgement")

    def deliver(self, callback: TaskCallback) -> None:
        response = _post_json(
            self.http,
            self._url("/v1/workers/task-callbacks"),
            _JSON_OBJECT.validate_json(callback.model_dump_json()),
            {
                **self._headers(),
                "idempotency-key": callback.callback_id,
            },
        )
        _ = _response_payload(response, operation="worker task callback")

    def deliver_approval(self, approval: ReviewApproval) -> None:
        _ = approval
        message = "D1 Mac workers do not deliver control-plane review approvals"
        raise CloudflareQueueError(message)

    def heartbeat_once(self) -> None:
        response = _post_json(
            self.http,
            self._url("/v1/workers/heartbeat"),
            self.heartbeat(),
            self._headers(),
        )
        _ = _response_payload(response, operation="worker heartbeat")

    def _url(self, path: str) -> str:
        return f"{self.config.control_plane_url.rstrip('/')}{path}"

    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self.credential.worker_token}",
            "content-type": "application/json",
        }


def enroll_mac_worker(
    http: HttpClient,
    *,
    control_plane_url: str,
    enrollment_code: str,
    heartbeat: JsonObject,
    poll_seconds: float = 2.0,
) -> tuple[MacWorkerConfig, MacWorkerCredential]:
    root = normalize_control_plane_origin(control_plane_url)
    response = _post_json(
        http,
        f"{root}/v1/workers/enroll",
        {"enrollment_code": enrollment_code, **heartbeat},
        {"content-type": "application/json"},
    )
    payload = _response_payload(response, operation="worker enrollment")
    try:
        enrolled = MacWorkerEnrollment.model_validate(payload)
        config = MacWorkerConfig(
            worker_id=enrolled.worker_id,
            display_name=enrolled.display_name,
            pool=enrolled.pool,
            control_plane_url=root,
            poll_seconds=poll_seconds,
        )
        credential = MacWorkerCredential(worker_token=enrolled.worker_token)
    except ValidationError as error:
        raise CloudflareQueueError("worker enrollment returned an invalid identity") from error
    return config, credential


def _atomic_private_write(path: Path, value: str) -> None:
    temporary = path.with_suffix(".tmp")
    _ = temporary.write_text(value, encoding="utf-8")
    temporary.chmod(0o600)
    _ = temporary.replace(path)


def _post_json(
    http: HttpClient,
    url: str,
    payload: JsonObject,
    headers: dict[str, str],
) -> HttpResponse:
    try:
        return http.post_json(url, payload, headers)
    except Exception as error:
        raise CloudflareQueueError("Mac worker transport request failed") from error


def _response_payload(response: HttpResponse, *, operation: str) -> JsonObject:
    if not _HTTP_SUCCESS_MIN <= response.status_code < _HTTP_SUCCESS_MAX:
        raise CloudflareQueueError(f"{operation} failed with HTTP {response.status_code}")
    try:
        return response.json_object()
    except ValidationError as error:
        raise CloudflareQueueError(f"{operation} returned invalid JSON") from error


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValueError
    return int(value)


def normalize_control_plane_origin(value: str) -> str:
    parsed = urlsplit(value.strip())
    try:
        _ = parsed.port
    except ValueError as error:
        raise ValueError("control plane URL must be an HTTPS origin") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("control plane URL must be an HTTPS origin")
    return f"https://{parsed.netloc}"
