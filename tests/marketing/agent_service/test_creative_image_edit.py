# pyright: reportPrivateUsage=false
"""Real deferred service and SQLite edit queue with fake paid image generation."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, cast

import jsonschema
import pytest

from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentRecordKind,
    AgentRunState,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.contracts.creative_work import CreativeAsset, CreativeScope
from ads_booster.contracts.reasoning import ReasoningDecision, ReasoningRequest, ReasoningResult
from ads_booster.contracts.tool_capability import ToolExecutionResult
from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.application import MarketingAgentService
from ads_booster.marketing.agent_service.creative_assets import SqliteCreativeAssetRepository
from ads_booster.marketing.agent_service.creative_image_edit import (
    CreativeImageEditTool,
    ImageEditConfig,
    ImageEditJob,
    image_edit_descriptor,
)
from ads_booster.marketing.agent_service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.marketing.agent_service.work_continuation import continue_work
from ads_booster.marketing.runtime import SqliteSessionStore
from ads_booster.providers.codex_cli import CodexCliError, ReviewImage, read_review_images
from ads_booster.providers.codex_image_edit import ImageEditResult
from tests.marketing.agent_service.test_application import _reasoning_result, _request
from tests.marketing.agent_service.test_creative_capture import NOW, png, setup_tool

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject


class Reasoning:
    def __init__(self, payload: JsonObject) -> None:
        self.payload: JsonObject = payload

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        done = any(item.get("capability_id") == "creative.image.edit" for item in request.evidence)
        return _reasoning_result(
            request,
            ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="stop" if done else "invoke_tool",
                capability_id=None if done else "creative.image.edit",
                tool_input=None if done else self.payload,
                reasoning_summary="Bounded top extension",
                expected_outcome="Original remains preserved",
            ),
        )


class Provider:
    def __init__(self) -> None:
        self.calls: int = 0
        self.fail: bool = False
        self.hook: Callable[[], None] = lambda: None

    def generate(
        self,
        *,
        operation_id: str,
        workspace: Path,
        prompt: str,
        images: tuple[ReviewImage, ...],
        timeout_seconds: float,
    ) -> ImageEditResult:
        _ = operation_id, timeout_seconds
        self.calls += 1
        self.hook()
        if self.fail:
            message = "unknown generation result"
            raise RuntimeError(message)
        path = workspace / "generated.png"
        _ = path.write_bytes(png())
        return ImageEditResult(
            path=path,
            sha256=hashlib.sha256(png()).hexdigest(),
            event_id="image-event",
            prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
            source_sha256s=tuple(i.sha256 for i in images),
            thread_id="thread",
            turn_id="turn",
        )


def setup(tmp_path: Path, *, max_cost: int = 40) -> tuple[CreativeImageEditTool, Provider]:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    seed, _, _ = setup_tool(fixture)
    run = seed.repository.get("tenant-a", "run-a")
    assert run is not None
    asset = seed.assets.get(seed.scope_for_run(run), "background")
    assert asset is not None
    db = tmp_path / "state.db"
    assets = SqliteCreativeAssetRepository(db, tmp_path / "artifacts")
    _ = (assets.artifact_root / asset.relative_path).write_bytes(png())
    assets.add(asset, actor_scope=asset.scope)
    payload: JsonObject = {
        "source": {"asset_id": asset.asset_id, "revision": asset.revision, "sha256": asset.sha256},
        "operation": "extend_top",
        "instruction": "Extend calm top background",
        "top_pixels": 8,
        "preserve": ["Character"],
    }
    service = MarketingAgentService(
        repository=SqliteAgentRunRepository(db),
        registry=ToolRegistry(()),
        reasoning=Reasoning(payload),
        tools={},
        runtime_store=SqliteSessionStore(db),
    )
    provider = Provider()
    config = ImageEditConfig(model_id="configured-model", executable="/explicit/codex")
    tool = CreativeImageEditTool(
        service,
        assets,
        assets.artifact_root / "image-edits",
        provider,
        config,
        lambda: True,
        clock=lambda: NOW,
    )
    service.registry = ToolRegistry(
        (
            image_edit_descriptor(
                config=config, capability_id="creative.image.edit", now=NOW, ready=True
            ),
        )
    )
    service.tools = {"creative.image.edit": tool}
    request = _request().model_copy(
        update={
            "tenant_id": "tenant-a",
            "budget": AgentBudget(max_tool_calls=4, max_cost_units=max_cost),
        }
    )
    assert service.create(request, now=NOW).state is AgentRunState.AWAITING_APPROVAL
    return tool, provider


def approve(tool: CreativeImageEditTool) -> None:
    invocation = ToolInvocation.model_validate(
        next(
            r.payload
            for r in tool.service.repository.records("tenant-a", "run-one")
            if r.kind is AgentRecordKind.INVOCATION
        )
    )
    assert (
        tool.service.decide_approval(
            "tenant-a",
            "run-one",
            approver_id="reviewer",
            granted=True,
            expected_invocation_sha256=contract_sha256(invocation),
            now=NOW,
            expires_at=NOW + timedelta(minutes=10),
        ).state
        is AgentRunState.AWAITING_TOOL
    )


def test_exact_approval_to_composed_asset_and_one_generation(tmp_path: Path) -> None:
    tool, provider = setup(tmp_path)
    assert tool.work_once()["state"] == "idle"
    approve(tool)
    assert provider.calls == 0
    assert tool.work_once()["state"] == "completed"
    assert replace(tool).work_once()["state"] == "idle"
    assert provider.calls == 1
    records = tool.service.repository.records("tenant-a", "run-one")
    assert len([r for r in records if r.kind is AgentRecordKind.RECEIPT]) == 1
    outputs = tuple(tool.root.glob("*/composed.png"))
    assert len(outputs) == 1
    assert read_review_images(outputs)[0].height == 72


def test_unknown_generation_on_restart_is_never_repeated(tmp_path: Path) -> None:
    tool, provider = setup(tmp_path)
    approve(tool)
    provider.fail = True
    assert tool.work_once()["state"] == "uncertain"
    assert replace(tool).work_once()["state"] == "idle"
    assert provider.calls == 1


def test_callback_failure_replays_only_projection(tmp_path: Path) -> None:
    tool, provider = setup(tmp_path)
    approve(tool)

    def fail(tenant: str, run: str, event: str) -> None:
        _ = tenant, run, event
        message = "synthetic callback failure"
        raise RuntimeError(message)

    tool.on_completed = fail
    with pytest.raises(RuntimeError, match="callback failure"):
        _ = tool.work_once()
    tool.on_completed = None
    assert replace(tool).work_once()["state"] == "completed"
    assert provider.calls == 1


def test_expired_approval_no_effect_without_generation(tmp_path: Path) -> None:
    tool, provider = setup(tmp_path)
    approve(tool)
    tool.clock = lambda: NOW + timedelta(hours=1)
    assert tool.work_once()["state"] == "completed"
    assert provider.calls == 0


def test_source_bytes_changed_after_generation_fail_without_asset(tmp_path: Path) -> None:
    tool, provider = setup(tmp_path)
    approve(tool)

    def corrupt() -> None:
        _ = (tool.assets.artifact_root / "background.png").write_bytes(b"changed")

    provider.hook = corrupt
    assert tool.work_once()["state"] == "completed"
    assert provider.calls == 1
    assert not tuple(tool.root.glob("*/composed.png"))
    receipts = [
        r.payload
        for r in tool.service.repository.records("tenant-a", "run-one")
        if r.kind is AgentRecordKind.RECEIPT
    ]
    assert receipts[0]["disposition"] == "failed"


def test_generation_releases_service_lock_for_other_work(tmp_path: Path) -> None:
    tool, provider = setup(tmp_path)
    approve(tool)
    acquired = threading.Event()

    def probe() -> None:
        def lock() -> None:
            with tool.service.execution_lock:
                acquired.set()

        thread = threading.Thread(target=lock)
        thread.start()
        assert acquired.wait(1)
        thread.join(1)

    provider.hook = probe
    assert tool.work_once()["state"] == "completed"


def test_pause_while_queued_settles_without_generation(tmp_path: Path) -> None:
    tool, provider = setup(tmp_path)
    approve(tool)
    _ = continue_work(
        tool.service,
        "tenant-a",
        "run-one",
        event_id="stop",
        actor_id="reviewer",
        note="Pause",
        action="pause",
        now=NOW,
    )
    assert tool.work_once()["state"] == "awaiting_input"
    assert provider.calls == 0


def test_started_process_loss_is_reconciliation_without_provider_replay(tmp_path: Path) -> None:
    tool, provider = setup(tmp_path)
    approve(tool)
    with closing(sqlite3.connect(tool.service.repository.database_path)) as db, db:
        _ = db.execute("UPDATE image_edit_jobs SET stage='started'")
    assert replace(tool).work_once()["state"] == "uncertain"
    assert provider.calls == 0
    assert replace(tool).work_once()["state"] == "idle"


def test_workspace_parent_rebound_after_generation_never_writes_outside_root(
    tmp_path: Path,
) -> None:
    tool, provider = setup(tmp_path)
    approve(tool)
    outside = tmp_path / "outside"
    outside.mkdir()

    def redirect() -> None:
        operation = next(tool.root.iterdir()).name
        _ = tool.root.rename(tool.root.with_name("original-image-edits"))
        (outside / operation).mkdir()
        tool.root.symlink_to(outside, target_is_directory=True)

    provider.hook = redirect
    assert tool.work_once()["state"] == "completed"
    assert not tuple(outside.glob("*/composed.png"))
    receipts = [
        r.payload
        for r in tool.service.repository.records("tenant-a", "run-one")
        if r.kind is AgentRecordKind.RECEIPT
    ]
    assert receipts[0]["disposition"] == "failed"


def test_private_tenant_is_denied_before_asset_or_approval_access(tmp_path: Path) -> None:
    tool, provider = setup(tmp_path)
    invocation = ToolInvocation.model_validate(
        next(
            r.payload
            for r in tool.service.repository.records("tenant-a", "run-one")
            if r.kind is AgentRecordKind.INVOCATION
        )
    ).model_copy(update={"tenant_id": "slack-private-fixture"})
    descriptor = image_edit_descriptor(
        config=tool.config, capability_id="creative.image.edit", now=NOW, ready=True
    )
    with pytest.raises(ValueError, match="image_edit_private_scope_denied"):
        _ = tool.execute(invocation, descriptor)
    assert provider.calls == 0


def test_uncertainty_projection_crash_is_repaired_without_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, provider = setup(tmp_path)
    approve(tool)
    provider.fail = True

    def unavailable(*args: object, **kwargs: object) -> None:
        _ = args, kwargs
        message = "fixture projection crash"
        raise RuntimeError(message)

    with monkeypatch.context() as scoped:
        scoped.setattr(type(tool.service), "mark_deferred_uncertain", unavailable)
        with pytest.raises(RuntimeError, match="projection crash"):
            _ = tool.work_once()
    _ = replace(tool).work_once()
    run = tool.service.repository.get("tenant-a", "run-one")
    assert run is not None
    assert run.state is AgentRunState.AWAITING_RECONCILIATION
    assert provider.calls == 1


@pytest.mark.parametrize("after_generation", [False, True])
def test_known_decode_failure_has_terminal_effect_classification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    after_generation: bool,
) -> None:
    tool, provider = setup(tmp_path)
    approve(tool)

    def decode(paths: tuple[Path, ...]) -> tuple[ReviewImage, ...]:
        if not after_generation or paths[0].name == "composed.png":
            message = "fixture image size or decode limit"
            raise CodexCliError(message)
        return read_review_images(paths)

    monkeypatch.setattr(
        "ads_booster.marketing.agent_service.creative_image_edit.read_review_images",
        decode,
    )
    assert tool.work_once()["state"] == "completed"
    assert provider.calls == (1 if after_generation else 0)
    receipts = [
        r.payload
        for r in tool.service.repository.records("tenant-a", "run-one")
        if r.kind is AgentRecordKind.RECEIPT
    ]
    assert receipts[0]["disposition"] == ("failed" if after_generation else "no_effect")


def test_output_contract_rejects_arbitrary_object(tmp_path: Path) -> None:
    tool, _ = setup(tmp_path)
    schema = image_edit_descriptor(
        config=tool.config, capability_id="creative.image.edit", now=NOW, ready=True
    ).output_schema
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"unverified": True}, schema)


def test_frozen_prompt_contains_measured_canvas(tmp_path: Path) -> None:
    tool, _ = setup(tmp_path)
    approve(tool)
    with closing(sqlite3.connect(tool.service.repository.database_path)) as db:
        row = cast("tuple[str] | None", db.execute("SELECT data FROM image_edit_jobs").fetchone())
    assert row is not None
    job = ImageEditJob.model_validate_json(row[0])
    assert '"output_canvas": {"width": 32, "height": 72}' in job.prompt


def test_uncertainty_projection_failure_does_not_starve_next_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, provider = setup(tmp_path)
    approve(tool)
    provider.fail = True

    def unavailable(*args: object, **kwargs: object) -> None:
        _ = args, kwargs
        message = "fixture projection unavailable"
        raise RuntimeError(message)

    monkeypatch.setattr(type(tool.service), "mark_deferred_uncertain", unavailable)
    with pytest.raises(RuntimeError, match="projection unavailable"):
        _ = tool.work_once()
    request = _request().model_copy(
        update={
            "tenant_id": "tenant-a",
            "run_id": "run-two",
            "budget": AgentBudget(max_tool_calls=4, max_cost_units=40),
        }
    )
    assert tool.service.create(request, now=NOW).state is AgentRunState.AWAITING_APPROVAL
    invocation = ToolInvocation.model_validate(
        next(
            r.payload
            for r in tool.service.repository.records("tenant-a", "run-two")
            if r.kind is AgentRecordKind.INVOCATION
        )
    )
    _ = tool.service.decide_approval(
        "tenant-a",
        "run-two",
        approver_id="reviewer",
        granted=True,
        expected_invocation_sha256=contract_sha256(invocation),
        now=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    provider.fail = False
    assert tool.work_once()["state"] == "completed"
    assert provider.calls == 2
    assert tool.work_once()["state"] == "reconciliation_projection_pending"
    assert provider.calls == 2


def test_previous_queue_schema_preserves_jobs_when_adding_projection_marker(tmp_path: Path) -> None:
    tool, provider = setup(tmp_path)
    approve(tool)
    with closing(sqlite3.connect(tool.service.repository.database_path)) as db, db:
        _ = db.execute("ALTER TABLE image_edit_jobs DROP COLUMN uncertain_projected")
        _ = db.execute("ALTER TABLE image_edit_jobs DROP COLUMN uncertainty_attempts")
    restarted = replace(tool)
    assert restarted.work_once()["state"] == "completed"
    assert provider.calls == 1


class LocaleReasoning:
    def __init__(self, payload: JsonObject, capability: str, expected_locale: str) -> None:
        self.payload: JsonObject = payload
        self.capability: str = capability
        self.expected_locale: str = expected_locale

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        done = False
        for evidence in request.evidence:
            output = evidence.get("output")
            asset = output.get("asset") if isinstance(output, dict) else None
            if (
                isinstance(asset, dict)
                and asset.get("locale") == self.expected_locale
                and asset.get("parents") == [self.payload["source"]]
            ):
                done = True
        return _reasoning_result(
            request,
            ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="stop" if done else "invoke_tool",
                capability_id=None if done else self.capability,
                tool_input=None if done else self.payload,
                reasoning_summary="Fixture locale-scoped revision",
                expected_outcome="One locale asset",
            ),
        )


def test_japanese_english_and_japanese_only_followup_keep_same_run_and_sibling(
    tmp_path: Path,
) -> None:
    tool, provider = setup(tmp_path, max_cost=100)
    scope = CreativeScope(workspace_id="tenant-a", product_id="trace")
    original = tool.assets.get(scope, "background")
    assert original is not None
    parent: JsonObject = {
        "asset_id": original.asset_id,
        "revision": original.revision,
        "sha256": original.sha256,
    }
    tool.service.registry = ToolRegistry(
        tuple(
            image_edit_descriptor(config=tool.config, capability_id=capability, now=NOW, ready=True)
            for capability in ("creative.image.edit", "creative.image.localize")
        )
    )
    tool.service.tools = {"creative.image.edit": tool, "creative.image.localize": tool}
    produced: list[CreativeAsset] = []
    for index, (locale, expected_locale, text) in enumerate(
        (
            ("ja", "ja", "予定"),
            ("en", "en", "Schedule"),
            (None, "ja", "新しい予定"),
        )
    ):
        if index == 2:
            japanese = produced[0]
            parent = {
                "asset_id": japanese.asset_id,
                "revision": japanese.revision,
                "sha256": japanese.sha256,
            }
        payload: JsonObject = {
            "source": parent,
            "operation": "replace_regions",
            "instruction": "Title only",
            "regions": [
                {
                    "x": 0,
                    "y": 0,
                    "width": 12,
                    "height": 8,
                    "instruction": "Replace title",
                    "exact_text": text,
                }
            ],
            "locale": locale,
        }
        capability = "creative.image.localize" if locale else "creative.image.edit"
        tool.service.reasoning = LocaleReasoning(payload, capability, expected_locale)
        _ = continue_work(
            tool.service,
            "tenant-a",
            "run-one",
            event_id=f"locale-{index}",
            actor_id="reviewer",
            note="Fixture requested language edit",
            action="revise",
            now=NOW,
        )
        records = tool.service.repository.records("tenant-a", "run-one")
        invocation = ToolInvocation.model_validate(
            next(r.payload for r in reversed(records) if r.kind is AgentRecordKind.INVOCATION)
        )
        _ = tool.service.decide_approval(
            "tenant-a",
            "run-one",
            approver_id="reviewer",
            granted=True,
            expected_invocation_sha256=contract_sha256(invocation),
            now=NOW,
            expires_at=NOW + timedelta(minutes=10),
        )
        assert tool.work_once()["state"] == "completed"
        asset = tool.assets.get(scope, "image-edit-" + contract_sha256(invocation)[:48])
        assert asset is not None
        assert asset.locale == expected_locale
        assert all(qa.locale == expected_locale for qa in asset.qa)
        assert not asset.product_proof_verified
        produced.append(asset)
    assert provider.calls == 3
    assert tool.assets.get(scope, produced[1].asset_id) == produced[1]
    assert not tool.assets.is_stale(scope, produced[1].asset_id)
    tool.assets.add(produced[0].model_copy(update={"revision": 2}), actor_scope=scope)
    assert tool.assets.is_stale(scope, produced[2].asset_id)
    assert not tool.assets.is_stale(scope, produced[1].asset_id)


def test_canonical_terminal_readback_releases_uncertain_queue_without_generation(
    tmp_path: Path,
) -> None:
    tool, provider = setup(tmp_path)
    approve(tool)
    provider.fail = True
    assert tool.work_once()["state"] == "uncertain"
    with closing(sqlite3.connect(tool.service.repository.database_path)) as db:
        row = cast("tuple[str]", db.execute("SELECT data FROM image_edit_jobs").fetchone())
    job = ImageEditJob.model_validate_json(row[0])
    result = ToolExecutionResult(
        schema_version="trace.tool-execution-result.v1",
        invocation_sha256=contract_sha256(job.invocation),
        executor_id="codex-image-edit",
        disposition="failed",
        output={"reason_code": "image_edit_result_validation_failed"},
        actual_cost_units=20,
    )
    _ = tool.service.complete_deferred(
        "tenant-a", "run-one", operation_id=job.operation_id, result=result, now=NOW
    )
    _ = tool.work_once()
    with closing(sqlite3.connect(tool.service.repository.database_path)) as db:
        remaining = cast(
            "tuple[int]",
            db.execute("SELECT COUNT(*) FROM image_edit_jobs WHERE settled=0").fetchone(),
        )[0]
    assert remaining == 0
    assert provider.calls == 1


def test_unknown_abandonment_persists_failure_without_provider_replay(tmp_path: Path) -> None:
    tool, provider = setup(tmp_path)
    provider.fail = True
    approve(tool)
    _ = tool.work_once()
    with closing(tool._db()) as db:
        job = ImageEditJob.model_validate_json(
            cast("str", db.execute("SELECT data FROM image_edit_jobs").fetchone()[0])
        )
    digest = contract_sha256(job.invocation)
    status = tool.operation_status("tenant-a", "run-one", job.operation_id)
    assert status["outcome_unknown"] is True
    with pytest.raises(ValueError, match="image_edit_abandonment_target_mismatch"):
        _ = tool.abandon(
            "tenant-a",
            "run-one",
            job.operation_id,
            invocation_sha256="0" * 64,
            reviewer_id="reviewer",
            note="Stop here",
        )
    _ = tool.abandon(
        "tenant-a",
        "run-one",
        job.operation_id,
        invocation_sha256=digest,
        reviewer_id="reviewer",
        note="Stop here",
    )
    _ = tool.abandon(
        "tenant-a",
        "run-one",
        job.operation_id,
        invocation_sha256=digest,
        reviewer_id="reviewer",
        note="Stop here",
    )
    with closing(tool._db()) as db:
        row = cast(
            "tuple[str, int]", db.execute("SELECT result,settled FROM image_edit_jobs").fetchone()
        )
    result = ToolExecutionResult.model_validate_json(row[0])
    assert row[1] == 1
    assert result.disposition == "failed"
    assert result.actual_cost_units == 20
    assert result.output["evidence_kind"] == "human_reported"
    assert result.output["reason_code"] == "image_edit_abandoned_outcome_unknown"
    assert "asset" not in result.output
    assert provider.calls == 1
    with pytest.raises(ValueError, match="image_edit_abandonment_conflict"):
        _ = tool.abandon(
            "tenant-a",
            "run-one",
            job.operation_id,
            invocation_sha256=digest,
            reviewer_id="reviewer",
            note="Different decision",
        )
