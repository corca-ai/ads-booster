from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import CapabilitySnapshot
from ads_booster.contracts.knowledge_preparation import PreparedKnowledgeContext
from ads_booster.contracts.knowledge_selection import KnowledgeActionKind
from ads_booster.knowledge.context_selection import KnowledgeContextAssembler
from ads_booster.knowledge.contracts import TaskBinding, TaskBindingState
from ads_booster.knowledge.curation_contracts import CurationMemoryIntent
from ads_booster.knowledge.curation_memory import CurationMemoryWriter
from ads_booster.knowledge.retrieval import KnowledgeRetriever
from ads_booster.knowledge.tool_contracts import (
    MemoryData,
    MemoryGetInput,
    ToolResultStatus,
)
from ads_booster.knowledge.tools import ToolHost
from tests.knowledge.test_curation_inputs import curation_input as curation_input  # noqa: PLC0414
from tests.knowledge.test_user_curation_memory import member_work, remember_user

if TYPE_CHECKING:
    from ads_booster.knowledge.scope_contracts import ActorContext
    from tests.knowledge.test_curation_inputs import CurationInput


def prepare_for(fixture: CurationInput, principal: ActorContext) -> PreparedKnowledgeContext:
    now = datetime.now(UTC)
    task = TaskBinding(
        task_id=f"task.{principal.member_id}.{principal.session_id}",
        workspace_id=principal.workspace_id,
        actor_ref=principal.actor_id,
        member_id=principal.member_id,
        session_id=principal.session_id,
        action_kind=KnowledgeActionKind.TEAM_CHAT,
        capability_epoch=principal.policy_epoch,
        state=TaskBindingState.ACTIVE,
        opened_at=now,
    )
    result = KnowledgeContextAssembler(fixture[0], KnowledgeRetriever(fixture[0])).prepare(
        principal,
        task,
        query="Plan the next campaign.",
        tool_catalog=(),
        capability_snapshot=CapabilitySnapshot(
            schema_version="trace.capability-snapshot.v1",
            snapshot_id=f"capabilities.{principal.member_id}",
            run_id=f"run.{principal.member_id}",
            descriptors=(),
            created_at=now,
        ),
        now=now,
    )
    assert isinstance(result, PreparedKnowledgeContext)
    return result


def test_new_thread_selects_common_memory_and_only_requesters_user_document(
    curation_input: CurationInput,
) -> None:
    alice = member_work(
        curation_input, "alice", "initial", "Team budget is 44. I prefer alpha-short copy."
    )
    alice_document = remember_user(curation_input, alice, "Prefer alpha-short copy.")
    event = alice.request.authenticated_user_event
    assert event is not None
    common = CurationMemoryWriter(curation_input[0], ToolHost(curation_input[0])).write(
        alice.request,
        CurationMemoryIntent(
            subject_key="team budget",
            text="The shared team budget is 44.",
            evidence_ids=(event.evidence_ref.evidence_id,),
        ),
        alice.trusted_context,
    )
    assert common.status is ToolResultStatus.APPLIED, common
    bob = member_work(curation_input, "bob", "initial", "I prefer beta-detailed copy.")
    bob_document = remember_user(curation_input, bob, "Prefer beta-detailed copy.")
    fresh_alice = member_work(curation_input, "alice", "fresh", "Plan a campaign.")

    prepared = prepare_for(curation_input, fresh_alice.trusted_context.actor)

    selected = {item.document_id: item.kind for item in prepared.receipt.selected_memory_revisions}
    text = "\n".join(block.text for block in prepared.blocks)
    assert selected[alice_document] == "user"
    assert bob_document not in selected
    assert "alpha-short" in text
    assert "beta-detailed" not in text
    assert "shared team budget is 44" in text


def test_user_selector_resolves_authenticated_owner_without_request_member_override(
    curation_input: CurationInput,
) -> None:
    alice = member_work(curation_input, "alice", "initial", "I prefer alpha-short copy.")
    alice_document = remember_user(curation_input, alice, "Prefer alpha-short copy.")
    bob = member_work(curation_input, "bob", "initial", "I prefer beta-detailed copy.")
    bob_document = remember_user(curation_input, bob, "Prefer beta-detailed copy.")
    request = MemoryGetInput.model_validate(
        {"schema": "knowledge.tool.memory-get.v1", "kind": "user"}
    )

    result = ToolHost(curation_input[0]).execute(
        "memory_get", request.model_dump(mode="json"), bob.trusted_context
    )

    assert isinstance(result.data, MemoryData)
    assert result.data.document.document_id == bob_document
    assert result.data.document.document_id != alice_document
