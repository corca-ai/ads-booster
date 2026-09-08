from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ads_booster.contracts.knowledge_selection import (
    ContextBudget,
    ContextReceipt,
    ContextTokenCounts,
    KnowledgeActionKind,
    RetrievalStatus,
    SelectedSourceRevision,
    VoiceStatus,
)

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
