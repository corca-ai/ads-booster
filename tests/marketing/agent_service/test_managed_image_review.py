"""Managed image review uses real pixels/SQLite and explicitly fake Codex assessment."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from ads_booster.contracts.agent_run import ToolInvocation, contract_sha256
from ads_booster.contracts.creative_work import CreativeScope
from ads_booster.marketing.agent_service.creative_asset_links import link_asset
from ads_booster.marketing.agent_service.creative_image_edit import ImageEditJob
from ads_booster.marketing.agent_service.lifecycle import (
    InstalledServicePaths,
    build_installed_marketing_agent_service,
)
from ads_booster.marketing.agent_service.managed_image_review import (
    ManagedImageReviewTool,
    managed_image_review_descriptor,
)
from ads_booster.providers.codex_cli import CodexCli, read_review_images
from tests.marketing.agent_service.creative_fixtures import NOW, setup_assets
from tests.marketing.agent_service.test_creative_image_edit import approve
from tests.marketing.agent_service.test_creative_image_edit import setup as setup_edit

if TYPE_CHECKING:
    from collections.abc import Callable

    from ads_booster.transport.json_types import JsonObject


@dataclass
class FakeInference:
    calls: int = 0
    hook: Callable[[], None] = lambda: None
    fail: bool = False

    def run(  # noqa: PLR0913 - mirror provider method contract.
        self,
        codex: CodexCli,
        prompt: str,
        schema: JsonObject,
        *,
        images: tuple[Path, ...],
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        _ = codex, prompt, schema, workspace, timeout_seconds
        self.calls += 1
        assert read_review_images(images)[0].width == 32
        self.hook()
        if self.fail:
            message = "fixture response lost"
            raise RuntimeError(message)
        return {
            "schema_version": "trace.image-visual-assessment.v1",
            "summary": "Fixture: inspect calendar contrast",
            "findings": [],
            "uncertainties": ["Not a real model assessment"],
            "human_questions": [],
        }


def install_fake(monkeypatch: pytest.MonkeyPatch) -> FakeInference:
    fake = FakeInference()

    def review(  # noqa: PLR0913 - mirror provider method contract.
        codex: CodexCli,
        prompt: str,
        schema: JsonObject,
        *,
        images: tuple[Path, ...],
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        return fake.run(
            codex,
            prompt,
            schema,
            images=images,
            workspace=workspace,
            timeout_seconds=timeout_seconds,
        )

    monkeypatch.setattr(CodexCli, "run_marketing_image_review_job", review)
    return fake


def setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[
    ManagedImageReviewTool,
    ToolInvocation,
    FakeInference,
]:
    seed, old = setup_assets(tmp_path)
    fake = install_fake(monkeypatch)
    source = old.input["background"]
    assert isinstance(source, dict)
    payload: JsonObject = {"assets": [source], "request": "Check readability only"}
    invocation = old.model_copy(update={"input": payload, "input_sha256": contract_sha256(payload)})
    link_asset(
        seed.repository.database_path,
        tenant_id="tenant-a",
        run_id="run-a",
        asset_id="background",
        revision=1,
        request_sha256=contract_sha256(payload),
        actor_id="fixture-author",
    )
    tool = ManagedImageReviewTool(
        seed.repository, seed.assets, CodexCli(Path("/fixture/codex"), model="fixture")
    )
    return tool, invocation, fake


def test_review_same_run_asset_preserves_lineage_and_replays_no_inference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, invocation, fake = setup(tmp_path, monkeypatch)
    descriptor = managed_image_review_descriptor(now=NOW, ready=True)
    result = tool.execute(invocation, descriptor)
    assert result.output["product_support_verified"] is False
    assert result.output["final_approval"] is False
    assert result.output["sources"] == invocation.input["assets"]
    assert tool.execute(invocation, descriptor) == result
    assert fake.calls == 1


@pytest.mark.parametrize("tenant", [None, "other", "slack-private-fixture"])
def test_foreign_and_private_authority_rejected_before_inference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tenant: str | None,
) -> None:
    tool, invocation, fake = setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="managed_image_review_"):
        _ = tool.execute(
            invocation.model_copy(update={"tenant_id": tenant}),
            managed_image_review_descriptor(now=NOW, ready=True),
        )
    assert fake.calls == 0


def test_same_tenant_other_run_cannot_review_unlinked_asset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, invocation, fake = setup(tmp_path, monkeypatch)
    run = tool.repository.get("tenant-a", "run-a")
    assert run is not None
    _ = tool.repository.create(run.model_copy(update={"run_id": "other-run"}))
    with pytest.raises(ValueError, match="not_bound"):
        _ = tool.execute(
            invocation.model_copy(update={"run_id": "other-run"}),
            managed_image_review_descriptor(now=NOW, ready=True),
        )
    assert fake.calls == 0


def test_current_bytes_rechecked_before_cached_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, invocation, fake = setup(tmp_path, monkeypatch)
    descriptor = managed_image_review_descriptor(now=NOW, ready=True)
    _ = tool.execute(invocation, descriptor)
    _ = (tool.assets.artifact_root / "background.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="digest"):
        _ = tool.execute(invocation, descriptor)
    assert fake.calls == 1


def test_original_revision_change_during_inference_rejects_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, invocation, fake = setup(tmp_path, monkeypatch)
    scope = CreativeScope(workspace_id="tenant-a", product_id="trace")
    original = tool.assets.get(scope, "background")
    assert original is not None
    fake.hook = lambda: tool.assets.add(
        original.model_copy(update={"revision": 2}), actor_scope=scope
    )
    with pytest.raises(ValueError, match="review_failed"):
        _ = tool.execute(invocation, managed_image_review_descriptor(now=NOW, ready=True))
    assert fake.calls == 1


def test_unknown_inference_is_not_repeated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, invocation, fake = setup(tmp_path, monkeypatch)
    fake.fail = True
    descriptor = managed_image_review_descriptor(now=NOW, ready=True)
    with pytest.raises(ValueError, match="review_failed"):
        _ = tool.execute(invocation, descriptor)
    fake.fail = False
    with pytest.raises(ValueError, match="unresolved"):
        _ = tool.execute(invocation, descriptor)
    assert fake.calls == 1


def test_installed_composition_registers_managed_review_without_slack(tmp_path: Path) -> None:
    service = build_installed_marketing_agent_service(
        paths=InstalledServicePaths(tmp_path),
        codex_executable=Path("/fixture/absent"),
        model_id="fixture",
        timeout_seconds=5,
    )
    assert "creative.asset.review" in service.tools
    descriptor = next(
        d
        for d in service.registry.current_descriptors(now=NOW)
        if d.capability_id == "creative.asset.review"
    )
    assert not descriptor.readiness.ready


def test_generated_asset_can_be_reviewed_without_slack_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edit, generated = setup_edit(tmp_path)
    approve(edit)
    assert edit.work_once()["state"] == "completed"
    with closing(sqlite3.connect(edit.service.repository.database_path)) as db:
        row = cast("tuple[str]", db.execute("SELECT data FROM image_edit_jobs").fetchone())
    job = ImageEditJob.model_validate_json(row[0])
    scope = CreativeScope(workspace_id="tenant-a", product_id="trace")
    asset = edit.assets.get(scope, job.operation_id)
    assert asset is not None
    fake = install_fake(monkeypatch)
    review = ManagedImageReviewTool(
        edit.service.repository, edit.assets, CodexCli(Path("/fixture/codex"), model="fixture")
    )
    payload: JsonObject = {
        "assets": [
            {"asset_id": asset.asset_id, "revision": asset.revision, "sha256": asset.sha256}
        ],
        "request": "Review generated seam and preserved character",
    }
    invocation = job.invocation.model_copy(
        update={
            "invocation_id": "review-generated",
            "input": payload,
            "input_sha256": contract_sha256(payload),
        }
    )
    output = review.execute(invocation, managed_image_review_descriptor(now=NOW, ready=True)).output
    assert output["sources"] == payload["assets"]
    assert output["final_approval"] is False
    assert fake.calls == 1
    assert generated.calls == 1
    assert edit.assets.get(scope, asset.asset_id) == asset
