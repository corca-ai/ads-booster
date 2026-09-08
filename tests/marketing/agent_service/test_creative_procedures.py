"""Procedure decisions preserve useful work without inventing execution or image quality."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import AgentBudget, AgentGoal, AgentRunState, contract_sha256
from ads_booster.contracts.reasoning import (
    ReasoningDecision,
    ReasoningProviderReceipt,
    ReasoningRequest,
    ReasoningResult,
)
from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.application import (
    CreateAgentRunRequest,
    MarketingAgentService,
)
from ads_booster.marketing.agent_service.creative_procedures import (
    PROCEDURES,
    CreativeBriefRequest,
    CreativeInputs,
    Task,
    build_creative_brief,
)
from ads_booster.marketing.agent_service.integrations import (
    AgentServiceIntegrationConfig,
    ConfiguredAgentTools,
)
from ads_booster.marketing.agent_service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.marketing.runtime import SqliteSessionStore

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.marketing.dynamic_evidence_research import (
        DynamicEvidenceResearchRequest,
        DynamicEvidenceResearchResult,
    )

NOW = datetime(2026, 9, 7, tzinfo=UTC)


def _inputs(**updates: object) -> CreativeInputs:
    return CreativeInputs.model_validate(
        {
            "asset_ids": ["figma-background"],
            "source_sha256": "a" * 64,
            "source_kind": "background_asset",
            "source": "team Figma export",
            "use_terms": "team-owned synthetic test asset",
            "data_permission": "synthetic",
            "permission_evidence": "no personal calendar data",
            **updates,
        }
    )


def test_removed_capture_task_is_rejected_by_request_contract() -> None:
    with pytest.raises(ValueError, match="literal_error"):
        _ = CreativeBriefRequest.model_validate({"task": "app_capture"})


def test_localization_routes_product_proof_to_real_capture_only() -> None:
    ready = frozenset({"creative.image.localize"})
    promotion = build_creative_brief(
        "localization", _inputs(), locales=("ja-JP", "en-US"), ready_capabilities=ready
    )
    assert promotion.capability_id == "creative.image.localize"
    proof = build_creative_brief(
        "localization",
        _inputs(require_product_proof=True),
        locales=("ja-JP",),
        ready_capabilities=ready,
    )
    assert proof.route == "human_assisted"
    assert proof.capability_id is None
    assert proof.locales == ("ja-JP",)
    assert any("대체하지 않는다" in line for line in proof.guidance)
    assert any("폰트의 대상 문자 지원" in line for line in proof.quality_checks)
    assert any("줄바꿈·넘침" in line for line in proof.quality_checks)


def test_partial_edit_requires_exact_preserve_change_and_source_lineage() -> None:
    ready = frozenset({"creative.image.edit"})
    missing = build_creative_brief(
        "partial_edit", CreativeInputs(asset_ids=("original",)), ready_capabilities=ready
    )
    assert missing.route == "awaiting_input"
    assert {"source_digest", "preserve_regions", "change_regions", "permission_evidence"}.issubset(
        missing.missing_inputs
    )
    brief = build_creative_brief(
        "partial_edit",
        _inputs(),
        preserve=("캐릭터·달력·글씨",),
        change=("위쪽 여백만 확장",),
        ready_capabilities=ready,
    )
    assert brief.route == "automatic"
    assert brief.preserve == ("캐릭터·달력·글씨",)
    assert brief.change == ("위쪽 여백만 확장",)
    assert any("벡터 파일과 구분" in item and "실험 후보" in item for item in brief.guidance)
    unrelated = build_creative_brief(
        "partial_edit",
        _inputs(),
        preserve=brief.preserve,
        change=brief.change,
        ready_capabilities=frozenset({"creative.image.generate"}),
    )
    assert unrelated.route == "human_assisted"


def test_quality_review_without_assets_never_claims_visual_inspection() -> None:
    brief = build_creative_brief(
        "final_qa", CreativeInputs(), ready_capabilities=frozenset({"creative.image.review"})
    )
    assert brief.route == "awaiting_input"
    assert brief.missing_inputs == ("source_asset",)
    assert brief.status == "prepared_not_executed"
    assert any("정량 검사·모델 시각 평가·사람 취향" in item for item in brief.guidance)


def test_bounded_catalog_and_locale_requests_do_not_require_campaign() -> None:
    assert len({item.task for item in PROCEDURES}) == len(PROCEDURES)
    brief = build_creative_brief("mood", CreativeInputs(request="가을 캠퍼스 무드"))
    assert brief.max_candidates == 3
    assert brief.missing_inputs == ()
    with pytest.raises(ValueError, match="locale_duplicate"):
        _ = build_creative_brief("localization", _inputs(), locales=("ja-JP", "ja-JP"))
    with pytest.raises(ValueError, match="limit_exceeded"):
        _ = build_creative_brief("partial_edit", _inputs(), preserve=("keep",) * 33)
    with pytest.raises(ValueError, match="string_pattern_mismatch"):
        _ = build_creative_brief("localization", _inputs(), locales=("../ja",))


def test_configured_service_executes_prepare_without_external_writes(
    tmp_path: Path,
) -> None:
    configured = ConfiguredAgentTools(
        config=AgentServiceIntegrationConfig(),
        research_runner=NeverResearch(),
    )
    provider = PrepareThenWait()
    database = tmp_path / "service.db"
    service = MarketingAgentService(
        repository=SqliteAgentRunRepository(database),
        registry=ToolRegistry(configured.descriptors(now=NOW)),
        reasoning=provider,
        tools=configured.adapters(),
        runtime_store=SqliteSessionStore(database),
    )
    run = service.create(
        CreateAgentRunRequest(
            run_id="creative-small",
            tenant_id="trace",
            goal=AgentGoal(
                objective="Figma에서 배경은 만들었어. 다음엔?", success_criteria=("목업 안내",)
            ),
            budget=AgentBudget(max_tool_calls=1, max_cost_units=0),
        ),
        now=NOW,
    )
    assert run.state == AgentRunState.AWAITING_INPUT
    assert provider.saw_packet
    assert all(item.capability_id != "capture.appium" for item in configured.descriptors(now=NOW))
    with pytest.raises(ValueError, match="extra_forbidden"):
        _ = CreativeBriefRequest.model_validate(
            {
                "task": "mockup",
                "inputs": _inputs().model_dump(mode="json"),
                "ready_capabilities": ["creative.image.mockup"],
            }
        )


class NeverResearch:
    def run(self, request: DynamicEvidenceResearchRequest) -> DynamicEvidenceResearchResult:
        _ = request
        message = "brief preparation must not call research or external services"
        raise AssertionError(message)


class PrepareThenWait:
    def __init__(self) -> None:
        self.saw_packet: bool = False

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        if request.evidence:
            evidence = str(request.evidence)
            assert "human_assisted" in evidence
            assert "prepared_not_executed" in evidence
            self.saw_packet = True
            decision = ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="request_input",
                expected_outcome="목업을 같은 작업에 받아 검수",
                reasoning_summary="목업 도구가 없으므로 준비한 절차로 사람에게 요청",
            )
        else:
            decision = ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="invoke_tool",
                capability_id="creative.prepare",
                tool_input={"task": "mockup", "inputs": _inputs().model_dump(mode="json")},
                expected_outcome="목업 제작과 반환물 안내",
                reasoning_summary="작은 작업 절차 준비",
            )
        return ReasoningResult(
            schema_version="trace.reasoning-result.v1",
            decision=decision,
            receipt=ReasoningProviderReceipt(
                schema_version="trace.reasoning-provider-receipt.v1",
                provider_id="fake.reasoning",
                model_id="fake-model",
                request_sha256=contract_sha256(request),
                output_schema_sha256="d" * 64,
                decision_sha256=contract_sha256(decision),
            ),
        )


@pytest.mark.parametrize("task", ["background_review", "final_qa"])
def test_managed_asset_review_does_not_require_slack_file_upload(task: Task) -> None:
    brief = build_creative_brief(
        task, _inputs(), ready_capabilities=frozenset({"creative.asset.review"})
    )
    assert brief.route == "automatic"
    assert brief.capability_id == "creative.asset.review"
    assert brief.status == "prepared_not_executed"


def test_review_without_managed_assets_preserves_existing_input_boundary() -> None:
    brief = build_creative_brief(
        "final_qa",
        _inputs(asset_ids=[]),
        ready_capabilities=frozenset({"creative.asset.review", "creative.image.review"}),
    )
    assert brief.route == "awaiting_input"
    assert brief.missing_inputs == ("source_asset",)
    slack = build_creative_brief(
        "final_qa", _inputs(), ready_capabilities=frozenset({"creative.image.review"})
    )
    assert slack.capability_id == "creative.image.review"
    assert build_creative_brief("final_qa", _inputs()).route == "human_assisted"
