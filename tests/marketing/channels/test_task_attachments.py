from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from typing import TYPE_CHECKING

from PIL import Image

from ads_booster.agent.core.ports import CompletionRenderContext
from ads_booster.agent.service.task_progress import TaskProjection, task_records
from ads_booster.channels.slack_conversations import Conversation
from ads_booster.channels.slack_images import SlackImageDelivery
from ads_booster.channels.task_results import BoundedCompletionRenderer, attachment_records
from ads_booster.contracts.agent_run import (
    AgentRecord,
    AgentRecordKind,
    AgentRunState,
    ToolReceiptRecord,
    contract_sha256,
)
from ads_booster.contracts.task_completion import CompletionAssessment, ObligationAssessment
from ads_booster.tools.image_generation import CAPABILITY
from tests.marketing.agent_service.completion_fixtures import NOW, response_case
from tests.marketing.agent_service.test_github_issues import Response

if TYPE_CHECKING:
    from pathlib import Path
    from urllib.request import Request

    from ads_booster.transport.json_types import JsonObject


def test_delivery_owner_uploads_only_assessed_reference_and_empty_uploads_none(
    tmp_path: Path,
) -> None:
    case = response_case(tmp_path / "attachments.sqlite3")
    root = tmp_path / "images"
    root.mkdir()
    evidence: list[AgentRecord] = []
    image_bytes: list[bytes] = []
    refs: list[str] = []
    for index, color in enumerate(("blue", "red")):
        buffer = BytesIO()
        Image.new("RGB", (128, 128), color).save(buffer, format="PNG")
        data = buffer.getvalue()
        image_bytes.append(data)
        digest = sha256(data).hexdigest()
        _ = (root / f"{digest}.png").write_bytes(data)
        output: JsonObject = {"artifact_sha256": digest}
        receipt = ToolReceiptRecord(
            schema_version="trace.tool-receipt.v1",
            receipt_id=f"receipt-{index}",
            invocation_sha256="a" * 64,
            disposition="succeeded",
            actual_cost_units=0,
            output_schema_sha256="b" * 64,
            output_sha256=contract_sha256(output),
            executor_id="fixture",
            occurred_at=NOW,
        )
        payloads: tuple[JsonObject, ...] = (
            receipt.model_dump(mode="json"),
            {
                "schema_version": "trace.tool-output-evidence.v1",
                "receipt_sha256": contract_sha256(receipt),
                "capability_id": CAPABILITY,
                "output": output,
            },
        )
        for offset, payload in enumerate(payloads):
            evidence.append(
                AgentRecord(
                    schema_version="trace.agent-record.v1",
                    record_id=f"record-{index}-{offset}",
                    run_id=case.run.run_id,
                    kind=AgentRecordKind.RECEIPT if offset == 0 else AgentRecordKind.EVIDENCE,
                    payload_schema_version=str(payload["schema_version"]),
                    payload=payload,
                    payload_sha256=contract_sha256(payload),
                    occurred_at=NOW,
                )
            )
        refs.append(evidence[-1].payload_sha256)
    candidate = BoundedCompletionRenderer().render(
        case.candidate.model_copy(update={"evidence_sha256s": (refs[0],)}),
        CompletionRenderContext(case.run, tuple(evidence)),
    )
    assessment = CompletionAssessment(
        assessment_id="assessment",
        task_id=case.task.task_id,
        task_revision=1,
        task_spec_sha256=contract_sha256(case.task),
        candidate_sha256=contract_sha256(candidate),
        obligations=(
            ObligationAssessment(
                obligation_id="tip", status="satisfied", mechanism="fixture", reason="fixture"
            ),
        ),
        disposition="satisfied",
        reason="fixture",
    )
    checkpoint = case.checkpoint.model_copy(
        update={"candidate": candidate, "disposition": "satisfied"}
    )
    run = case.run.model_copy(update={"state": AgentRunState.COMPLETED})

    def records() -> tuple[AgentRecord, ...]:
        return (
            *evidence,
            *task_records(run, TaskProjection(case.task, checkpoint), NOW),
            AgentRecord(
                schema_version="trace.agent-record.v1",
                record_id="assessment",
                run_id=run.run_id,
                kind=AgentRecordKind.EVIDENCE,
                payload_schema_version=assessment.schema_version,
                payload=assessment.model_dump(mode="json"),
                payload_sha256=contract_sha256(assessment),
                occurred_at=NOW,
            ),
        )

    requests: list[Request] = []

    def opener(request: Request, *, timeout: float) -> Response:
        del timeout
        requests.append(request)
        if request.full_url.endswith("getUploadURLExternal"):
            return Response(
                {
                    "ok": True,
                    "file_id": "F123",
                    "upload_url": "https://files.slack.com/upload/test",
                },
                200,
            )
        return Response({"ok": True, "files": [{"id": "F123"}]}, 200)

    owner = SlackImageDelivery(root, case.repository.database_path, "fixture", opener)
    conversation = Conversation(
        conversation_id="one",
        tenant_id=run.tenant_id,
        channel_id="C1",
        thread_ts="1",
        owner_id="member",
        private=False,
        current_run=run.run_id,
    )
    _ = owner.deliver(attachment_records(run, records()), conversation)
    assert len(requests) == 3
    assert requests[1].data == image_bytes[0]
    candidate = candidate.model_copy(update={"attachment_refs": ()})
    checkpoint = checkpoint.model_copy(update={"candidate": candidate})
    assessment = assessment.model_copy(update={"candidate_sha256": contract_sha256(candidate)})
    _ = owner.deliver(
        attachment_records(run, records()),
        conversation.model_copy(update={"conversation_id": "two"}),
    )
    assert len(requests) == 3
