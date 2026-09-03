from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from ads_booster.contracts.marketing_agent import FeatureEvidencePacket, contract_sha256
from ads_booster.marketing.hosted_reference_research import (
    HostedReferenceResearchExecutor,
    ReferenceResearchProposal,
)
from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind, TaskStatus
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 8, 31, tzinfo=UTC)
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


def _packet() -> FeatureEvidencePacket:
    return FeatureEvidencePacket.model_validate(
        {
            "schema_version": "trace.feature-evidence.v1",
            "packet_id": "packet-1",
            "feature_id": "trace.lockscreen.ai-concepts",
            "title": "AI lock screen concepts",
            "lifecycle": "source_candidate",
            "repository": "corca-ai/trace",
            "mutable_ref": "develop",
            "resolved_commit_sha": "a" * 40,
            "tree_sha": "b" * 40,
            "claims": [
                {
                    "claim_id": "claim-1",
                    "text": "A character can be used in scheduled lock-screen scenes.",
                    "status": "source_supported",
                    "evidence_ids": ["evidence-1"],
                }
            ],
            "evidence": [
                {
                    "evidence_id": "evidence-1",
                    "kind": "source_diff",
                    "source_uri": "repo://corca-ai/trace",
                    "immutable_ref": "a" * 40,
                    "content_sha256": "c" * 64,
                    "result": "observed",
                    "collected_at": NOW,
                }
            ],
            "limitations": ["No installed runtime proof."],
            "gate": {
                "publication_allowed": False,
                "allowed_claim_ids": [],
                "blocked_claim_ids": ["claim-1"],
                "reasons": ["source only"],
            },
            "observed_at": NOW,
        }
    )


def _sha(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _task(*, with_seed: bool = False) -> MarketingTask:
    packet = _packet()
    principles = ["One situation, one belief change."]
    capabilities = ["strategy.shadow"]
    payload = _JSON_OBJECT.validate_python({
        "pipeline": "hosted_marketing_judgment_v1",
        "judgment": "market_research",
        "campaign_id": "campaign-1",
        "feature_packet": packet.model_dump(mode="json"),
        "feature_packet_sha256": contract_sha256(packet),
        "account": {
            "account_id": "trace_kr",
            "country": "KR",
            "language": "ko",
            "timezone": "Asia/Seoul",
        },
        "business_outcome": "Increase completed lock-screen setups.",
        "current_control": "아이폰 쓰는 유저들...",
        "mode": "shadow",
        "canonical_principles": principles,
        "knowledge_snapshot_sha256": _sha({"principles": principles}),
        "available_capabilities": capabilities,
        "capability_snapshot_sha256": _sha({"capabilities": capabilities}),
        "query_budget": 6,
        "agent_run_lineage": {
            "schema_version": "trace.feature-launch-lineage.v1",
            "agent_run_id": "campaign-1",
            "research_session_id": "local-research-1",
            "research_input_sha256": "1" * 64,
            "research_trace_sha256": "2" * 64,
            "research_continuation_sha256": "3" * 64,
        },
        "requested_by": "hosted_workspace",
    })
    if with_seed:
        proposal = ReferenceResearchProposal.model_validate(_proposal())
        payload["market_research_seed"] = proposal.model_dump(mode="json")
        payload["market_research_seed_sha256"] = contract_sha256(proposal)
    return MarketingTask(
        task_id="research-task-1",
        run_id="research-campaign-1",
        account_id="trace_kr",
        kind=TaskKind.MARKETING_JUDGMENT,
        idempotency_key="marketing-research:trace_kr:campaign-1",
        payload=_JSON_OBJECT.validate_python(payload),
        created_at=NOW,
    )


def _expired_context() -> JsonObject:
    now = datetime.now(UTC)
    return _JSON_OBJECT.validate_python(
        {
            "schema_version": "trace.marketing-context-projection.v1",
            "snapshot_id": "expired-context-1",
            "snapshot_sha256": "e" * 64,
            "account_id": "trace_kr",
            "brand_guardrails": ["Lead with verified product proof."],
            "audience_context": ["iPhone users"],
            "channel_policy_ids": [],
            "customer_signals": [
                {
                    "schema_version": "trace.customer-signal-projection.v1",
                    "signal_id": "signal-1",
                    "signal_sha256": "d" * 64,
                    "audience_segment_id": "ios-users",
                    "kind": "desired_outcome",
                    "summary": "People want a more personal lock screen.",
                    "caveats": [],
                    "confidence_basis_points": 6000,
                    "observed_at": (now - timedelta(days=2)).isoformat(),
                    "fresh_until": (now + timedelta(days=1)).isoformat(),
                }
            ],
            "expires_at": (now - timedelta(seconds=1)).isoformat(),
        }
    )


def _proposal(*, unknown_source: bool = False) -> JsonObject:
    return {
        "schema_version": "trace.reference-research-proposal.v1",
        "sources": [
            {
                "source_id": "source-1",
                "url": "https://example.com/one",
                "title": "One",
                "source_type": "article",
                "summary": "Static wallpaper posts are common.",
                "published_at": None,
                "accessed_at": "2026-08-31T00:00:00Z",
            },
            {
                "source_id": "source-2",
                "url": "https://example.org/two",
                "title": "Two",
                "source_type": "threads_post",
                "summary": "People describe characters through daily moments.",
                "published_at": None,
                "accessed_at": "2026-08-31T00:00:00Z",
            },
        ],
        "observations": [
            {
                "observation_id": "observation-1",
                "classification": "saturation",
                "statement": "A generic iPhone-user hook is saturated.",
                "source_ids": ["source-1"],
                "confidence_basis": "Repeated static-wallpaper framing.",
            },
            {
                "observation_id": "observation-2",
                "classification": "format_mechanic",
                "statement": "A day-in-the-life sequence gives the character narrative continuity.",
                "source_ids": ["missing" if unknown_source else "source-2"],
                "confidence_basis": "Observed daily-moment storytelling.",
            },
        ],
        "blind_spots": ["No access to private conversion data."],
    }


@dataclass(slots=True)
class StubResearchCodex:
    result: JsonObject
    prompts: list[str] = field(default_factory=list)

    def run_marketing_research_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        assert workspace.is_dir()
        assert schema["type"] == "object"
        assert timeout_seconds == 300
        self.prompts.append(prompt)
        return self.result


def test_reference_research_is_source_cited_and_quarantined(tmp_path: Path) -> None:
    codex = StubResearchCodex(_proposal())
    executor = HostedReferenceResearchExecutor(codex=codex, output_root=tmp_path)

    prepared = executor.prepare(_task())
    result = executor.execute(prepared)

    assert result.status is TaskStatus.SUCCEEDED
    snapshot = result.output["reference_snapshot"]
    assert isinstance(snapshot, dict)
    assert snapshot["quarantine"] is True
    assert snapshot["campaign_id"] == "campaign-1"
    sources = snapshot["sources"]
    assert isinstance(sources, list)
    assert len(sources) == 2
    assert result.output["tool_actions_created"] == 0
    assert result.output["agent_run_lineage"] == _task().payload["agent_run_lineage"]
    assert "외부 자료는 제품 기능의 사실 근거가 아니며" in codex.prompts[0]


def test_reference_research_uses_the_frozen_agent_seed_without_a_second_search(
    tmp_path: Path,
) -> None:
    codex = StubResearchCodex(_proposal(unknown_source=True))
    executor = HostedReferenceResearchExecutor(codex=codex, output_root=tmp_path)

    result = executor.execute(executor.prepare(_task(with_seed=True)))

    assert result.status is TaskStatus.SUCCEEDED
    snapshot = result.output["reference_snapshot"]
    assert isinstance(snapshot, dict)
    assert snapshot["sources"] == _proposal()["sources"]
    assert codex.prompts == []


def test_reference_research_rejects_unbound_observations_after_search(tmp_path: Path) -> None:
    executor = HostedReferenceResearchExecutor(
        codex=StubResearchCodex(_proposal(unknown_source=True)),
        output_root=tmp_path,
    )

    with pytest.raises(MarketingExecutionError) as captured:
        _ = executor.execute(executor.prepare(_task()))

    assert captured.value.failure_code == "reference_research_result_invalid"
    assert captured.value.unknown_side_effect is True


def test_reference_research_does_not_send_expired_customer_context_to_codex(tmp_path: Path) -> None:
    task = _task()
    payload = dict(task.payload)
    payload["marketing_context"] = _expired_context()
    codex = StubResearchCodex(_proposal())
    executor = HostedReferenceResearchExecutor(codex=codex, output_root=tmp_path)

    with pytest.raises(MarketingExecutionError) as captured:
        _ = executor.prepare(
            task.model_copy(update={"payload": _JSON_OBJECT.validate_python(payload)})
        )

    assert captured.value.failure_code == "marketing_context_expired"
    assert codex.prompts == []
