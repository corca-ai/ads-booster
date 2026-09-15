# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
import json
import sys
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast, override

import pytest
from pydantic import TypeAdapter

from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.agent.runtime import SqliteSessionStore
from ads_booster.agent.service import trace_post as trace_post_module
from ads_booster.agent.service.application import MarketingAgentService
from ads_booster.agent.service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.agent.service.trace_post import (
    TracePostConfig,
    TracePostTool,
    _provider_result_json,
    trace_post_descriptor,
)
from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentRecordKind,
    AgentRunState,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.contracts.reasoning import ReasoningDecision, ReasoningRequest, ReasoningResult
from ads_booster.creative.creative_assets import SqliteCreativeAssetRepository
from ads_booster.providers.codex_cli import CodexCliError
from ads_booster.providers.codex_image_edit import ImageEditProcessDiagnostic
from ads_booster.providers.codex_trace_post import (
    TracePostGeneratedImage,
    TracePostProviderResult,
)

from .test_application import NOW, _reasoning_result, _request
from .trace_post_test_fixture import build_completed_run

if TYPE_CHECKING:
    from collections.abc import Callable


class TracePostReasoning:
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        evidence = [
            item for item in request.evidence if item.get("capability_id") == "creative.trace_post"
        ]
        return _reasoning_result(
            request,
            ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="stop" if evidence else "invoke_tool",
                capability_id=None if evidence else "creative.trace_post",
                tool_input=None
                if evidence
                else {"schema_version": "trace.trace-post-input.v1", "concept": "cute"},
                expected_outcome="Six reviewed Trace post images and three captions",
                reasoning_summary="Use the installed frozen Trace post workflow",
            ),
        )


class FakeProvider:
    def __init__(self, *, fail: bool = False, extra_image: bool = False) -> None:
        self.calls: int = 0
        self.fail: bool = fail
        self.extra_image: bool = extra_image

    def run(
        self, *, workspace: Path, instruction: str, timeout_seconds: float
    ) -> TracePostProviderResult:
        self.calls += 1
        assert json.dumps(str(Path(sys.executable).resolve())) in instruction
        assert "Human feedback is optional" in instruction
        assert "without waiting for another approval or human review" in instruction
        assert "헬로키티" in instruction
        assert "카페 나무 테이블" in instruction
        assert timeout_seconds == 3600
        if self.fail:
            message = "synthetic provider uncertainty"
            raise RuntimeError(message)
        run = build_completed_run(workspace / "repo", "헬로키티", "카페 나무 테이블", "2026-09-11")
        result = _provider_result(run)
        if not self.extra_image:
            return result
        extra = workspace / "provider-images" / "extra.png"
        extra.parent.mkdir()
        _ = extra.write_bytes(result.images[0].path.read_bytes())
        return TracePostProviderResult(
            result.thread_id,
            result.turn_id,
            (
                *result.images,
                TracePostGeneratedImage(
                    "extra", extra, hashlib.sha256(extra.read_bytes()).hexdigest()
                ),
            ),
        )


def _provider_result(run: Path) -> TracePostProviderResult:
    paths = sorted((run / "synthetic-generated").glob("*.png"))
    return TracePostProviderResult(
        "thread-fixture",
        "turn-fixture",
        tuple(
            TracePostGeneratedImage(path.stem, path, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in paths
        ),
    )


def setup(tmp_path: Path, provider: FakeProvider, *, legacy_review: bool = False) -> TracePostTool:
    database = tmp_path / "service.sqlite3"
    descriptor = trace_post_descriptor(now=NOW)
    if legacy_review:
        schema = descriptor.output_schema
        value = schema
        for key in ("$defs", "TracePostSuccess", "properties", "human_review_required"):
            nested = value[key]
            assert isinstance(nested, dict)
            value = nested
        value["const"] = True
        _ = value.pop("default", None)
        descriptor = descriptor.model_copy(
            update={
                "output_schema": schema,
                "output_schema_sha256": contract_sha256(schema),
            }
        )
    service = MarketingAgentService(
        repository=SqliteAgentRunRepository(database),
        registry=ToolRegistry(()),
        reasoning=TracePostReasoning(),
        tools={},
        runtime_store=SqliteSessionStore(database),
        clock=lambda: NOW,
    )
    artifact_root = tmp_path / "artifacts"
    tool = TracePostTool(
        service=service,
        assets=SqliteCreativeAssetRepository(database, artifact_root),
        root=artifact_root / "trace-post",
        bundle=Path(str(files("ads_booster").joinpath("trace_post_bundle"))),
        provider=provider,
        config=TracePostConfig(tmp_path / "codex", "gpt-5.6-sol", 3600),
        clock=lambda: NOW,
    )
    service.registry = ToolRegistry((descriptor,))
    service.tools = {"creative.trace_post": tool}
    request = _request().model_copy(
        update={
            "tenant_id": "tenant-a",
            "budget": AgentBudget(max_tool_calls=4, max_cost_units=14),
        }
    )
    assert service.create(request, now=NOW).state is AgentRunState.AWAITING_APPROVAL
    _approve(service, "run-one")
    return tool


def _unsettled_workspace(tool: TracePostTool) -> Path:
    with closing(tool._db()) as database:
        row = cast(
            "tuple[str]",
            database.execute("SELECT data FROM trace_post_jobs WHERE settled=0").fetchone(),
        )
    raw = cast("dict[str, object]", json.loads(row[0]))
    return Path(cast("str", raw["workspace"]))


def _install_completed_provider_result(tool: TracePostTool) -> Path:
    workspace = _unsettled_workspace(tool)
    run = build_completed_run(workspace / "repo", "헬로키티", "카페 나무 테이블", "2026-09-11")
    proof = _provider_result(run)
    with closing(tool._db()) as database, database:
        _ = database.execute(
            "UPDATE trace_post_jobs SET stage='generated',provider_result=?",
            (_provider_result_json(proof, workspace),),
        )
    return run


def _approve(service: MarketingAgentService, run_id: str) -> None:
    invocation = ToolInvocation.model_validate(
        next(
            record.payload
            for record in service.repository.records("tenant-a", run_id)
            if record.kind is AgentRecordKind.INVOCATION
        )
    )
    assert (
        service.decide_approval(
            "tenant-a",
            run_id,
            approver_id="reviewer",
            granted=True,
            expected_invocation_sha256=contract_sha256(invocation),
            now=NOW,
            expires_at=NOW + timedelta(hours=2),
        ).state
        is AgentRunState.AWAITING_TOOL
    )


@pytest.mark.parametrize("legacy_review", [False, True])
def test_approved_deferred_trace_post_ingests_six_assets_and_does_not_replay(
    tmp_path: Path,
    legacy_review: bool,
) -> None:
    provider = FakeProvider()
    tool = setup(tmp_path, provider, legacy_review=legacy_review)
    assert tool.work_once()["state"] == "running"
    assert provider.calls == 1
    with closing(tool._db()) as database:
        linked = database.execute(
            "SELECT asset_id,revision FROM creative_run_assets WHERE tenant_id=? AND run_id=?",
            ("tenant-a", "run-one"),
        ).fetchall()
    assert len(linked) == 6
    assert replace(tool).work_once()["state"] == "idle"
    assert provider.calls == 1
    records = tool.service.repository.records("tenant-a", "run-one")
    receipts = [record for record in records if record.kind is AgentRecordKind.RECEIPT]
    assert len(receipts) == 1
    assert receipts[0].payload["actual_cost_units"] == 7
    evidence = next(
        r
        for r in records
        if r.kind is AgentRecordKind.EVIDENCE
        and r.payload.get("capability_id") == "creative.trace_post"
    )
    output = evidence.payload["output"]
    assert isinstance(output, dict)
    assert output["human_review_required"] is legacy_review


def test_provider_event_count_must_equal_frozen_workflow_receipts(tmp_path: Path) -> None:
    provider = FakeProvider(extra_image=True)
    tool = setup(tmp_path, provider)

    assert tool.work_once()["state"] == "running"
    records = tool.service.repository.records("tenant-a", "run-one")
    receipt = next(record for record in records if record.kind is AgentRecordKind.RECEIPT)
    assert receipt.payload["disposition"] == "failed"
    assert receipt.payload["actual_cost_units"] == 8
    with closing(tool._db()) as database:
        asset_count = cast(
            "tuple[int]",
            database.execute("SELECT COUNT(*) FROM creative_assets").fetchone(),
        )[0]
    assert asset_count == 0


def test_started_unknown_trace_post_is_not_replayed_after_restart(tmp_path: Path) -> None:
    provider = FakeProvider(fail=True)
    tool = setup(tmp_path, provider)
    assert tool.work_once()["state"] == "uncertain"
    assert provider.calls == 1
    provider.fail = False
    second = _request().model_copy(
        update={
            "run_id": "run-two",
            "tenant_id": "tenant-a",
            "budget": AgentBudget(max_tool_calls=4, max_cost_units=14),
        }
    )
    assert tool.service.create(second, now=NOW).state is AgentRunState.AWAITING_APPROVAL
    _approve(tool.service, "run-two")
    assert replace(tool).work_once()["state"] == "running"
    assert provider.calls == 2
    assert replace(tool).work_once()["state"] == "uncertain"
    assert provider.calls == 2


def test_restart_recovers_a_completed_frozen_run_without_provider_replay(tmp_path: Path) -> None:
    provider = FakeProvider()
    tool = setup(tmp_path, provider)
    _ = _install_completed_provider_result(tool)

    assert replace(tool).work_once()["state"] == "running"
    assert provider.calls == 0
    with closing(tool._db()) as database:
        linked = cast(
            "tuple[int]",
            database.execute(
                "SELECT COUNT(*) FROM creative_run_assets WHERE tenant_id=? AND run_id=?",
                ("tenant-a", "run-one"),
            ).fetchone(),
        )[0]
    assert linked == 6


def test_restart_does_not_invent_provider_proof_from_completed_files(tmp_path: Path) -> None:
    provider = FakeProvider()
    tool = setup(tmp_path, provider)
    workspace = _unsettled_workspace(tool)
    with closing(tool._db()) as database, database:
        _ = database.execute("UPDATE trace_post_jobs SET stage='started'")
    _ = build_completed_run(workspace / "repo", "헬로키티", "카페 나무 테이블", "2026-09-11")

    assert replace(tool).work_once()["state"] == "uncertain"
    assert provider.calls == 0


@pytest.mark.parametrize("completed", [True, False])
def test_provider_checkpoint_survives_exception_and_restart(
    tmp_path: Path, completed: bool
) -> None:
    class InterruptedProvider(FakeProvider):
        def run_checkpointed(
            self,
            *,
            workspace: Path,
            instruction: str,
            timeout_seconds: float,
            on_checkpoint: Callable[[TracePostProviderResult, bool], None] | None,
        ) -> TracePostProviderResult:
            result = self.run(
                workspace=workspace, instruction=instruction, timeout_seconds=timeout_seconds
            )
            assert on_checkpoint is not None
            on_checkpoint(result, completed)
            message = "lost provider return"
            raise RuntimeError(message)

    provider = InterruptedProvider()
    tool = setup(tmp_path, provider)
    assert tool.work_once()["state"] == "uncertain"
    with closing(tool._db()) as database:
        row = TypeAdapter[tuple[str | None, str | None] | None](
            tuple[str | None, str | None] | None
        ).validate_python(
            database.execute(
                "SELECT provider_progress,provider_result FROM trace_post_jobs"
            ).fetchone()
        )
    assert row is not None
    assert row[0] is not None
    assert (row[1] is not None) is completed
    assert replace(tool).work_once()["state"] == ("running" if completed else "uncertain")
    assert provider.calls == 1


def test_uncertain_job_recovers_saved_provider_proof_without_replay(tmp_path: Path) -> None:
    # Given complete owner proof but a lost completion transition.
    provider = FakeProvider()
    tool = setup(tmp_path, provider)
    _ = _install_completed_provider_result(tool)
    with closing(tool._db()) as database, database:
        _ = database.execute("UPDATE trace_post_jobs SET stage='uncertain'")
    # When the worker restarts.
    outcome = replace(tool).work_once()
    # Then existing files settle the original operation without a new generation.
    assert outcome["state"] == "running"
    assert provider.calls == 0
    records = tool.service.repository.records("tenant-a", "run-one")
    assert any(
        item.kind is AgentRecordKind.RECEIPT and item.payload["disposition"] == "succeeded"
        for item in records
    )


@pytest.mark.parametrize("damage", ["empty", "omitted"])
def test_restart_rejects_incomplete_snapshot_manifest_before_asset_ingest(
    tmp_path: Path, damage: Literal["empty", "omitted"]
) -> None:
    provider = FakeProvider()
    tool = setup(tmp_path, provider)
    run = _install_completed_provider_result(tool)
    manifest_path = run / "document-manifest.json"
    manifest = cast("dict[str, object]", json.loads(manifest_path.read_text(encoding="utf-8")))
    manifest_files = cast("dict[str, object]", manifest["files"])
    if damage == "empty":
        manifest_files.clear()
    else:
        _ = manifest_files.pop(next(iter(manifest_files)))
    _ = manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert replace(tool).work_once()["state"] == "uncertain"
    assert provider.calls == 0
    with closing(tool._db()) as database:
        asset_count = cast(
            "tuple[int]",
            database.execute("SELECT COUNT(*) FROM creative_assets").fetchone(),
        )[0]
    assert asset_count == 0
    assert replace(tool).work_once()["state"] == "idle"
    assert provider.calls == 0


@pytest.mark.parametrize("fail", [False, True])
def test_completion_notification_recovers_without_regenerating(tmp_path: Path, fail: bool) -> None:
    provider = FakeProvider(fail=fail)
    tool = setup(tmp_path, provider)
    events: list[str] = []

    def offline(_tenant: str, _run: str, _event: str) -> None:
        message = "synthetic notification unavailable"
        raise RuntimeError(message)

    tool.on_completed = offline
    with pytest.raises(RuntimeError, match="synthetic notification unavailable"):
        _ = tool.work_once()

    def online(_tenant: str, _run: str, event: str) -> None:
        events.append(event)

    recovered = replace(tool, on_completed=online)
    _ = recovered.work_once()
    assert len(events) == 1
    _ = recovered.work_once()
    assert len(events) == 1
    assert provider.calls == 1


def test_old_uncertain_job_gets_notification_after_upgrade_without_generation(
    tmp_path: Path,
) -> None:
    provider = FakeProvider(fail=True)
    tool = setup(tmp_path, provider)
    assert tool.work_once()["state"] == "uncertain"
    with closing(tool._db()) as db, db:
        _ = db.execute("ALTER TABLE trace_post_jobs DROP COLUMN notified")
        _ = db.execute("ALTER TABLE trace_post_jobs DROP COLUMN failure_diagnostic")
    events: list[str] = []

    def completed(_tenant: str, _run: str, event: str) -> None:
        events.append(event)

    upgraded = replace(tool, on_completed=completed)
    assert upgraded.work_once()["state"] == "uncertain"
    assert len(events) == 1
    assert "uncertain" in events[0]
    assert upgraded.work_once()["state"] == "uncertain"
    assert len(events) == 1
    assert provider.calls == 1


def test_provider_failure_reason_survives_restart_without_raw_output(tmp_path: Path) -> None:
    class LauncherFailure(FakeProvider):
        calls: int

        @override
        def run(
            self, *, workspace: Path, instruction: str, timeout_seconds: float
        ) -> TracePostProviderResult:
            self.calls += 1
            diagnostic = ImageEditProcessDiagnostic(
                phase="terminal",
                terminal="failed",
                command_success=3,
                command_nonzero=1,
                agent_message_count=1,
                agent_message_max_length="201_1000",
                turn_status="completed",
                items_view="full",
            )
            path = workspace / "codex-image-edit-diagnostic.json"
            _ = path.write_text(diagnostic.model_dump_json(), encoding="utf-8")
            path.chmod(0o600)
            run = workspace / "repo/output/posts/diagnostic"
            (run / "receipts").mkdir(parents=True)
            for name, value in (
                ("run.json", {}),
                ("package.json", {}),
                ("localization.json", {"padding": "x" * 12_000}),
                ("assembly-check.json", {"passed": True}),
                ("content-review.json", {"decision": "approved"}),
            ):
                _ = (run / name).write_text(json.dumps(value), encoding="utf-8")
            _ = (run / "receipts/one.json").write_text(
                json.dumps({"status": "completed", "prompt": "TOP SECRET RECEIPT"}),
                encoding="utf-8",
            )
            code = "codex_sandbox_launcher_unavailable"
            raise CodexCliError(code)

    provider = LauncherFailure()
    tool = setup(tmp_path, provider)
    assert tool.work_once()["state"] == "uncertain"
    recovered = replace(tool)
    assert recovered.work_once()["state"] == "uncertain"
    records = tool.service.repository.records("tenant-a", "run-one")
    diagnostics = [
        r for r in records if r.payload_schema_version == "trace.deferred-provider-failure.v1"
    ]
    assert len(diagnostics) == 1
    assert diagnostics[0].payload["reason_code"] == "codex_sandbox_launcher_unavailable"
    assert provider.calls == 1
    with closing(tool._db()) as db:
        raw = cast(
            "tuple[str]",
            db.execute("SELECT failure_diagnostic FROM trace_post_jobs").fetchone(),
        )[0]
    failure_diagnostic = cast("dict[str, object]", json.loads(raw))
    provider_record = cast("dict[str, object]", failure_diagnostic["provider"])
    workspace_record = cast("dict[str, object]", failure_diagnostic["workspace"])
    assert provider_record["command_success"] == 3
    assert provider_record["command_nonzero"] == 1
    assert workspace_record["run_directories"] == 1
    assert workspace_record["localization"] == "present"
    assert workspace_record["assembly"] == "passed"
    assert workspace_record["content_review"] == "approved"
    assert workspace_record["receipt_completed"] == 1
    assert "instruction" not in raw
    assert "TOP SECRET RECEIPT" not in raw


def test_workspace_diagnostic_failure_keeps_the_sanitized_provider_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostic = ImageEditProcessDiagnostic(
        phase="turn",
        terminal="failed",
        command_nonzero=1,
    )
    path = tmp_path / "codex-image-edit-diagnostic.json"
    _ = path.write_text(diagnostic.model_dump_json(by_alias=True), encoding="utf-8")

    def unavailable(_workspace: Path) -> object:
        raise PermissionError

    monkeypatch.setattr(trace_post_module, "_collect_workspace_milestones", unavailable)
    raw = trace_post_module._failure_diagnostic(tmp_path)
    value = cast("dict[str, object]", json.loads(raw))
    provider = cast("dict[str, object]", value["provider"])
    workspace = cast("dict[str, object]", value["workspace"])
    assert provider["command_nonzero"] == 1
    assert workspace["collection"] == "unavailable"
