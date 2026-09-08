"""Real PNG intake, tenant isolation, provenance and canonical same-Run continuation."""

from __future__ import annotations

import base64
import hashlib
import io
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from ads_booster.contracts.agent_run import AgentBudget, AgentGoal, contract_sha256
from ads_booster.contracts.creative_work import CreativeAsset, CreativeScope
from ads_booster.contracts.reasoning import (
    ReasoningDecision,
    ReasoningProviderReceipt,
    ReasoningRequest,
    ReasoningResult,
)
from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.agent.service.application import (
    CreateAgentRunRequest,
    MarketingAgentService,
)
from ads_booster.channels.http.creative_api import dispatch_creative
from ads_booster.creative.creative_asset_links import link_asset
from ads_booster.creative.creative_assets import SqliteCreativeAssetRepository
from ads_booster.channels.http.oauth import OAuthIdentity
from ads_booster.agent.service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.agent.runtime import SqliteSessionStore

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 9, 7, tzinfo=UTC)
IDENTITY = OAuthIdentity("trace", "member")


class WaitThenStop:
    def __init__(self) -> None:
        self.calls: int = 0
        self.evidence: str = ""

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        self.calls += 1
        self.evidence = str(request.evidence)
        decision = ReasoningDecision(
            schema_version="trace.reasoning-decision.v1",
            action="request_input" if self.calls == 1 else "stop",
            reasoning_summary="Please return a capture"
            if self.calls == 1
            else "Asset received; visual QA remains",
            expected_outcome="Continue the same work from human output",
        )
        return ReasoningResult(
            schema_version="trace.reasoning-result.v1",
            decision=decision,
            receipt=ReasoningProviderReceipt(
                schema_version="trace.reasoning-provider-receipt.v1",
                provider_id="fake",
                model_id="fake",
                request_sha256=contract_sha256(request),
                output_schema_sha256="a" * 64,
                decision_sha256=contract_sha256(decision),
            ),
        )


def _service(tmp_path: Path) -> tuple[MarketingAgentService, WaitThenStop]:
    database = tmp_path / "service.db"
    provider = WaitThenStop()
    service = MarketingAgentService(
        SqliteAgentRunRepository(database),
        ToolRegistry(()),
        provider,
        {},
        SqliteSessionStore(database),
    )
    _ = service.create(
        CreateAgentRunRequest(
            run_id="work",
            tenant_id="trace",
            goal=AgentGoal(objective="Review my Figma image", success_criteria=("Receive image",)),
            budget=AgentBudget(max_tool_calls=0, max_cost_units=0),
        ),
        now=NOW,
    )
    return service, provider


def _body(**updates: object) -> bytes:
    buffer = io.BytesIO()
    with Image.new("RGB", (12, 20), color="white") as image:
        image.save(buffer, format="PNG")
    return json.dumps(
        {
            "asset_id": "background",
            "revision": 1,
            "image_base64": base64.b64encode(buffer.getvalue()).decode(),
            "kind": "background_asset",
            "source": "Synthetic PNG fixture",
            "use_terms": "Test owned",
            "data_permission": "synthetic",
            "permission_evidence": "Generated test fixture",
            **updates,
        }
    ).encode()


def test_png_upload_resumes_same_run_and_replay_does_not_repeat_provider(tmp_path: Path) -> None:
    service, provider = _service(tmp_path)
    root = tmp_path / "assets"
    body = _body()
    result = dispatch_creative(
        "POST",
        "/v1/runs/work/assets",
        body,
        identity=IDENTITY,
        service=service,
        artifact_root=root,
        now=NOW,
    )
    assert result is not None
    assert result[0] == 201
    assert provider.calls == 2
    assert "human_reported" in provider.evidence
    assert "not_performed" in provider.evidence
    run = service.repository.get("trace", "work")
    assert run is not None
    assert run.state.value == "completed"
    replay = dispatch_creative(
        "POST",
        "/v1/runs/work/assets",
        body,
        identity=IDENTITY,
        service=service,
        artifact_root=root,
        now=NOW,
    )
    assert replay == result
    assert provider.calls == 2
    preview = dispatch_creative(
        "GET",
        "/v1/runs/work/assets/background",
        b"",
        identity=IDENTITY,
        service=service,
        artifact_root=root,
        now=NOW,
    )
    assert preview is not None
    assert preview[0] == 200
    encoded = preview[1]["image_base64"]
    assert isinstance(encoded, str)
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
        assert image.size == (12, 20)
    assert "relative_path" not in str(preview)
    assert str(tmp_path) not in str(preview)
    assert preview[1]["product_proof_verified"] is False
    assert all(file.stat().st_mode & 0o777 == 0o600 for file in root.rglob("*.png"))


@pytest.mark.parametrize(
    "forged",
    [
        {"origin": "worker_receipt"},
        {"verified": True},
        {"scope": {"workspace_id": "other"}},
        {"relative_path": "../../private.png"},
        {"receipt_sha256": "a" * 64},
    ],
)
def test_upload_cannot_self_attest_or_choose_paths(
    tmp_path: Path, forged: dict[str, object]
) -> None:
    service, provider = _service(tmp_path)
    root = tmp_path / "assets"
    result = dispatch_creative(
        "POST",
        "/v1/runs/work/assets",
        _body(**forged),
        identity=IDENTITY,
        service=service,
        artifact_root=root,
        now=NOW,
    )
    assert result == (400, {"error": "creative_upload_invalid"})
    assert not root.exists()
    assert provider.calls == 1


def test_tenant_denial_precedes_invalid_image_decode_and_writes(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    root = tmp_path / "assets"
    result = dispatch_creative(
        "POST",
        "/v1/runs/work/assets",
        _body(image_base64="not-base64"),
        identity=OAuthIdentity("other", "member"),
        service=service,
        artifact_root=root,
        now=NOW,
    )
    assert result == (404, {"error": "agent_run_not_found"})
    assert not root.exists()
    own = dispatch_creative(
        "POST",
        "/v1/runs/work/assets",
        _body(image_base64="not-base64"),
        identity=IDENTITY,
        service=service,
        artifact_root=root,
        now=NOW,
    )
    assert own == (400, {"error": "creative_image_base64_invalid"})
    assert not root.exists()


def test_derivative_locale_and_optional_pause_preserve_source(tmp_path: Path) -> None:
    service, provider = _service(tmp_path)
    root = tmp_path / "assets"
    original = dispatch_creative(
        "POST",
        "/v1/runs/work/assets",
        _body(resume=False),
        identity=IDENTITY,
        service=service,
        artifact_root=root,
        now=NOW,
    )
    assert original is not None
    assert original[0] == 201
    assert original[1]["resume_required"] is True
    assert provider.calls == 1
    asset = original[1]["asset"]
    assert isinstance(asset, dict)
    child = dispatch_creative(
        "POST",
        "/v1/runs/work/assets",
        _body(
            asset_id="ja",
            resume=False,
            kind="edited_promotion",
            locale="ja-JP",
            preserve=["layout"],
            change=["title"],
            parents=[{"asset_id": "background", "revision": 1, "sha256": asset["sha256"]}],
        ),
        identity=IDENTITY,
        service=service,
        artifact_root=root,
        now=NOW,
    )
    assert child is not None
    assert child[0] == 201
    received = child[1]["asset"]
    assert isinstance(received, dict)
    assert received["locale"] == "ja-JP"
    assert received["origin"] == "human_reported"
    assert received["preserve"] == ["layout"]
    assert provider.calls == 1


def test_valid_base64_is_not_sufficient_image_evidence(tmp_path: Path) -> None:
    service, provider = _service(tmp_path)
    root = tmp_path / "assets"
    result = dispatch_creative(
        "POST",
        "/v1/runs/work/assets",
        _body(image_base64=base64.b64encode(b"not an image").decode()),
        identity=IDENTITY,
        service=service,
        artifact_root=root,
        now=NOW,
    )
    assert result == (400, {"error": "creative_image_invalid"})
    assert not root.exists()
    assert provider.calls == 1


def test_registered_asset_retry_after_interrupted_continuation_is_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, provider = _service(tmp_path)
    root = tmp_path / "assets"
    body = _body()

    def interrupt(*_args: object, **_kwargs: object) -> None:
        message = "creative_test_interrupted"
        raise OSError(message)

    with monkeypatch.context() as patch:
        patch.setattr("ads_booster.channels.http.creative_api.continue_work", interrupt)
        failed = dispatch_creative(
            "POST",
            "/v1/runs/work/assets",
            body,
            identity=IDENTITY,
            service=service,
            artifact_root=root,
            now=NOW,
        )
    assert failed == (400, {"error": "creative_test_interrupted"})
    assert provider.calls == 1
    recovered = dispatch_creative(
        "POST",
        "/v1/runs/work/assets",
        body,
        identity=IDENTITY,
        service=service,
        artifact_root=root,
        now=NOW,
    )
    assert recovered is not None
    assert recovered[0] == 201
    assert provider.calls == 2
    assert len(list(root.rglob("*.png"))) == 1


def test_web_reads_registered_worker_image_larger_than_inline_upload_limit(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    root = tmp_path / "assets"
    repository = SqliteCreativeAssetRepository(service.repository.database_path, root)
    stream = io.BytesIO()
    Image.new("RGB", (512, 512), "white").save(stream, format="PNG", compress_level=0)
    data = stream.getvalue()
    assert len(data) > 512 * 1024
    _ = (root / "capture.png").write_bytes(data)
    asset = CreativeAsset(
        asset_id="capture",
        revision=1,
        scope=CreativeScope(workspace_id="trace", product_id="trace"),
        kind="native_trace_capture",
        relative_path="capture.png",
        sha256=hashlib.sha256(data).hexdigest(),
        source="Synthetic worker fixture",
        use_terms="Test only",
        data_permission="synthetic",
        permission_evidence="Generated fixture",
        origin="worker_receipt",
        receipt_sha256="a" * 64,
    )
    repository.add(asset, actor_scope=asset.scope)
    link_asset(
        service.repository.database_path,
        tenant_id="trace",
        run_id="work",
        asset_id="capture",
        revision=1,
        request_sha256="b" * 64,
        actor_id="fixture-worker",
    )
    result = dispatch_creative(
        "GET",
        "/v1/runs/work/assets/capture",
        b"",
        identity=IDENTITY,
        service=service,
        artifact_root=root,
        now=NOW,
    )
    assert result is not None
    assert result[0] == 200
    assert result[1]["image_base64"] == base64.b64encode(data).decode()
    assert result[1]["product_proof_verified"] is False
