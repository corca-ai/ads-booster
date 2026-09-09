from __future__ import annotations

from datetime import timedelta
from threading import Event
from typing import TYPE_CHECKING

import pytest

from ads_booster.knowledge.change_publication import ChangePublisher
from ads_booster.knowledge.contract_types import GrantCapability
from ads_booster.knowledge.curation_contracts import CurationMemoryIntent
from ads_booster.knowledge.curation_memory import CurationMemoryWriter
from ads_booster.knowledge.memory_consolidation import MemoryConsolidationProcessor
from ads_booster.knowledge.operation_enums import JobKind, JobState
from ads_booster.knowledge.repository import JobClaim, MembershipRole
from ads_booster.knowledge.tool_contracts import ToolResultStatus
from ads_booster.knowledge.tools import ToolHost
from tests.knowledge.test_curation_inputs import curation_input as fixture_curation_input

if TYPE_CHECKING:
    from tests.knowledge.test_curation_inputs import CurationInput

curation_input = fixture_curation_input


@pytest.mark.parametrize("missing_target", [False, True])
@pytest.mark.parametrize(
    "kind",
    [JobKind.MEMORY_CONSOLIDATE, JobKind.MEMORY_SUMMARY_REFRESH, JobKind.MEMORY_VIEW_REFRESH],
)
def test_scheduled_refresh_uses_persisted_document_target(
    curation_input: CurationInput,
    kind: JobKind,
    missing_target: bool,
) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    work = processor.build_curation_work(job)
    editor = processor.actor.model_copy(
        update={
            "grants": (
                *processor.actor.grants,
                processor.actor.grants[0].model_copy(
                    update={"grant_id": "grant.schedule", "capability": GrantCapability.SCHEDULE}
                ),
            )
        }
    )
    repository.register_actor(editor, MembershipRole.ADMIN)
    context = work.trusted_context.model_copy(update={"actor": editor})
    host = ToolHost(repository)
    written = CurationMemoryWriter(repository, host).write(
        work.request,
        CurationMemoryIntent(
            subject_key="price", text=event.text, evidence_ids=(event.message_id,)
        ),
        context,
    )
    assert written.status is ToolResultStatus.APPLIED
    now = work.trusted_context.invoked_at
    scheduled = host.execute(
        "knowledge_schedule",
        {
            "schema": "knowledge.tool.schedule.v1",
            "operation_id": "schedule.refresh",
            "kind": kind.value,
            "targets": ["memory.core", "memory.missing"] if missing_target else ["memory.core"],
            "due_at": now.isoformat(),
            "purpose": "Refresh the selected memory document.",
            "triggers": [event.message_id],
        },
        context,
    )
    assert scheduled.status is ToolResultStatus.APPLIED
    lease = repository.claim_job(JobClaim("worker.schedule", now, now + timedelta(minutes=1)))
    assert lease is not None
    assert lease.job.kind is kind

    # When
    result = MemoryConsolidationProcessor(
        repository,
        editor,
        ChangePublisher(repository),
    ).process(lease, Event())

    # Then
    if missing_target:
        assert result.state is JobState.FAILED
        assert result.payload == b"memory_refresh_target_invalid"
        assert not (repository.files.root / "teams/workspace.alpha/MEMORY.md").exists()
        return
    assert result.state is JobState.COMPLETED, result.payload
    if kind is JobKind.MEMORY_VIEW_REFRESH:
        assert (repository.files.root / "teams/workspace.alpha/MEMORY.md").is_file()
    else:
        child = repository.claim_job(JobClaim("worker.view", now, now + timedelta(minutes=1)))
        assert child is not None
        expected = (
            JobKind.MEMORY_SUMMARY_REFRESH
            if kind is JobKind.MEMORY_CONSOLIDATE
            else JobKind.MEMORY_VIEW_REFRESH
        )
        assert child.job.kind is expected
