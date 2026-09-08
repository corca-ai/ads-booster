from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ads_booster.contracts.knowledge_context import (
    EditorialContextBlock,
    EditorialContextRole,
    EvidenceExcerpt,
    KnowledgeContextTransfer,
)
from ads_booster.contracts.knowledge_context_validation import TrustedKnowledgeContextBinding
from ads_booster.contracts.knowledge_selection import (
    ContextBudget,
    ContextReceipt,
    ContextRequest,
    ContextTokenCounts,
    KnowledgeActionKind,
    RetrievalStatus,
    SelectedSourceRevision,
    VoiceStatus,
)

NOW = datetime(2026, 9, 7, 10, tzinfo=UTC)
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def transfer_fixture() -> KnowledgeContextTransfer:
    request = ContextRequest(
        schema="knowledge.context-request.v1",
        request_id="context.request.1",
        action_kind=KnowledgeActionKind.CONTENT_WRITE,
        task_ref="task.1",
        brand_ref="brand.a",
        query="Write the approved launch caption.",
        required_context=True,
        budget=ContextBudget(max_input_tokens=4_000),
    )
    receipt = ContextReceipt(
        schema="knowledge.context-receipt.v1",
        receipt_id="receipt.1",
        task_ref="task.1",
        scoped_actor_ref="member.a1",
        team_id="workspace.team-a",
        policy_version="policy.7",
        action_kind=KnowledgeActionKind.CONTENT_WRITE,
        resolved_brand_ref="brand.a",
        voice_status=VoiceStatus.CONFIGURED,
        soul_revision_id="memory.soul.rev3",
        selected_source_revisions=(
            SelectedSourceRevision(
                source_id="source.launch",
                revision_id="source.launch.rev2",
                segment_ids=("segment.launch.1",),
                content_sha256=DIGEST_A,
            ),
        ),
        token_counts=ContextTokenCounts(
            required_tokens=80,
            selected_reference_tokens=120,
            total_input_tokens=200,
        ),
        retrieval_status=RetrievalStatus.READY,
        created_at=NOW,
    )
    return KnowledgeContextTransfer(
        schema="trace.knowledge-context.v1",
        transfer_id="transfer.1",
        workspace_id="workspace.team-a",
        account_id="account.a",
        scoped_actor_ref="member.a1",
        brand_ref="brand.a",
        action_kind=KnowledgeActionKind.CONTENT_WRITE,
        run_ref="run.1",
        task_ref="task.1",
        invocation_ref="invocation.1",
        request=request,
        receipt=receipt,
        editorial_context=(
            EditorialContextBlock(
                block_id="constraint.1",
                role=EditorialContextRole.CONSTRAINT,
                text="Use only approved launch claims.",
                revision_refs=("memory.soul.rev3",),
            ),
        ),
        evidence_excerpts=(
            EvidenceExcerpt(
                source_id="source.launch",
                revision_id="source.launch.rev2",
                segment_id="segment.launch.1",
                text="The launch is approved for Korea on September 15.",
            ),
        ),
        policy_revision="policy.7",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )


def binding_fixture(  # noqa: PLR0913
    *,
    workspace_id: str = "workspace.team-a",
    account_id: str = "account.a",
    scoped_actor_ref: str = "member.a1",
    brand_ref: str | None = "brand.a",
    action_kind: KnowledgeActionKind = KnowledgeActionKind.CONTENT_WRITE,
    run_ref: str = "run.1",
    task_ref: str = "task.1",
    invocation_ref: str = "invocation.1",
) -> TrustedKnowledgeContextBinding:
    return TrustedKnowledgeContextBinding(
        workspace_id=workspace_id,
        account_id=account_id,
        scoped_actor_ref=scoped_actor_ref,
        brand_ref=brand_ref,
        action_kind=action_kind,
        run_ref=run_ref,
        task_ref=task_ref,
        invocation_ref=invocation_ref,
    )
