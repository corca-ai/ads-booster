from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from trace_capture.marketing.cloudflare_queue import (
    CloudflareQueueClient,
    CloudflareQueueConfig,
    CloudflareQueueError,
    ControlPlaneCallbackClient,
)
from trace_capture.marketing.models import (
    ApprovalDecision,
    ApprovalPhase,
    MarketingTask,
    ReviewApproval,
    TaskCallback,
    TaskKind,
    TaskResult,
    TaskStatus,
)
from trace_capture.transport.http import HttpResponse
from trace_capture.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Mapping

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


def _task_body() -> JsonObject:
    return _JSON_OBJECT.validate_json(
        MarketingTask(
            task_id="task-1",
            run_id="run-1",
            account_id="trace_kr",
            kind=TaskKind.RESEARCH,
            idempotency_key="run-1:research:once",
            payload={"country": "KR"},
            created_at=datetime.now(UTC),
        ).model_dump_json()
    )


@dataclass
class StubHttp:
    response: HttpResponse | None = None
    failure: Exception | None = None
    requests: list[tuple[str, JsonObject, Mapping[str, str]]] | None = None

    def get(self, url: str, headers: Mapping[str, str]) -> HttpResponse:
        _ = (url, headers)
        message = "unexpected GET"
        raise AssertionError(message)

    def post_json(
        self,
        url: str,
        payload: JsonObject,
        headers: Mapping[str, str],
    ) -> HttpResponse:
        if self.requests is not None:
            self.requests.append((url, payload, headers))
        if self.failure is not None:
            raise self.failure
        assert self.response is not None
        return self.response

    def post_form(
        self,
        url: str,
        form: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> HttpResponse:
        _ = (url, form, headers)
        message = "unexpected form request"
        raise AssertionError(message)


def _pull_response(body: object) -> HttpResponse:
    content = json.dumps(
        {
            "result": {
                "messages": [
                    {"id": "message-1", "lease_id": "lease-1", "attempts": 1, "body": body}
                ]
            }
        }
    ).encode()
    return HttpResponse(200, content, {})


@pytest.mark.parametrize("encoding", ["text", "legacy_base64"])
def test_pull_decodes_cloudflare_http_queue_bodies(encoding: str) -> None:
    serialized = json.dumps(_task_body())
    body = serialized if encoding == "text" else base64.b64encode(serialized.encode()).decode()
    client = CloudflareQueueClient(
        StubHttp(response=_pull_response(body)),
        CloudflareQueueConfig(
            account_id="cf-account",
            queue_id="queue",
            api_token="fixture",  # noqa: S106 - inert test credential.
        ),
    )

    leases = client.pull()

    assert leases[0].task.task_id == "task-1"
    assert leases[0].lease_id == "lease-1"


def test_callback_transport_failure_uses_retryable_boundary_error() -> None:
    task = MarketingTask.model_validate(_task_body())
    callback = TaskCallback(
        callback_id="task-1:completed",
        task_id=task.task_id,
        run_id=task.run_id,
        account_id=task.account_id,
        kind=task.kind,
        result=TaskResult(status=TaskStatus.SUCCEEDED),
        completed_at=datetime.now(UTC),
    )
    client = ControlPlaneCallbackClient(
        StubHttp(failure=OSError("network down")),
        control_plane_url="https://worker.example.test",
        worker_token="fixture",  # noqa: S106 - inert test credential.
    )

    with pytest.raises(CloudflareQueueError, match="transport request failed"):
        client.deliver(callback)


def test_review_approval_uses_worker_only_endpoint_and_idempotency_key() -> None:
    requests: list[tuple[str, JsonObject, Mapping[str, str]]] = []
    http = StubHttp(
        response=HttpResponse(202, json.dumps({"accepted": True}).encode(), {}),
        requests=requests,
    )
    client = ControlPlaneCallbackClient(
        http,
        control_plane_url="https://worker.example.test",
        worker_token="fixture",  # noqa: S106 - inert test credential.
    )
    approval = ReviewApproval(
        approval_id="run-1:candidates",
        run_id="run-1",
        account_id="trace_kr",
        phase=ApprovalPhase.CANDIDATES,
        decision=ApprovalDecision.APPROVED,
        candidate_ids=("candidate-1",),
        reviewed_at=datetime.now(UTC),
    )

    client.deliver_approval(approval)

    assert requests[0][0] == "https://worker.example.test/v1/review-events"
    assert requests[0][1]["approval_id"] == "run-1:candidates"
    assert requests[0][2]["idempotency-key"] == "run-1:candidates"
