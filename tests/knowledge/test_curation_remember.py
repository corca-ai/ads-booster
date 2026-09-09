from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from ads_booster.knowledge.curation import CurationRunner
from ads_booster.knowledge.curation_contracts import (
    CurationBatchDecision,
    CurationBatchJobContext,
    CurationBatchJobDecision,
    CurationDecision,
    CurationDecisionAction,
    CurationMemoryIntent,
    CurationObservation,
    CurationRequest,
    CurationRunStatus,
)
from ads_booster.knowledge.curation_disposition import RepositorySourceDisposition
from ads_booster.knowledge.curation_memory import CurationMemoryWriter
from ads_booster.knowledge.curation_runtime import CurationDependencies
from ads_booster.knowledge.operation_enums import CurationTarget, OperationStatus
from ads_booster.knowledge.tool_contracts import ToolResultStatus
from ads_booster.knowledge.tools import ToolHost
from tests.knowledge.test_curation_inputs import curation_input as fixture_curation_input

if TYPE_CHECKING:
    from tests.knowledge.test_curation_inputs import CurationInput

curation_input = fixture_curation_input


@dataclass(frozen=True, slots=True)
class MemoryProvider:
    def decide(
        self,
        request: CurationRequest,
        observations: tuple[CurationObservation, ...],
        *,
        timeout_seconds: float,
    ) -> CurationDecision:
        assert timeout_seconds > 0
        assert request.auto_memory_enabled
        if not observations:
            assert request.authenticated_user_event is not None
            return CurationDecision(
                schema="knowledge.curation-decision.v1",
                action=CurationDecisionAction.REMEMBER,
                memory_intent=CurationMemoryIntent(
                    subject_key="한국 팀 가격",
                    text=request.objective,
                    evidence_ids=(request.authenticated_user_event.evidence_ref.evidence_id,),
                ),
            )
        assert observations[-1].result.status in {
            ToolResultStatus.APPLIED,
            ToolResultStatus.REPLAYED,
        }
        return CurationDecision(
            schema="knowledge.curation-decision.v1",
            action=CurationDecisionAction.FINISH,
            targets=(CurationTarget.CORE,),
            finish_summary="Saved sourced project memory",
        )

    def decide_batch(
        self,
        batch_id: str,
        jobs: tuple[CurationBatchJobContext, ...],
        *,
        timeout_seconds: float,
    ) -> CurationBatchDecision:
        return CurationBatchDecision(
            schema="knowledge.curation-batch-decision.v1",
            batch_id=batch_id,
            decisions=tuple(
                CurationBatchJobDecision(
                    job_id=item.request.job_id,
                    decision=self.decide(
                        item.request, item.observations, timeout_seconds=timeout_seconds
                    ),
                )
                for item in jobs
            ),
        )


@pytest.mark.parametrize("batch", [False, True])
def test_semantic_remember_publishes_through_curation(
    curation_input: CurationInput,
    batch: bool,
) -> None:
    repository, processor, job, event, _ = curation_input
    host = ToolHost(repository)
    runner = CurationRunner(
        CurationDependencies(
            provider=MemoryProvider(),
            tool_host=host,
            dispositions=RepositorySourceDisposition(repository),
            memory=CurationMemoryWriter(repository, host),
        )
    )
    work = processor.build_curation_work(job)
    result = (
        runner.run_batch("batch.remember", (work,))[0]
        if batch
        else runner.run(work.request, work.trusted_context)
    )
    assert result.status is CurationRunStatus.FINISHED
    assert result.event_receipt.status is OperationStatus.APPLIED
    assert result.applied_operation_ids
    saved = repository.read_memory(processor.actor, "memory.core")
    assert saved is not None
    assert len(saved.entries) == 1
    assert event.text in saved.entries[0].text
    assert saved.entries[0].source_refs[0].evidence_id == event.message_id
