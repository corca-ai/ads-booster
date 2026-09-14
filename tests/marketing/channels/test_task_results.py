from __future__ import annotations

from typing import TYPE_CHECKING, override

import pytest

from ads_booster.agent.service.task_completion import TaskCompletionService
from ads_booster.agent.service.work_continuation import continue_work
from ads_booster.channels.http.http_api import MarketingAgentApi
from ads_booster.channels.http.jobs import AgentJobs
from ads_booster.channels.http.oauth import OAuthIdentity
from tests.marketing.agent_service.completion_fixtures import ScriptedAssessor
from tests.marketing.agent_service.test_application import (
    AskThenStopReasoning,
    build_service,
    reasoning_result,
    run_request,
)
from tests.marketing.agent_service.test_browser_login import login
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import receive, setup_events

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult
    from ads_booster.contracts.task_completion import (
        SemanticAssessmentRequest,
        SemanticAssessmentResult,
    )
    from ads_booster.transport.json_types import JsonObject

from ads_booster.agent.core.ports import CompletionRenderContext
from ads_booster.channels.task_results import BoundedCompletionRenderer, result_for
from ads_booster.contracts.agent_run import (
    AgentRecord,
    AgentRecordKind,
    ToolReceiptRecord,
    contract_sha256,
)
from ads_booster.contracts.task_completion import CompletionCandidate
from tests.marketing.agent_service.test_task_progress import make_run


def test_renderer_binds_exact_limited_answer_and_links_before_assessment() -> None:
    candidate = CompletionCandidate(
        candidate_id="one",
        task_id="task",
        task_revision=1,
        answer="a" * 100,
        answer_sha256=contract_sha256({"answer": "a" * 100}),
        result_links=("https://example.test/result",),
    )
    rendered = BoundedCompletionRenderer(max_chars=80).render(candidate)
    assert len(rendered.answer) == 80
    assert rendered.answer_sha256 == contract_sha256({"answer": rendered.answer})
    assert contract_sha256(rendered) != contract_sha256(candidate)
    assert BoundedCompletionRenderer(max_chars=80).render(rendered) == rendered


def test_browser_subject_match_does_not_grant_static_drive_authority(tmp_path: Path) -> None:
    service = build_service(tmp_path / "browser.sqlite3", AskThenStopReasoning(stop=True))
    jobs = AgentJobs(service)
    _ = MarketingAgentApi(
        service, "trace", "member", "static-token", browser_login=login(), jobs=jobs
    )
    assert jobs.drive_authorizer is not None
    with pytest.raises(RuntimeError, match="authorization_unavailable"):
        _ = jobs.drive_authorizer(OAuthIdentity("trace", "member"))


def test_rejected_candidate_is_not_presented_as_partial_success(tmp_path: Path) -> None:
    service = build_service(tmp_path / "rejected.sqlite3", LongAnswer(stop=True))
    service.completion = None
    run = service.create(run_request(), now=service.clock())
    result = result_for(run, service.repository.records(run.tenant_id, run.run_id))
    assert result.identity is None
    assert "a" * 100 not in result.text
    assert "남은 작업" in result.text


class LongAnswer(AskThenStopReasoning):
    @override
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        result = super().plan(request)
        return reasoning_result(
            request, result.decision.model_copy(update={"reasoning_summary": "a" * 3800})
        )


class RecordingAssessor(ScriptedAssessor):
    def __init__(self) -> None:
        self.requests: list[SemanticAssessmentRequest] = []

    @override
    def assess(self, request: SemanticAssessmentRequest) -> SemanticAssessmentResult:
        self.requests.append(request)
        return super().assess(request)


def test_actual_service_assesses_and_persists_rendered_exact_body(tmp_path: Path) -> None:
    service = build_service(tmp_path / "render.sqlite3", LongAnswer(stop=True))
    assessor = RecordingAssessor()
    service.completion = TaskCompletionService(service.repository, assessor)
    service.renderer = BoundedCompletionRenderer(max_chars=120)
    run = service.create(run_request(), now=service.clock())
    result = result_for(run, service.repository.records(run.tenant_id, run.run_id))
    assert result.identity is not None
    assert len(result.text) == 120
    assert assessor.requests[0].candidate.answer == result.text


def test_tampered_queued_body_is_not_delivered(tmp_path: Path) -> None:
    owner, messages = setup_events(tmp_path)
    receive(owner)
    assert owner.work_once(now=NOW)
    run = owner.commands.application.service.repository.list_runs("team")[0]
    assert owner.enqueue_run_update("team", run.run_id, event_id="queued")
    with owner.store.connect() as db:
        _ = db.execute(
            "UPDATE slack_message_jobs SET result='tampered' WHERE notification_state='pending'"
        )
    count = len(messages)
    assert owner.work_once(now=NOW)
    assert len(messages) == count


def test_new_task_suppresses_old_queued_completion_even_when_text_matches(tmp_path: Path) -> None:
    owner, messages = setup_events(tmp_path)
    receive(owner)
    assert owner.work_once(now=NOW)
    service = owner.commands.application.service
    run = service.repository.list_runs("team")[0]
    assert owner.enqueue_run_update("team", run.run_id, event_id="queued")
    _ = continue_work(
        service,
        "team",
        run.run_id,
        event_id="new-task",
        actor_id="member",
        note="Answer the new question",
        action="revise",
        now=NOW,
    )
    count = len(messages)
    assert owner.work_once(now=NOW)
    assert len(messages) == count


def test_only_selected_canonical_image_is_materialized_and_private_has_none() -> None:
    run = make_run()
    records: list[AgentRecord] = []
    evidence_refs: list[str] = []
    for index in (1, 2):
        output: JsonObject = {"artifact_sha256": str(index) * 64}
        receipt = ToolReceiptRecord(
            schema_version="trace.tool-receipt.v1",
            receipt_id=f"receipt-{index}",
            invocation_sha256="a" * 64,
            disposition="succeeded",
            actual_cost_units=1,
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
                "output": output,
            },
        )
        for offset, payload in enumerate(payloads):
            record = AgentRecord(
                schema_version="trace.agent-record.v1",
                record_id=f"record-{index}-{offset}",
                run_id=run.run_id,
                kind=AgentRecordKind.RECEIPT if offset == 0 else AgentRecordKind.EVIDENCE,
                payload_schema_version=str(payload["schema_version"]),
                payload=payload,
                payload_sha256=contract_sha256(payload),
                occurred_at=NOW,
            )
            records.append(record)
        evidence_refs.append(records[-1].payload_sha256)
    candidate = CompletionCandidate(
        candidate_id="candidate",
        task_id="task",
        task_revision=1,
        answer="image",
        answer_sha256=contract_sha256({"answer": "image"}),
        evidence_sha256s=(evidence_refs[0],),
        attachment_refs=("2" * 64,),
    )
    context = CompletionRenderContext(run, tuple(records))
    assert BoundedCompletionRenderer().render(candidate, context).attachment_refs == ("1" * 64,)
    assert (
        BoundedCompletionRenderer(include_links=False).render(candidate, context).attachment_refs
        == ()
    )
    assert (
        BoundedCompletionRenderer()
        .render(candidate.model_copy(update={"evidence_sha256s": ()}), context)
        .attachment_refs
        == ()
    )
