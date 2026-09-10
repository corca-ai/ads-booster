from __future__ import annotations

from dataclasses import dataclass
from multiprocessing import get_context
from typing import TYPE_CHECKING, override

from pydantic import TypeAdapter

from ads_booster.knowledge.batch_curation import CurationBatchWork
from ads_booster.knowledge.batch_runtime import CurationBatchRuntime
from ads_booster.knowledge.change_publication import ChangePublisher
from ads_booster.knowledge.contracts import ActorContext, KnowledgeJob
from ads_booster.knowledge.curation import CurationRunner
from ads_booster.knowledge.curation_contracts import (
    CurationBatchDecision,
    CurationBatchJobContext,
    CurationBatchJobDecision,
    CurationDecision,
    CurationDecisionAction,
    CurationObservation,
    CurationRequest,
)
from ads_booster.knowledge.curation_disposition import RepositorySourceDisposition
from ads_booster.knowledge.curation_runtime import CurationDependencies
from ads_booster.knowledge.maintenance_jobs import CanonicalJobProcessor
from ads_booster.knowledge.memory_consolidation import MemoryConsolidationProcessor
from ads_booster.knowledge.operation_enums import JobKind, JobPriority, JobState
from ads_booster.knowledge.repository import (
    JobRegistration,
    MembershipRole,
    SqliteKnowledgeRepository,
)
from ads_booster.knowledge.tool_contracts import (
    KnowledgeToolName,
    ToolResult,
    TrustedInvocationContext,
)
from tests.knowledge.change_test_fixtures import NOW
from tests.knowledge.change_test_fixtures import actor as catalog_actor

if TYPE_CHECKING:
    from datetime import datetime
    from multiprocessing.queues import Queue
    from multiprocessing.synchronize import Event
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject

_TEXT_PAIRS: TypeAdapter[list[tuple[str, str]]] = TypeAdapter(list[tuple[str, str]])


@dataclass(frozen=True, slots=True)
class ControlledProvider:
    started: Event
    release: Event
    calls: Queue[tuple[str, ...]]

    def decide(
        self,
        request: CurationRequest,
        observations: tuple[CurationObservation, ...],
        *,
        timeout_seconds: float,
    ) -> CurationDecision:
        message = (request.job_id, observations, timeout_seconds)
        raise AssertionError(message)

    def decide_batch(
        self, batch_id: str, jobs: tuple[CurationBatchJobContext, ...], *, timeout_seconds: float
    ) -> CurationBatchDecision:
        self.calls.put(tuple(item.request.job_id for item in jobs))
        self.started.set()
        assert self.release.wait(timeout=min(timeout_seconds, 5))
        return CurationBatchDecision(
            schema="knowledge.curation-batch-decision.v1",
            batch_id=batch_id,
            decisions=tuple(
                CurationBatchJobDecision(
                    job_id=item.request.job_id,
                    decision=CurationDecision(
                        schema="knowledge.curation-decision.v1",
                        action=CurationDecisionAction.FINISH,
                        finish_summary="Reviewed fixture",
                    ),
                )
                for item in jobs
            ),
        )


class EmptyToolHost:
    def schemas(self) -> dict[KnowledgeToolName, JsonObject]:
        return {}

    def execute(
        self, name: str, arguments: JsonObject, trusted_context: TrustedInvocationContext
    ) -> ToolResult:
        message = (name, arguments, trusted_context.invocation_id)
        raise AssertionError(message)


class FixtureJobProcessor(CanonicalJobProcessor):
    @override
    def build_curation_work(
        self, job: KnowledgeJob, actor: ActorContext | None = None
    ) -> CurationBatchWork:
        scoped_actor = actor or self.actor
        return CurationBatchWork(
            request=CurationRequest(
                schema="knowledge.curation-request.v1",
                job_id=job.job_id,
                event_id=job.root_event_id,
                event_revision=1,
                policy_version=job.policy_version,
                objective="Review fixture",
                started_at=NOW,
            ),
            trusted_context=TrustedInvocationContext(
                invocation_id=job.job_id,
                actor=scoped_actor,
                run_binding_id="run.batch",
                run_id="run.batch",
                job_id=job.job_id,
                capability_epoch=scoped_actor.policy_epoch,
                invoked_at=NOW,
            ),
        )


class FixtureBatchRuntime(CurationBatchRuntime):
    def reap(self, at: datetime) -> None:
        process = self._process
        assert process is not None
        process.join(timeout=5)
        assert not process.is_alive()
        assert self.tick(now=at)

    def close_queue(self) -> None:
        self._queue.close()
        self._queue.join_thread()


@dataclass(frozen=True, slots=True)
class BatchFixture:
    repository: SqliteKnowledgeRepository
    actor: ActorContext
    runtime: FixtureBatchRuntime
    provider: ControlledProvider

    def put(
        self, name: str, *, priority: JobPriority = JobPriority.ROUTINE, at: datetime = NOW
    ) -> None:
        job = KnowledgeJob(
            schema="knowledge.job.v1",
            job_id=f"job.{name}",
            workspace_id=self.actor.workspace_id,
            scope=self.actor.conversation_scope,
            kind=JobKind.CURATION,
            state=JobState.QUEUED,
            priority=priority,
            root_event_id=f"event.{name}",
            policy_version="policy.v1",
            due_at=at,
            created_at=at,
        )
        self.repository.put_job(JobRegistration(job=job, unique_key=job.job_id))

    def states(self) -> tuple[tuple[str, str], ...]:
        with self.repository.connection() as connection:
            return tuple(
                _TEXT_PAIRS.validate_python(
                    connection.execute("SELECT job_id,state FROM jobs ORDER BY job_id").fetchall()
                )
            )

    def close(self) -> None:
        self.provider.release.set()
        self.runtime.shutdown()
        self.provider.calls.close()
        self.provider.calls.join_thread()
        self.runtime.close_queue()


def batch_fixture(root: Path) -> BatchFixture:
    repository = SqliteKnowledgeRepository(root)
    actor = catalog_actor()
    repository.register_actor(actor, MembershipRole.ADMIN)
    context = get_context("spawn")
    provider = ControlledProvider(context.Event(), context.Event(), context.Queue())
    processor = FixtureJobProcessor(
        repository,
        actor,
        CurationRunner(
            CurationDependencies(provider, EmptyToolHost(), RepositorySourceDisposition(repository))
        ),
        MemoryConsolidationProcessor(repository, actor, ChangePublisher(repository)),
    )
    return BatchFixture(
        repository, actor, FixtureBatchRuntime(repository, actor, processor), provider
    )
