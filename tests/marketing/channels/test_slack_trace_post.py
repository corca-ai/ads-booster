"""Trace post requests deliver the worker's six files back to their Slack thread."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from threading import Barrier
from typing import TYPE_CHECKING, override

import pytest
from pydantic import TypeAdapter

from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.agent.service.trace_post import (
    TracePostConfig,
    TracePostTool,
    trace_post_descriptor,
)
from ads_booster.contracts.agent_run import AgentRecordKind, AgentRunState
from ads_booster.contracts.creative_work import CreativeScope
from ads_booster.contracts.trace_post import TracePostSuccess
from ads_booster.creative.creative_assets import SqliteCreativeAssetRepository
from ads_booster.tools.completion_proofs import (
    CanonicalCompletionProofs,
    CompletionArtifactOwners,
)
from tests.marketing.agent_service.test_application import _reasoning_result
from tests.marketing.agent_service.test_trace_post_runtime import FakeProvider, TracePostReasoning
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import receive
from tests.marketing.channels.test_slack_images import configured

if TYPE_CHECKING:
    from urllib.request import Request

    from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult
    from ads_booster.providers.codex_trace_post import TracePostProviderResult


def upload_payload(request: Request) -> dict[str, object]:
    assert isinstance(request.data, bytes)
    return TypeAdapter(dict[str, object]).validate_json(request.data)


class RequestedTracePost(TracePostReasoning):
    @override
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        decision = super().plan(request).decision
        if decision.action == "invoke_tool":
            decision = decision.model_copy(
                update={
                    "authorization_message": request.current_user_message,
                    "tool_input": {
                        "schema_version": "trace.trace-post-input.v1",
                        "concept": "cute",
                        "motif": "헬로키티",
                        "place": "카페 나무 테이블",
                    },
                }
            )
        return _reasoning_result(request, decision)


@pytest.mark.parametrize("new_member", [False, True])
@pytest.mark.parametrize("damage", ["", "digest", "link", "lost", "disabled_member"])
def test_request_worker_completion_attaches_six_named_downloadable_images(  # noqa: PLR0915 - signed request through deferred worker, upload and restart.
    tmp_path: Path,
    new_member: bool,
    damage: str,
) -> None:
    owner, messages, uploads, _ = configured(tmp_path, lost=damage == "lost")
    owner.workspace_mentions = True
    service = owner.commands.application.service
    provider = FakeProvider()
    root = tmp_path / "artifacts"

    def completed(tenant: str, run: str, event: str) -> None:
        _ = owner.enqueue_run_update(tenant, run, event_id=event)

    tool = TracePostTool(
        service=service,
        assets=SqliteCreativeAssetRepository(service.repository.database_path, root),
        root=root / "trace-post",
        bundle=Path(str(files("ads_booster").joinpath("trace_post_bundle"))),
        provider=provider,
        config=TracePostConfig(tmp_path / "codex", "fixture", 3600),
        clock=lambda: NOW,
        on_completed=completed,
    )
    service.registry = ToolRegistry((trace_post_descriptor(now=NOW),))
    service.tools = {"creative.trace_post": tool}
    service.reasoning = RequestedTracePost()
    assert service.completion is not None
    service.completion = replace(
        service.completion,
        proof_reader=CanonicalCompletionProofs(
            service.repository,
            CompletionArtifactOwners(
                assets=tool.assets,
                scope_for_run=lambda run: CreativeScope(
                    workspace_id=run.tenant_id, product_id="trace"
                ),
            ),
        ),
    )
    receive(owner, user="UNEW" if new_member else "U1", text="<@UBOT> trace-post로 이미지 만들어줘")
    assert owner.work_once(now=NOW)
    run = service.repository.list_runs("team")[0]
    assert run.state is AgentRunState.AWAITING_TOOL
    assert not uploads
    assert tool.work_once()["state"] == "running"
    run = service.drive("team", run.run_id, now=NOW)
    assert run.state is AgentRunState.COMPLETED
    assert provider.calls == 1
    output = next(
        r.payload["output"]
        for r in service.repository.records("team", run.run_id)
        if r.kind is AgentRecordKind.EVIDENCE
        and r.payload.get("capability_id") == "creative.trace_post"
    )
    assert isinstance(output, dict)
    assert output["human_review_required"] is False
    reference = TracePostSuccess.model_validate(output).assets[0].asset
    asset = tool.assets.get(
        CreativeScope(workspace_id="team", product_id="trace"), reference.asset_id
    )
    assert asset is not None
    assert not any(item.method == "human_review" for item in asset.qa)
    if damage == "digest":
        _ = (root / asset.relative_path).write_bytes(b"changed")
    elif damage == "link":
        with owner.store.connect() as db:
            _ = db.execute("UPDATE creative_run_assets SET run_id='other-run'")
    elif damage == "disabled_member":
        identity = owner.identity("UNEW" if new_member else "U1")
        with owner.store.connect() as db:
            _ = db.execute(
                "UPDATE channel_identity_bindings SET binding_json=? WHERE binding_id=?",
                (
                    identity.model_copy(update={"can_create_runs": False}).model_dump_json(),
                    identity.binding_id,
                ),
            )
    assert owner.work_once(now=NOW)
    if damage:
        assert len(uploads) == (18 if damage == "lost" else 0)
        if damage in {"digest", "link"}:
            assert "검증에 실패" in str(messages[-1]["text"])
        elif damage == "lost":
            assert "확인하지 못했습니다" in str(messages[-1]["text"])
        restarted = replace(owner)
        restarted.recover()
        assert not restarted.work_once(now=NOW)
        assert len(uploads) == (18 if damage == "lost" else 0)
        assert provider.calls == 1
        return
    assert len(uploads) == 18
    allocations = [upload_payload(request) for request in uploads[::3]]
    assert {item["filename"] for item in allocations} == {
        f"trace-post-{country}-{role}.png"
        for country in ("kr", "jp", "tw")
        for role in ("final", "scene")
    }
    for request in uploads[2::3]:
        payload = upload_payload(request)
        assert payload["channel_id"] == "C1"
        assert payload["thread_ts"] == "100.001"
        assert "검토" not in str(payload["files"])
    answer = str(messages[-1]["text"])
    assert "다운로드" in answer
    assert "kr synthetic caption" in answer
    assert "jp synthetic caption" in answer
    assert "tw synthetic caption" in answer
    restarted = replace(owner)
    restarted.recover()
    assert not restarted.work_once(now=NOW)
    assert replace(tool).work_once()["state"] == "idle"
    assert provider.calls == 1
    assert len(uploads) == 18


def test_two_trace_posts_generate_in_parallel_without_reclaiming_active_jobs(
    tmp_path: Path,
) -> None:

    owner, messages, uploads, _ = configured(tmp_path)
    service = owner.commands.application.service
    barrier = Barrier(2)

    class ParallelProvider(FakeProvider):
        @override
        def run(
            self, *, workspace: Path, instruction: str, timeout_seconds: float
        ) -> TracePostProviderResult:
            _ = barrier.wait(timeout=8)
            return super().run(
                workspace=workspace, instruction=instruction, timeout_seconds=timeout_seconds
            )

    def completed(tenant: str, run: str, event: str) -> None:
        _ = owner.enqueue_run_update(tenant, run, event_id=event)

    provider = ParallelProvider()
    root = tmp_path / "artifacts"
    tool = TracePostTool(
        service=service,
        assets=SqliteCreativeAssetRepository(service.repository.database_path, root),
        root=root / "trace-post",
        bundle=Path(str(files("ads_booster").joinpath("trace_post_bundle"))),
        provider=provider,
        config=TracePostConfig(tmp_path / "codex", "fixture", 3600),
        clock=lambda: NOW,
        on_completed=completed,
    )
    service.registry = ToolRegistry((trace_post_descriptor(now=NOW),))
    service.tools = {"creative.trace_post": tool}
    service.reasoning = RequestedTracePost()
    assert service.completion is not None
    service.completion = replace(
        service.completion,
        proof_reader=CanonicalCompletionProofs(
            service.repository,
            CompletionArtifactOwners(
                assets=tool.assets,
                scope_for_run=lambda run: CreativeScope(
                    workspace_id=run.tenant_id, product_id="trace"
                ),
            ),
        ),
    )
    for ts in ("100.001", "200.001"):
        receive(owner, text="<@UBOT> trace-post로 이미지 만들어줘", ts=ts)
        assert owner.work_once(now=NOW)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(tool.work_once) for _ in range(2)]
        results = [job.result(timeout=15) for job in jobs]
    assert [result["state"] for result in results] == ["running", "running"]
    assert provider.calls == 2
    for run in service.repository.list_runs("team"):
        assert service.drive("team", run.run_id, now=NOW).state is AgentRunState.COMPLETED
    while owner.work_once(now=NOW):
        pass
    assert len(uploads) == 36
    assert all(run.state is AgentRunState.COMPLETED for run in service.repository.list_runs("team"))
    assert not tool._active_operations
    assert {str(message["thread_ts"]) for message in messages if "thread_ts" in message} == {
        "100.001",
        "200.001",
    }
