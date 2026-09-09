from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest
from pydantic import ValidationError

from ads_booster.contracts.knowledge_selection import (
    ContextBudget,
    ContextReceipt,
    ContextTokenCounts,
    KnowledgeActionKind,
    RetrievalStatus,
    SelectedSkillRevision,
    SelectedSourceRevision,
    VoiceStatus,
)
from ads_booster.knowledge.operation_enums import SkillOrigin

NOW = datetime(2026, 9, 7, 10, tzinfo=UTC)
DIGEST = "a" * 64


def test_context_receipt_is_selection_provenance_not_semantic_proof() -> None:
    selected = SelectedSourceRevision(
        source_id="source.price.kr",
        revision_id="source.price.kr.rev2",
        segment_ids=("segment.price.kr.2",),
        content_sha256=DIGEST,
    )
    receipt = ContextReceipt(
        schema="knowledge.context-receipt.v1",
        receipt_id="receipt.context.1",
        task_ref="task.fixture.price-check",
        scoped_actor_ref="member.a1",
        team_id="workspace.team-a",
        policy_version="policy.v7",
        action_kind=KnowledgeActionKind.RESEARCH,
        voice_status=VoiceStatus.NOT_APPLICABLE,
        selected_source_revisions=(selected,),
        token_counts=ContextTokenCounts(
            required_tokens=100,
            selected_reference_tokens=200,
            total_input_tokens=300,
        ),
        retrieval_status=RetrievalStatus.READY,
        created_at=NOW,
    )

    assert receipt.selected_source_revisions == (selected,)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _ = ContextReceipt.model_validate({**receipt.model_dump(), "semantic_truth": True})


def test_context_budget_is_bounded() -> None:
    with pytest.raises(ValidationError):
        _ = ContextBudget(max_input_tokens=12_001)


def test_selected_skill_omits_empty_additive_source_dependencies() -> None:
    receipt = ContextReceipt(
        schema="knowledge.context-receipt.v1",
        receipt_id="receipt.context.skill",
        task_ref="task.fixture.skill",
        scoped_actor_ref="member.a1",
        team_id="workspace.team-a",
        policy_version="policy.v7",
        action_kind=KnowledgeActionKind.RESEARCH,
        voice_status=VoiceStatus.NOT_APPLICABLE,
        token_counts=ContextTokenCounts(
            required_tokens=100,
            selected_reference_tokens=0,
            total_input_tokens=100,
        ),
        retrieval_status=RetrievalStatus.READY,
        created_at=NOW,
    )
    selected = SelectedSkillRevision(
        skill_id="learned.legacy-safe",
        revision_id="learned.legacy-safe.r1",
        content_sha256=DIGEST,
        origin=SkillOrigin.AGENT_CREATED,
        protected=False,
    )

    assert "selected_skill_revisions" not in receipt.model_dump(mode="json")
    payload = receipt.model_copy(update={"selected_skill_revisions": (selected,)}).model_dump(
        mode="json"
    )
    selected_skills = cast("list[object]", payload["selected_skill_revisions"])
    skill = cast("dict[str, object]", selected_skills[0])
    assert "source_refs" not in skill
    assert "source_revisions" not in skill
