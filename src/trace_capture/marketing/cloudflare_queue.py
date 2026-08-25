from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter, ValidationError

from trace_capture.marketing.models import MarketingTask, QueueLease, TaskCallback
from trace_capture.transport.json_types import JsonObject

if TYPE_CHECKING:
    from trace_capture.transport.http import HttpClient, HttpResponse

_DEFAULT_API_ROOT: Final = "https://api.cloudflare.com/client/v4"
_HTTP_SUCCESS_MIN: Final = 200
_HTTP_SUCCESS_MAX: Final = 300
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class CloudflareQueueError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CloudflareQueueConfig:
    account_id: str
    queue_id: str
    api_token: str
    api_root: str = _DEFAULT_API_ROOT
    batch_size: int = 5
    visibility_timeout_ms: int = 300_000

    @property
    def messages_url(self) -> str:
        base = self.api_root.rstrip("/")
        return f"{base}/accounts/{self.account_id}/queues/{self.queue_id}/messages"


@dataclass(frozen=True, slots=True)
class CloudflareQueueClient:
    http: HttpClient
    config: CloudflareQueueConfig

    def pull(self) -> tuple[QueueLease, ...]:
        response = self.http.post_json(
            f"{self.config.messages_url}/pull",
            {
                "batch_size": self.config.batch_size,
                "visibility_timeout_ms": self.config.visibility_timeout_ms,
            },
            self._headers(),
        )
        payload = _response_payload(response, operation="pull")
        result = payload.get("result", payload)
        if not isinstance(result, dict):
            raise CloudflareQueueError("queue pull result is not an object")
        messages = result.get("messages", [])
        if not isinstance(messages, list):
            raise CloudflareQueueError("queue pull messages is not an array")
        leases: list[QueueLease] = []
        for raw in messages:
            if not isinstance(raw, dict):
                raise CloudflareQueueError("queue message is not an object")
            body = raw.get("body")
            if not isinstance(body, dict):
                raise CloudflareQueueError("queue message body is not an object")
            try:
                leases.append(
                    QueueLease(
                        message_id=str(raw["id"]),
                        lease_id=str(raw["lease_id"]),
                        attempts=_integer(raw.get("attempts", 0), field="attempts"),
                        task=MarketingTask.model_validate(body),
                    )
                )
            except (KeyError, TypeError, ValueError, ValidationError) as error:
                raise CloudflareQueueError("queue message failed contract validation") from error
        return tuple(leases)

    def acknowledge(
        self,
        *,
        ack_lease_ids: tuple[str, ...] = (),
        retry_lease_ids: tuple[str, ...] = (),
    ) -> None:
        response = self.http.post_json(
            f"{self.config.messages_url}/ack",
            {
                "acks": [{"lease_id": lease_id} for lease_id in ack_lease_ids],
                "retries": [{"lease_id": lease_id} for lease_id in retry_lease_ids],
            },
            self._headers(),
        )
        _ = _response_payload(response, operation="acknowledge")

    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self.config.api_token}",
            "content-type": "application/json",
        }


@dataclass(frozen=True, slots=True)
class ControlPlaneCallbackClient:
    http: HttpClient
    control_plane_url: str
    worker_token: str

    def deliver(self, callback: TaskCallback) -> None:
        response = self.http.post_json(
            f"{self.control_plane_url.rstrip('/')}/v1/task-callbacks",
            _JSON_OBJECT.validate_json(callback.model_dump_json()),
            {
                "authorization": f"Bearer {self.worker_token}",
                "content-type": "application/json",
                "idempotency-key": callback.callback_id,
            },
        )
        _ = _response_payload(response, operation="callback")


def _response_payload(response: HttpResponse, *, operation: str) -> JsonObject:
    if not _HTTP_SUCCESS_MIN <= response.status_code < _HTTP_SUCCESS_MAX:
        raise CloudflareQueueError(
            f"Cloudflare {operation} failed with HTTP {response.status_code}"
        )
    try:
        return response.json_object()
    except ValidationError as error:
        raise CloudflareQueueError(f"Cloudflare {operation} returned invalid JSON") from error


def _integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise CloudflareQueueError(f"queue message {field} is not an integer")
    try:
        return int(value)
    except ValueError as error:
        raise CloudflareQueueError(f"queue message {field} is not an integer") from error
