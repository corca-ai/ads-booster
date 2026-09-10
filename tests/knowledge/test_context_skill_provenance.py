"""Selected skill provenance remains current without becoming generic retrieved context."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from ads_booster.agent.service.knowledge import KnowledgeServiceAdapter
from ads_booster.agent.service.knowledge_ingress import CanonicalKnowledgeIngress
from ads_booster.agent.service.knowledge_transfer import TransferContextMaterial
from ads_booster.contracts.agent_run import CapabilitySnapshot
from ads_booster.contracts.knowledge_context_validation import TrustedKnowledgeContextBinding
from ads_booster.contracts.knowledge_preparation import PreparedKnowledgeContext
from ads_booster.contracts.knowledge_selection import (
    KnowledgeActionKind,
    SelectedSkillSourceRevision,
)
from ads_booster.knowledge.context_selection import KnowledgeContextAssembler
from ads_booster.knowledge.contracts import (
    ConversationEventKind,
    TaskBinding,
    TaskBindingState,
)
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.operation_enums import SkillOperationKind, SkillOrigin
from ads_booster.knowledge.repository_context import context_receipt_is_current
from ads_booster.knowledge.retrieval import KnowledgeRetriever
from ads_booster.knowledge.skill_contracts import SkillOperation
from ads_booster.knowledge.skills import KnowledgeSkills
from ads_booster.knowledge.tool_contracts import ToolResultStatus
from ads_booster.knowledge.tools import ToolHost
from tests.knowledge.change_test_fixtures import NOW
from tests.knowledge.procedural_skill_test_support import (
    apply_skill,
    requested_skill_work,
    skill_record,
)
from tests.knowledge.procedural_skill_test_support import (
    requested_skill_input as fixture_curation_input,
)
from tests.knowledge.test_curation_inputs import envelope

if TYPE_CHECKING:
    from tests.knowledge.test_curation_inputs import CurationInput

curation_input = fixture_curation_input


@pytest.mark.parametrize(
    ("event_kind", "replacement_text"),
    [
        (ConversationEventKind.MESSAGE_EDITED, "The source-linked procedure was superseded."),
        (ConversationEventKind.MESSAGE_DELETED, ""),
    ],
)
def test_selected_skill_retains_current_source_dependency_and_excludes_stale_head(
    curation_input: CurationInput,
    event_kind: ConversationEventKind,
    replacement_text: str,
) -> None:
    # Given: a source-linked shared skill and a task that selects it as metadata-only context.
    repository, processor, _job, event, receipt = curation_input
    host = ToolHost(repository)
    work = requested_skill_work(curation_input)
    authenticated = work.request.authenticated_user_event
    assert authenticated is not None
    record = skill_record(
        skill_id="learned.context-source",
        version="learned.context-source.r1",
        origin=SkillOrigin.AGENT_CREATED,
        protected=False,
        source_refs=(authenticated.evidence_ref,),
    )
    applied = apply_skill(
        host,
        work.trusted_context,
        operation_id="operation.context-source.create",
        operation=SkillOperation(
            operation_id="operation.context-source.create",
            kind=SkillOperationKind.CREATE,
            skill_id=record.skill_id,
            replacement_revision_id=record.version,
            record=record,
            source_refs=record.source_refs,
            reason="The current shared source supports a reusable procedure.",
        ),
    )
    assert applied.status is ToolResultStatus.APPLIED
    task = TaskBinding(
        task_id="task.context-source",
        workspace_id=processor.actor.workspace_id,
        actor_ref=processor.actor.actor_id,
        member_id=processor.actor.member_id,
        session_id=processor.actor.session_id,
        action_kind=KnowledgeActionKind.TEAM_CHAT,
        capability_epoch=processor.actor.policy_epoch,
        state=TaskBindingState.ACTIVE,
        opened_at=NOW,
    )
    _ = host.open_task(processor.actor, task)
    assembler = KnowledgeContextAssembler(repository, KnowledgeRetriever(repository))
    snapshot = CapabilitySnapshot(
        schema_version="trace.capability-snapshot.v1",
        snapshot_id="snapshot.context-source",
        run_id="run.context-source",
        descriptors=(),
        created_at=NOW,
    )

    # When: the context selection records the skill without retrieving its source body.
    prepared = assembler.prepare(
        processor.actor,
        task,
        query="Use learned.context-source",
        tool_catalog=host.catalog(),
        capability_snapshot=snapshot,
        now=NOW,
    )
    assert isinstance(prepared, PreparedKnowledgeContext)
    replay = assembler.prepare(
        processor.actor,
        task,
        query="Use learned.context-source",
        tool_catalog=host.catalog(),
        capability_snapshot=snapshot,
        now=NOW,
    )
    assert isinstance(replay, PreparedKnowledgeContext)
    assert replay.receipt == prepared.receipt
    later = assembler.prepare(
        processor.actor,
        task,
        query="Use learned.context-source",
        tool_catalog=host.catalog(),
        capability_snapshot=snapshot,
        now=NOW + timedelta(seconds=1),
    )
    assert isinstance(later, PreparedKnowledgeContext)
    assert later.receipt.receipt_id != prepared.receipt.receipt_id
    assert prepared.receipt.selected_skill_revisions[0].skill_id == record.skill_id
    skill_blocks = [block for block in prepared.blocks if block.block_id.startswith("skill.")]
    assert "unavailable_capability_ids: knowledge_search" in skill_blocks[0].text
    assert sum(len(block.text.encode()) for block in skill_blocks) <= 2400
    assert prepared.receipt.exclusions
    selected = next(
        item
        for item in prepared.receipt.selected_skill_revisions
        if item.skill_id == record.skill_id
    )
    stored_source = repository.read_source(processor.actor, receipt.source_id)
    assert stored_source is not None
    assert selected.source_refs == record.source_refs
    assert selected.source_revisions == (
        SelectedSkillSourceRevision(
            source_id=stored_source.source.source_id,
            revision_id=stored_source.source.revision_id,
            content_sha256=stored_source.source.sha256,
        ),
    )
    assert prepared.receipt.selected_source_revisions == ()
    assert context_receipt_is_current(repository, processor.actor, prepared.receipt)
    adapter = KnowledgeServiceAdapter(
        CanonicalKnowledgeIngress(repository.database_path.parent / "agent.sqlite"),
        repository,
        host,
        assembler,
    )
    transfer = adapter.create_transfer(
        binding=TrustedKnowledgeContextBinding(
            workspace_id=processor.actor.workspace_id,
            account_id="account.context-source",
            scoped_actor_ref=processor.actor.actor_id,
            brand_ref=None,
            action_kind=task.action_kind,
            run_ref="run.context-source",
            task_ref=task.task_id,
            invocation_ref="invocation.context-source",
        ),
        material=TransferContextMaterial(
            request=prepared.request,
            receipt=prepared.receipt,
        ),
        external_sharing_authority_ref=prepared.receipt.policy_version,
    )
    assert (
        "source",
        stored_source.source.source_id,
        stored_source.source.revision_id,
    ) in repository.transfer_dependencies(transfer.transfer_id)

    # Then: editing the linked source invalidates the old receipt and removes the learned skill.
    edited = event.model_copy(
        update={
            "revision": 2,
            "event_kind": event_kind,
            "edited_at": NOW if event_kind is ConversationEventKind.MESSAGE_EDITED else None,
            "text": replacement_text,
        }
    )
    _ = KnowledgeIngestion(repository).ingest(
        processor.actor,
        edited,
        envelope(edited, "delivery.context-source.edit"),
    )
    assert not context_receipt_is_current(repository, processor.actor, prepared.receipt)
    assert KnowledgeSkills(repository).reference(processor.actor, record.skill_id) is None
    refreshed = assembler.prepare(
        processor.actor,
        task,
        query="Use the learned source procedure.",
        tool_catalog=host.catalog(),
        capability_snapshot=snapshot,
        now=NOW,
    )
    assert isinstance(refreshed, PreparedKnowledgeContext)
    assert all(
        item.skill_id != record.skill_id for item in refreshed.receipt.selected_skill_revisions
    )
