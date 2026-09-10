from __future__ import annotations

from datetime import timedelta
from multiprocessing import get_context
from typing import TYPE_CHECKING, override

from pydantic import TypeAdapter

from ads_booster.knowledge.batch_curation import BatchCurationCoordinator, CurationBatchWork
from ads_booster.knowledge.change_publication import ChangePublisher
from ads_booster.knowledge.curation import CurationRunner
from ads_booster.knowledge.curation_disposition import RepositorySourceDisposition
from ads_booster.knowledge.curation_runtime import CurationDependencies
from ads_booster.knowledge.learning_contracts import LearningRoundState
from ads_booster.knowledge.maintenance import KnowledgeOwner
from ads_booster.knowledge.memory_consolidation import MemoryConsolidationProcessor
from ads_booster.knowledge.repository_batch_recovery import recover_running_batches
from ads_booster.knowledge.repository_learning_recovery import recover_released_learning_batches
from tests.knowledge.batch_runtime_support import (
    ControlledProvider,
    EmptyToolHost,
    FixtureBatchRuntime,
    FixtureJobProcessor,
)
from tests.knowledge.change_test_fixtures import NOW
from tests.knowledge.feedback_learning_support import (
    LearningFixture,
    SharedMessage,
    admit_shared_source,
    learning_fixture,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.knowledge.contracts import ActorContext, CurationBatch

_STRING_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])
_TEXT_PAIRS: TypeAdapter[list[tuple[str, str]]] = TypeAdapter(list[tuple[str, str]])


class FailFirstWorkRuntime(FixtureBatchRuntime):
    fail_next_work: bool = True

    @override
    def _work_for(
        self,
        batch: CurationBatch,
        actor: ActorContext,
    ) -> tuple[CurationBatchWork, ...]:
        if self.fail_next_work:
            self.fail_next_work = False
            message = "controlled_learning_work_failure"
            raise ValueError(message)
        return super()._work_for(batch, actor)


def _seal_round(fixture: LearningFixture) -> tuple[str, str, ActorContext]:
    ready_batch_id = ""
    actor: ActorContext | None = None
    for ordinal in range(1, 11):
        source = admit_shared_source(
            fixture,
            SharedMessage(f"recovery.{ordinal}", f"run.recovery.{ordinal}"),
        )
        admission = fixture.learning.admit_turn(
            source.binding,
            source.event,
            source.receipt,
            at=source.event.created_at + timedelta(seconds=ordinal),
        )
        actor = source.binding.actor
        if admission.ready_batch_ids:
            ready_batch_id = admission.ready_batch_ids[0]
    assert actor is not None
    assert ready_batch_id
    round_ = fixture.learning.rounds(actor.workspace_id)[0]
    return round_.round_id, ready_batch_id, actor


def _runtime_parts(
    fixture: LearningFixture,
    actor: ActorContext,
) -> tuple[FixtureJobProcessor, ControlledProvider]:
    context = get_context("spawn")
    provider = ControlledProvider(context.Event(), context.Event(), context.Queue())
    processor = FixtureJobProcessor(
        fixture.knowledge,
        actor,
        CurationRunner(
            CurationDependencies(
                provider,
                EmptyToolHost(),
                RepositorySourceDisposition(fixture.knowledge),
            )
        ),
        MemoryConsolidationProcessor(
            fixture.knowledge,
            actor,
            ChangePublisher(fixture.knowledge),
        ),
    )
    return processor, provider


def _assert_recovered_round(
    fixture: LearningFixture,
    round_id: str,
    old_batch_id: str,
) -> None:
    round_ = fixture.learning.rounds("workspace.alpha")[0]
    assert round_.round_id == round_id
    assert round_.state is LearningRoundState.COMPLETED
    assert fixture.learning.counter("workspace.alpha").conversation_turns == 0
    with fixture.knowledge.connection() as connection:
        admission_batches = _STRING_ROWS.validate_python(
            connection.execute(
                "SELECT DISTINCT batch_id FROM learning_admissions WHERE sealed_round_id=?",
                (round_id,),
            ).fetchall()
        )
        assert len(admission_batches) == 1
        new_batch_id = admission_batches[0][0]
        partitions = _STRING_ROWS.validate_python(
            connection.execute(
                """SELECT partition_key FROM learning_batch_partitions
                WHERE batch_id IN (?,?) ORDER BY batch_id""",
                (old_batch_id, new_batch_id),
            ).fetchall()
        )
    assert new_batch_id != old_batch_id
    assert len(partitions) == 2
    assert partitions[0] == partitions[1]


def test_restart_recovery_reattaches_released_learning_round(tmp_path: Path) -> None:
    fixture = learning_fixture(tmp_path)
    round_id, old_batch_id, actor = _seal_round(fixture)
    claimed = BatchCurationCoordinator(fixture.knowledge).claim(
        actor,
        NOW + timedelta(seconds=60),
    )
    assert claimed is not None
    with KnowledgeOwner(fixture.knowledge.root, "restarted-learning-service") as owner:
        assert recover_running_batches(fixture.knowledge, actor.workspace_id, owner) == 1
    assert (
        recover_released_learning_batches(
            fixture.knowledge,
            actor.workspace_id,
            NOW + timedelta(seconds=60),
        )
        == 1
    )
    assert (
        recover_released_learning_batches(
            fixture.knowledge,
            actor.workspace_id,
            NOW + timedelta(seconds=60),
        )
        == 0
    )

    processor, provider = _runtime_parts(fixture, actor)
    runtime = FixtureBatchRuntime(fixture.knowledge, actor, processor)
    try:
        provider.release.set()
        assert runtime.tick(now=NOW + timedelta(seconds=60))
        runtime.reap(NOW + timedelta(seconds=60))
        _assert_recovered_round(fixture, round_id, old_batch_id)
    finally:
        runtime.shutdown()
        provider.calls.close()
        provider.calls.join_thread()
        runtime.close_queue()


def test_recovery_terminalizes_learning_round_when_bound_session_closed(tmp_path: Path) -> None:
    fixture = learning_fixture(tmp_path)
    round_id, _old_batch_id, actor = _seal_round(fixture)
    claimed = BatchCurationCoordinator(fixture.knowledge).claim(
        actor,
        NOW + timedelta(seconds=60),
    )
    assert claimed is not None
    with KnowledgeOwner(fixture.knowledge.root, "expired-learning-service") as owner:
        assert recover_running_batches(fixture.knowledge, actor.workspace_id, owner) == 1
    with fixture.knowledge.connection() as connection:
        _ = connection.execute(
            "UPDATE sessions SET state='closed' WHERE workspace_id=? AND session_id=?",
            (actor.workspace_id, actor.session_id),
        )

    assert (
        recover_released_learning_batches(
            fixture.knowledge,
            actor.workspace_id,
            NOW + timedelta(seconds=60),
        )
        == 1
    )

    round_ = fixture.learning.rounds(actor.workspace_id)[0]
    assert round_.round_id == round_id
    assert round_.state is LearningRoundState.CANCELLED
    with fixture.knowledge.connection() as connection:
        jobs = _TEXT_PAIRS.validate_python(
            connection.execute("SELECT state,reason_code FROM jobs ORDER BY job_id").fetchall()
        )
    assert jobs == [("failed", "learning_recovery_knowledge_batch_actor_unavailable")] * 10


def test_work_build_failure_reattaches_released_learning_round(tmp_path: Path) -> None:
    fixture = learning_fixture(tmp_path)
    round_id, old_batch_id, actor = _seal_round(fixture)
    processor, provider = _runtime_parts(fixture, actor)
    runtime = FailFirstWorkRuntime(fixture.knowledge, actor, processor)
    try:
        assert runtime.tick(now=NOW + timedelta(seconds=60))
        provider.release.set()
        assert runtime.tick(now=NOW + timedelta(seconds=60))
        runtime.reap(NOW + timedelta(seconds=60))
        _assert_recovered_round(fixture, round_id, old_batch_id)
    finally:
        runtime.shutdown()
        provider.calls.close()
        provider.calls.join_thread()
        runtime.close_queue()
