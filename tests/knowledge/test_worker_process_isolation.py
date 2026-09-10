from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from multiprocessing import get_context
from pathlib import Path
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING, override

from ads_booster.bootstrap.lifecycle import build_installed_knowledge_runtime
from ads_booster.knowledge.configuration import (
    KnowledgeSettings,
    initialize_knowledge_store,
    initialize_local_configuration,
)
from ads_booster.knowledge.curation import CurationRunner
from ads_booster.knowledge.jobs import BoundedJobRunner, CancellationEvent, JobProcessResult
from ads_booster.knowledge.operation_enums import JobState
from ads_booster.providers.codex_cli import CodexCli
from tests.knowledge.batch_runtime_support import ControlledProvider, batch_fixture
from tests.knowledge.change_test_fixtures import NOW

if TYPE_CHECKING:
    from multiprocessing.queues import Queue

    from ads_booster.knowledge.curation_contracts import (
        CurationBatchDecision,
        CurationBatchJobContext,
    )
    from ads_booster.knowledge.maintenance_jobs import CanonicalJobProcessor
    from ads_booster.knowledge.repository_types import JobLease

_PARENT_LOCK = Lock()


def hold_parent_lock(ready: Event, release: Event) -> None:
    with _PARENT_LOCK:
        ready.set()
        assert release.wait(timeout=15)


class LockProbeProcessor:
    def process(self, lease: JobLease, cancellation: CancellationEvent) -> JobProcessResult:
        assert not cancellation.is_set()
        acquired = _PARENT_LOCK.acquire(timeout=0.25)
        if acquired:
            _PARENT_LOCK.release()
        return JobProcessResult(
            JobState.COMPLETED if acquired else JobState.FAILED,
            lease.job.job_id.encode(),
        )


class LockProbeProvider(ControlledProvider):
    @override
    def decide_batch(
        self, batch_id: str, jobs: tuple[CurationBatchJobContext, ...], *, timeout_seconds: float
    ) -> CurationBatchDecision:
        assert _PARENT_LOCK.acquire(timeout=0.25)
        _PARENT_LOCK.release()
        return super().decide_batch(batch_id, jobs, timeout_seconds=timeout_seconds)


class ProbeJobRunner(BoundedJobRunner):
    def reap(self) -> None:
        process = self._process
        assert process is not None
        process.join(timeout=5)
        assert not process.is_alive()
        assert self.tick(now=NOW)

    def close_queue(self) -> None:
        self._queue.close()
        self._queue.join_thread()


def test_job_child_does_not_inherit_parent_thread_lock(tmp_path: Path) -> None:
    fixture = batch_fixture(tmp_path.resolve() / "knowledge")
    fixture.put("lock-probe")
    runner = ProbeJobRunner(fixture.repository, LockProbeProcessor(), "lock-probe")
    ready, release = Event(), Event()
    holder = Thread(target=hold_parent_lock, args=(ready, release))
    holder.start()
    try:
        assert ready.wait(timeout=5)
        assert runner.tick(now=NOW)
        runner.reap()
        assert fixture.states() == (("job.lock-probe", "completed"),)
    finally:
        release.set()
        holder.join(timeout=5)
        runner.shutdown()
        runner.close_queue()
        fixture.close()


def test_batch_child_does_not_inherit_parent_thread_lock(tmp_path: Path) -> None:
    fixture = batch_fixture(tmp_path.resolve() / "knowledge")
    provider = LockProbeProvider(
        fixture.provider.started, fixture.provider.release, fixture.provider.calls
    )
    fixture.runtime.jobs = replace(
        fixture.runtime.jobs,
        curation=CurationRunner(
            replace(fixture.runtime.jobs.curation.dependencies, provider=provider)
        ),
    )
    fixture.provider.release.set()
    fixture.put("lock-probe")
    ready, release = Event(), Event()
    holder = Thread(target=hold_parent_lock, args=(ready, release))
    holder.start()
    try:
        assert ready.wait(timeout=5)
        at = NOW + timedelta(seconds=60)
        assert fixture.runtime.tick(now=at)
        fixture.runtime.reap(at)
        assert fixture.states() == (("job.lock-probe", "completed"),)
    finally:
        release.set()
        holder.join(timeout=5)
        fixture.close()


def probe_installed_processor(processor: CanonicalJobProcessor, result: Queue[str]) -> None:
    with processor.repository.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM workspaces WHERE workspace_id=?",
                (processor.actor.workspace_id,),
            ).fetchone()[0]
            == 1
        )
    result.put(processor.actor.workspace_id)


def test_installed_processor_graph_crosses_spawn_boundary(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    settings = KnowledgeSettings(
        root=root / "knowledge",
        control_root=root / "control",
        policy_path=root / "control/policy.json",
    )
    _ = initialize_local_configuration(*settings.require_enabled(), workspace_id="spawn-test")
    _ = initialize_knowledge_store(settings)
    installed = build_installed_knowledge_runtime(
        settings=settings,
        service_database=root / "service.sqlite",
        codex=CodexCli(executable=Path("/unused/codex"), model="test"),
        model_id="test",
    )
    context = get_context("spawn")
    result: Queue[str] = context.Queue()
    process = context.Process(
        target=probe_installed_processor,
        args=(installed.runtime.jobs.processor, result),
    )
    try:
        process.start()
        process.join(timeout=10)
        assert process.exitcode == 0
        assert result.get(timeout=1) == "spawn-test"
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        result.close()
        result.join_thread()
        installed.runtime.close()
