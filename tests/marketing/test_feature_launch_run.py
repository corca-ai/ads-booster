from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import ValidationError

from ads_booster.contracts.marketing_agent import (
    ClaimStatus,
    FeatureClaim,
    FeatureEvidencePacket,
    FeatureGate,
    FeatureLifecycle,
    contract_sha256,
)
from ads_booster.marketing.dynamic_evidence_research import (
    DynamicEvidenceResearchRequest,
    DynamicEvidenceResearchRunner,
    DynamicMarketResearchContext,
)
from ads_booster.marketing.evidence_research_operator import ResearchScope
from ads_booster.marketing.feature_launch_run import (
    FeatureLaunchRunError,
    FeatureLaunchRunner,
    FeatureLaunchRunRequest,
    HttpHostedCampaignControlPlane,
)
from ads_booster.marketing.runtime import (
    BoundToolInvocation,
    EffectDisposition,
    JsonSessionStore,
    RuntimeState,
    ToolReceipt,
    canonical_json_sha256,
)
from ads_booster.transport.http import HttpResponse

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ads_booster.transport.json_types import JsonObject

NOW = datetime(2026, 9, 2, 3, 0, tzinfo=UTC)


def test_documented_shadow_launch_matches_the_installed_contract() -> None:
    example = Path(__file__).parents[2] / "docs" / "examples" / "feature-launch-shadow.json"

    request = FeatureLaunchRunRequest.model_validate_json(example.read_text(encoding="utf-8"))

    assert request.agent_run_id == "ai-lock-screen-launch-one"
    assert set(request.research.required_scopes) == {
        ResearchScope.PRODUCT_TRUTH,
        ResearchScope.MARKET_EVIDENCE,
    }
    assert contract_sha256(request.research.feature_packet) == (
        "a1ed255fc06292f2350247f57e3f625b41cbe89bce4de45dfda2f2b34fcbc3a0"
    )


def test_launch_rejects_a_packet_the_host_would_trim_before_digesting() -> None:
    payload = cast("JsonObject", _launch_request().model_dump(mode="json"))
    research = cast("JsonObject", payload["research"])
    packet = cast("JsonObject", research["feature_packet"])
    packet["title"] = " AI Lock Screen Concept "

    with pytest.raises(ValidationError, match="hosted canonical string"):
        _ = FeatureLaunchRunRequest.model_validate(payload)


def test_launch_uses_the_host_utf16_string_limit_for_emoji() -> None:
    payload = cast("JsonObject", _launch_request().model_dump(mode="json"))
    research = cast("JsonObject", payload["research"])
    packet = cast("JsonObject", research["feature_packet"])
    packet["title"] = "😀" * 200

    with pytest.raises(ValidationError, match="hosted canonical string"):
        _ = FeatureLaunchRunRequest.model_validate(payload)


def test_launch_rejects_an_account_id_the_host_cannot_scope() -> None:
    payload = cast("JsonObject", _launch_request().model_dump(mode="json"))
    research = cast("JsonObject", payload["research"])
    research["account_id"] = "Trace_KR"

    with pytest.raises(ValidationError, match="hosted account_id"):
        _ = FeatureLaunchRunRequest.model_validate(payload)


class LaunchCodex:
    def __init__(self, decisions: tuple[ResearchScope, ...]) -> None:
        self.decisions: tuple[ResearchScope, ...] = decisions
        self.planner_calls: int = 0
        self.market_calls: int = 0

    def run_marketing_judgment_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        _ = prompt, schema, workspace, timeout_seconds
        scope = self.decisions[self.planner_calls]
        self.planner_calls += 1
        return {
            "action_id": f"observe.{scope.value}",
            "scope": scope.value,
            "claim_ids": ["claim-feature"],
            "research_question": f"What would change the {scope.value} decision?",
            "counter_evidence_question": f"What would reject the {scope.value} premise?",
        }

    def run_marketing_research_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        _ = prompt, schema, workspace, timeout_seconds
        self.market_calls += 1
        return {
            "schema_version": "trace.reference-research-proposal.v1",
            "sources": [
                {
                    "source_id": "source-one",
                    "url": "https://example.com/one",
                    "title": "One",
                    "source_type": "research",
                    "summary": "Demonstration changes comprehension.",
                    "published_at": None,
                    "accessed_at": "2026-09-02T03:00:00Z",
                },
                {
                    "source_id": "source-two",
                    "url": "https://example.org/two",
                    "title": "Two",
                    "source_type": "official_product",
                    "summary": "Generic hooks are saturated.",
                    "published_at": None,
                    "accessed_at": "2026-09-02T03:00:00Z",
                },
            ],
            "observations": [
                {
                    "observation_id": "observation-one",
                    "classification": "format_mechanic",
                    "statement": "Show the changing lock screen.",
                    "source_ids": ["source-one"],
                    "confidence_basis": "Observed in source one.",
                },
                {
                    "observation_id": "observation-two",
                    "classification": "counterevidence",
                    "statement": "Generic device-owner hooks are saturated.",
                    "source_ids": ["source-two"],
                    "confidence_basis": "Observed in source two.",
                },
            ],
            "blind_spots": ["Threads response is unverified."],
        }


class FakeControlPlane:
    def __init__(self, *, ambiguous: bool = False) -> None:
        self.ambiguous: bool = ambiguous
        self.execute_calls: int = 0
        self.lookup_calls: int = 0
        self.status: JsonObject | None = None
        self.invocation: BoundToolInvocation | None = None

    def execute(self, invocation: BoundToolInvocation) -> ToolReceipt:
        self.execute_calls += 1
        self.invocation = invocation
        if self.ambiguous:
            message = "transport ended after dispatch"
            raise RuntimeError(message)
        self.publish_status()
        return ToolReceipt(
            call_id=invocation.call.call_id,
            call_sha256=invocation.call.digest,
            approval_grant_sha256=None,
            disposition=EffectDisposition.SUCCEEDED,
            actual_cost_units=1,
            receipt_sha256="a" * 64,
        )

    def lookup(self, campaign_id: str, account_id: str) -> JsonObject | None:
        self.lookup_calls += 1
        assert campaign_id == "launch-one"
        assert account_id == "trace-kr"
        return self.status

    def publish_status(self) -> None:
        assert self.invocation is not None
        request = self.invocation.request
        packet = request["feature_packet"]
        assert isinstance(packet, dict)
        lineage = request["agent_run_lineage"]
        assert isinstance(lineage, dict)
        self.status = {
            "account_id": request["account_id"],
            "campaign_id": request["campaign_id"],
            "feature_packet_sha256": canonical_json_sha256(packet),
            "mode": "shadow",
            "state": "strategy_requested",
            "agent_run_lineage": lineage,
        }


class AmbiguousHttp:
    def __init__(self) -> None:
        self.post_calls: int = 0
        self.get_calls: int = 0
        self.post_headers: list[dict[str, str]] = []
        self.get_headers: list[dict[str, str]] = []

    def post_json(
        self,
        url: str,
        payload: JsonObject,
        headers: Mapping[str, str],
    ) -> HttpResponse:
        _ = url, payload
        self.post_calls += 1
        self.post_headers.append(dict(headers))
        return HttpResponse(429, b"rate limited", {})

    def get(self, url: str, headers: Mapping[str, str]) -> HttpResponse:
        _ = url
        self.get_calls += 1
        self.get_headers.append(dict(headers))
        return HttpResponse(404, b"not found", {})

    def post_form(
        self,
        url: str,
        form: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> HttpResponse:
        raise AssertionError((url, form, headers))


def test_feature_launch_connects_research_once_and_replays_without_post(tmp_path: Path) -> None:
    codex = LaunchCodex((ResearchScope.MARKET_EVIDENCE, ResearchScope.PRODUCT_TRUTH))
    control = FakeControlPlane()
    request = _launch_request()
    runner = _runner(tmp_path, codex, control)

    first = runner.run(request, now=NOW)
    resumed = runner.run(request, now=NOW + timedelta(minutes=1))

    assert first.state == "created"
    assert first.lineage is not None
    assert first.research.continuation is not None
    assert resumed.state == "created"
    assert control.execute_calls == 1
    assert codex.planner_calls == 2
    assert codex.market_calls == 1
    stored = JsonSessionStore(tmp_path / "launch" / "sessions").load("launch-launch-one")
    assert stored is not None
    assert stored.state is RuntimeState.COMPLETED


def test_ambiguous_create_uses_get_only_reconciliation(tmp_path: Path) -> None:
    codex = LaunchCodex((ResearchScope.PRODUCT_TRUTH, ResearchScope.MARKET_EVIDENCE))
    control = FakeControlPlane(ambiguous=True)
    request = _launch_request()
    runner = _runner(tmp_path, codex, control)

    first = runner.run(request, now=NOW)
    control.publish_status()
    second = runner.run(request, now=NOW + timedelta(minutes=1))

    assert first.state == "awaiting_reconciliation"
    assert second.state == "created"
    assert control.execute_calls == 1
    assert control.lookup_calls >= 2


def test_missing_customer_evidence_blocks_before_control_plane(tmp_path: Path) -> None:
    request = _launch_request(include_customer_scope=True)
    codex = LaunchCodex(
        (
            ResearchScope.PRODUCT_TRUTH,
            ResearchScope.CUSTOMER_INTELLIGENCE,
            ResearchScope.MARKET_EVIDENCE,
        )
    )
    control = FakeControlPlane()

    result = _runner(tmp_path, codex, control).run(request, now=NOW)

    assert result.state == "blocked"
    assert result.research.continuation is None
    assert control.execute_calls == 0
    assert control.lookup_calls == 0


def test_http_429_is_ambiguous_and_never_reposted(tmp_path: Path) -> None:
    codex = LaunchCodex((ResearchScope.PRODUCT_TRUTH, ResearchScope.MARKET_EVIDENCE))
    http = AmbiguousHttp()
    control = HttpHostedCampaignControlPlane(http, "https://control.example", "secret-token")
    runner = FeatureLaunchRunner(
        DynamicEvidenceResearchRunner(codex, tmp_path / "research", model_id="gpt-test"),
        control,
        tmp_path / "launch",
    )

    first = runner.run(_launch_request(), now=NOW)
    second = runner.run(_launch_request(), now=NOW + timedelta(minutes=1))

    assert first.state == "awaiting_reconciliation"
    assert second.state == "awaiting_reconciliation"
    assert http.post_calls == 1
    assert http.get_calls == 2
    assert http.post_headers == [
        {
            "authorization": "Bearer secret-token",
            "content-type": "application/json",
            "x-trace-account-id": "trace-kr",
        }
    ]
    assert all(headers["x-trace-account-id"] == "trace-kr" for headers in http.get_headers)
    assert "secret-token" not in repr(control)


def test_hosted_status_must_match_the_researched_account(tmp_path: Path) -> None:
    codex = LaunchCodex((ResearchScope.PRODUCT_TRUTH, ResearchScope.MARKET_EVIDENCE))
    control = FakeControlPlane(ambiguous=True)
    runner = _runner(tmp_path, codex, control)

    first = runner.run(_launch_request(), now=NOW)
    control.publish_status()
    assert control.status is not None
    control.status["account_id"] = "another-account"

    with pytest.raises(FeatureLaunchRunError, match="hosted_campaign_handoff_mismatch"):
        _ = runner.run(_launch_request(), now=NOW + timedelta(minutes=1))

    assert first.state == "awaiting_reconciliation"
    assert control.execute_calls == 1


def test_oversized_hosted_handoff_stops_before_research_or_http(tmp_path: Path) -> None:
    original = _launch_request()
    oversized_packet = original.research.feature_packet.model_copy(
        update={"limitations": ("x" * (65 * 1024),)}
    )
    request = original.model_copy(
        update={
            "research": original.research.model_copy(update={"feature_packet": oversized_packet})
        }
    )
    codex = LaunchCodex((ResearchScope.PRODUCT_TRUTH, ResearchScope.MARKET_EVIDENCE))
    control = FakeControlPlane()

    with pytest.raises(FeatureLaunchRunError, match="hosted_campaign_handoff_too_large"):
        _ = _runner(tmp_path, codex, control).run(request, now=NOW)

    assert codex.planner_calls == 0
    assert control.execute_calls == 0
    assert control.lookup_calls == 0


def _runner(
    root: Path,
    codex: LaunchCodex,
    control: FakeControlPlane,
) -> FeatureLaunchRunner:
    return FeatureLaunchRunner(
        DynamicEvidenceResearchRunner(codex, root / "research", model_id="gpt-test"),
        control,
        root / "launch",
    )


def _launch_request(*, include_customer_scope: bool = False) -> FeatureLaunchRunRequest:
    scopes = (
        ResearchScope.PRODUCT_TRUTH,
        *((ResearchScope.CUSTOMER_INTELLIGENCE,) if include_customer_scope else ()),
        ResearchScope.MARKET_EVIDENCE,
    )
    outcome = "Discover a stronger launch format."
    control = "A generic iPhone-owner text hook."
    research = DynamicEvidenceResearchRequest(
        schema_version="trace.dynamic-evidence-research-request.v1",
        session_id="research-one",
        account_id="trace-kr",
        feature_packet=_shadow_packet(),
        required_scopes=scopes,
        market_context=DynamicMarketResearchContext(
            schema_version="trace.dynamic-market-research-context.v1",
            country="KR",
            language="ko",
            business_outcome=outcome,
            current_control=control,
            query_budget=4,
        ),
        max_tool_calls=len(scopes),
        max_cost_units=8,
    )
    return FeatureLaunchRunRequest(
        schema_version="trace.feature-launch-run-request.v1",
        agent_run_id="launch-one",
        research=research,
        business_outcome=outcome,
        current_control=control,
    )


def _shadow_packet() -> FeatureEvidencePacket:
    return FeatureEvidencePacket(
        schema_version="trace.feature-evidence.v1",
        packet_id="packet-shadow-one",
        feature_id="ai-lock-screen-concept",
        title="AI Lock Screen Concept",
        lifecycle=FeatureLifecycle.SOURCE_CANDIDATE,
        repository="corca-ai/trace",
        mutable_ref="develop",
        resolved_commit_sha="1" * 40,
        tree_sha="2" * 40,
        claims=(
            FeatureClaim(
                claim_id="claim-feature",
                text="A character is reflected in a changing lock screen concept.",
                status=ClaimStatus.SOURCE_SUPPORTED,
            ),
        ),
        gate=FeatureGate(
            publication_allowed=False,
            blocked_claim_ids=("claim-feature",),
            reasons=("Installed proof is not available yet.",),
        ),
        observed_at=NOW,
    )
