"""Real canonical approval/SQLite flow, synthetic bytes and fake reasoning/network only."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentGoal,
    AgentRecordKind,
    AgentRunState,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.contracts.creative_work import CreativeScope
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
from ads_booster.creative.creative_assets import SqliteCreativeAssetRepository
from ads_booster.channels.slack_asset_intake import (
    SlackAssetIntakeTool,
    slack_asset_import_descriptor,
    slack_file_inspect_descriptor,
)
from ads_booster.channels.slack_image_files import SlackImageFiles
from ads_booster.channels.slack_image_review import bind_files
from ads_booster.agent.service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.agent.service.work_continuation import continue_work
from ads_booster.agent.runtime import SqliteSessionStore
from ads_booster.tools.compatibility import DelegatingToolAdapter

if TYPE_CHECKING:
    from pathlib import Path
    from urllib.request import Request

    from ads_booster.transport.json_types import JsonObject

NOW = datetime(2026, 9, 7, tzinfo=UTC)
SCOPE = CreativeScope(workspace_id="team", product_id="trace")


def png(color: str = "white") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (32, 64), color).save(output, format="PNG")
    return output.getvalue()


@dataclass
class Response:
    data: bytes
    url: str

    def read(self, size: int = -1) -> bytes:
        return self.data[:size]

    def geturl(self) -> str:
        return self.url

    def close(self) -> None:
        pass


@dataclass
class FakeSlack:
    image: bytes = field(default_factory=png)
    calls: list[str] = field(default_factory=list)

    def __call__(self, request: Request, *, timeout: float) -> Response:
        assert timeout <= 15
        assert request.get_method() == "GET"
        self.calls.append(request.full_url)
        if request.full_url.startswith("https://slack.com/api/files.info?"):
            data = json.dumps(
                {
                    "ok": True,
                    "file": {
                        "id": "F01",
                        "team_id": "T01",
                        "url_private_download": "https://files.slack.com/files-pri/T01-F01/fixture.png",
                    },
                }
            ).encode()
        else:
            assert request.full_url == "https://files.slack.com/files-pri/T01-F01/fixture.png"
            data = self.image
        return Response(data, request.full_url)


@dataclass
class IntakeReasoning:
    use_terms: str = "Team-owned synthetic example; limited to this promotional task"
    saw_import: bool = False

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        outputs = [item for item in request.evidence if item.get("capability_id")]
        imported = next(
            (item for item in outputs if item.get("capability_id") == "creative.asset.import"), None
        )
        inspected = next(
            (item for item in outputs if item.get("capability_id") == "creative.file.inspect"), None
        )
        if imported:
            output = imported["output"]
            assert isinstance(output, dict)
            assert output["approver_id"] == "reviewer"
            assert isinstance(output["approval_sha256"], str)
            assert output["usage_rights_verified"] is False
            assert output["product_support_verified"] is False
            asset = output["asset"]
            assert isinstance(asset, dict)
            assert asset["origin"] == "human_reported"
            assert asset["preserve"] == ["character"]
            self.saw_import = True
            decision = ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="request_input",
                expected_outcome="등록된 배경으로 실제 캡처를 함께 검토",
                reasoning_summary="사람 보고와 검증 구분",
            )
        else:
            payload: JsonObject = {"file_id": "F01"}
            if inspected:
                output = inspected["output"]
                assert isinstance(output, dict)
                assert output["sha256"] == sha256(png()).hexdigest()
                payload.update(
                    {
                        "expected_sha256": output["sha256"],
                        "asset_id": "figma-background",
                        "kind": "background_asset",
                        "source": "Team member's Figma export",
                        "use_terms": self.use_terms,
                        "data_permission": "synthetic",
                        "permission_evidence": "User reports team-owned synthetic Figma export",
                        "preserve": ["character"],
                        "change": ["top whitespace"],
                        "locale": "ja-JP",
                    }
                )
            decision = ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="invoke_tool",
                capability_id="creative.asset.import" if inspected else "creative.file.inspect",
                tool_input=payload,
                expected_outcome="정확한 바이트와 사용조건을 사람이 검토",
                reasoning_summary="캠페인 설정 없이 작은 배경 작업을 이어가기",
            )
        return ReasoningResult(
            schema_version="trace.reasoning-result.v1",
            decision=decision,
            receipt=ReasoningProviderReceipt(
                schema_version="trace.reasoning-provider-receipt.v1",
                provider_id="fake.reasoning",
                model_id="fake",
                request_sha256=contract_sha256(request),
                output_schema_sha256="a" * 64,
                decision_sha256=contract_sha256(decision),
            ),
        )


def build(
    tmp_path: Path, http: FakeSlack, reasoning: IntakeReasoning
) -> tuple[MarketingAgentService, SqliteCreativeAssetRepository]:
    database = tmp_path / "service.db"
    repository = SqliteAgentRunRepository(database)
    assets = SqliteCreativeAssetRepository(database, tmp_path / "artifacts")
    files = SlackImageFiles(
        database, assets.artifact_root, "team", "fixture-token", expected_team_id="T01", opener=http
    )
    tool = SlackAssetIntakeTool(repository, assets, files, clock=lambda: NOW)
    descriptors = (
        slack_file_inspect_descriptor(now=NOW, ready=True),
        slack_asset_import_descriptor(now=NOW, ready=True),
    )
    service = MarketingAgentService(
        repository=repository,
        registry=ToolRegistry(descriptors),
        reasoning=reasoning,
        tools={
            "creative.file.inspect": DelegatingToolAdapter(
                capability_id="creative.file.inspect",
                version="1",
                executor_id="fixture.inspect",
                executor=tool.inspect,
            ),
            "creative.asset.import": DelegatingToolAdapter(
                capability_id="creative.asset.import",
                version="1",
                executor_id="fixture.import",
                executor=tool.import_asset,
            ),
        },
        runtime_store=SqliteSessionStore(database),
    )
    return service, assets


def start(service: MarketingAgentService) -> str:
    bind_files(service.repository.database_path, "team", "work", "C01", ("F01",))
    run = service.create(
        CreateAgentRunRequest(
            run_id="work",
            tenant_id="team",
            goal=AgentGoal(
                objective="Figma 배경은 내가 만들었어. 캐릭터는 유지하고 다음 작업을 도와줘",
                success_criteria=("같은 업무에서 사용조건 검토 후 배경 이어받기",),
            ),
            budget=AgentBudget(max_tool_calls=4, max_cost_units=10),
        ),
        now=NOW,
    )
    assert run.state is AgentRunState.AWAITING_APPROVAL
    invocation = latest(service)
    assert invocation.input["expected_sha256"] == sha256(png()).hexdigest()
    return contract_sha256(invocation)


def latest(service: MarketingAgentService) -> ToolInvocation:
    records = service.repository.records("team", "work")
    return ToolInvocation.model_validate(
        next(
            record.payload
            for record in reversed(records)
            if record.kind is AgentRecordKind.INVOCATION
        )
    )


def approve(service: MarketingAgentService, digest: str) -> AgentRunState:
    return service.decide_approval(
        "team",
        "work",
        approver_id="reviewer",
        granted=True,
        expected_invocation_sha256=digest,
        now=NOW,
        expires_at=NOW + timedelta(minutes=5),
    ).state


def test_inspection_approval_restart_import_and_same_work_result(tmp_path: Path) -> None:
    http = FakeSlack()
    service, assets = build(tmp_path, http, IntakeReasoning())
    digest = start(service)
    assert len(http.calls) == 2
    assert assets.get(SCOPE, "figma-background") is None
    reasoning = IntakeReasoning()
    restarted, assets = build(tmp_path, http, reasoning)
    assert approve(restarted, digest) is AgentRunState.AWAITING_INPUT
    asset = assets.get(SCOPE, "figma-background")
    assert asset is not None
    assert asset.sha256 == sha256(png()).hexdigest()
    assert asset.origin == "human_reported"
    assert not asset.product_proof_verified
    assert reasoning.saw_import
    assert len(restarted.repository.list_runs("team")) == 1
    assert len(http.calls) == 4
    assert (
        sum(
            record.kind is AgentRecordKind.RECEIPT
            for record in restarted.repository.records("team", "work")
        )
        == 2
    )


def test_wrong_or_superseded_approval_does_not_register_asset(tmp_path: Path) -> None:
    http, reasoning = FakeSlack(), IntakeReasoning()
    service, assets = build(tmp_path, http, reasoning)
    original = start(service)
    with pytest.raises(ValueError, match="agent_approval_invocation_changed"):
        _ = approve(service, "f" * 64)
    reasoning.use_terms = "Revised permission: this Japanese experiment only"
    _ = continue_work(
        service,
        "team",
        "work",
        event_id="permission-correction",
        actor_id="member",
        note="사용 범위는 일본어 실험만으로 고쳐줘",
        action="revise",
        now=NOW,
    )
    assert contract_sha256(latest(service)) != original
    with pytest.raises(ValueError, match="agent_approval_invocation_changed"):
        _ = approve(service, original)
    assert assets.get(SCOPE, "figma-background") is None
    assert len(http.calls) == 2


def test_changed_file_bytes_cannot_use_earlier_import_approval(tmp_path: Path) -> None:
    http = FakeSlack()
    service, assets = build(tmp_path, http, IntakeReasoning())
    digest = start(service)
    http.image = png("black")
    state = approve(service, digest)
    assert state is AgentRunState.AWAITING_RECONCILIATION
    assert assets.get(SCOPE, "figma-background") is None
    calls = len(http.calls)
    assert service.drive("team", "work", now=NOW).state is AgentRunState.AWAITING_RECONCILIATION
    assert len(http.calls) == calls
