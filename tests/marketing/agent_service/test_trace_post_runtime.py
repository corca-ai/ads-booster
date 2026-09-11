# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
import json
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from importlib.resources import files
from pathlib import Path
from typing import Literal, cast

import pytest

from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.agent.runtime import SqliteSessionStore
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
from ads_booster.providers.codex_trace_post import (
    TracePostGeneratedImage,
    TracePostProviderResult,
)

from .test_application import NOW, _reasoning_result, _request
from .trace_post_test_fixture import build_completed_run


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


def setup(tmp_path: Path, provider: FakeProvider) -> TracePostTool:
    database = tmp_path / "service.sqlite3"
    descriptor = trace_post_descriptor(now=NOW)
    service = MarketingAgentService(
        repository=SqliteAgentRunRepository(database),
        registry=ToolRegistry(()),
        reasoning=TracePostReasoning(),
        tools={},
        runtime_store=SqliteSessionStore(database),
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


def test_approved_deferred_trace_post_ingests_six_assets_and_does_not_replay(
    tmp_path: Path,
) -> None:
    provider = FakeProvider()
    tool = setup(tmp_path, provider)
    assert tool.work_once()["state"] == "completed"
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


def test_provider_event_count_must_equal_frozen_workflow_receipts(tmp_path: Path) -> None:
    provider = FakeProvider(extra_image=True)
    tool = setup(tmp_path, provider)

    assert tool.work_once()["state"] == "completed"
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
    assert replace(tool).work_once()["state"] == "completed"
    assert provider.calls == 2
    assert replace(tool).work_once()["state"] == "uncertain"
    assert provider.calls == 2


def test_restart_recovers_a_completed_frozen_run_without_provider_replay(tmp_path: Path) -> None:
    provider = FakeProvider()
    tool = setup(tmp_path, provider)
    with closing(tool._db()) as database, database:
        row = cast(
            "tuple[str]",
            database.execute("SELECT data FROM trace_post_jobs WHERE settled=0").fetchone(),
        )
        raw = cast("dict[str, object]", json.loads(row[0]))
        workspace = Path(cast("str", raw["workspace"]))
    run = build_completed_run(workspace / "repo", "헬로키티", "카페 나무 테이블", "2026-09-11")
    proof = _provider_result(run)
    with closing(tool._db()) as database, database:
        _ = database.execute(
            "UPDATE trace_post_jobs SET stage='generated',provider_result=?",
            (_provider_result_json(proof, workspace),),
        )

    assert replace(tool).work_once()["state"] == "completed"
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
    with closing(tool._db()) as database, database:
        row = cast(
            "tuple[str]",
            database.execute("SELECT data FROM trace_post_jobs WHERE settled=0").fetchone(),
        )
        raw = cast("dict[str, object]", json.loads(row[0]))
        workspace = Path(cast("str", raw["workspace"]))
        _ = database.execute("UPDATE trace_post_jobs SET stage='started'")
    _ = build_completed_run(workspace / "repo", "헬로키티", "카페 나무 테이블", "2026-09-11")

    assert replace(tool).work_once()["state"] == "uncertain"
    assert provider.calls == 0


@pytest.mark.parametrize("damage", ["empty", "omitted"])
def test_restart_rejects_incomplete_snapshot_manifest_before_asset_ingest(
    tmp_path: Path, damage: Literal["empty", "omitted"]
) -> None:
    provider = FakeProvider()
    tool = setup(tmp_path, provider)
    with closing(tool._db()) as database, database:
        row = cast(
            "tuple[str]",
            database.execute("SELECT data FROM trace_post_jobs WHERE settled=0").fetchone(),
        )
        raw = cast("dict[str, object]", json.loads(row[0]))
        workspace = Path(cast("str", raw["workspace"]))
    run = build_completed_run(workspace / "repo", "헬로키티", "카페 나무 테이블", "2026-09-11")
    proof = _provider_result(run)
    with closing(tool._db()) as database, database:
        _ = database.execute(
            "UPDATE trace_post_jobs SET stage='generated',provider_result=?",
            (_provider_result_json(proof, workspace),),
        )
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
