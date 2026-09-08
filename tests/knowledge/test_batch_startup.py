from __future__ import annotations

import os
from datetime import timedelta
from multiprocessing import get_context
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.knowledge.batch_curation import BatchCurationCoordinator, CurationBatchItem
from ads_booster.knowledge.configuration import (
    KnowledgeSettings,
    initialize_knowledge_store,
    initialize_local_configuration,
)
from ads_booster.knowledge.contracts import KnowledgeJob
from ads_booster.knowledge.maintenance import KnowledgeOwner
from ads_booster.knowledge.operation_enums import JobKind, JobPriority, JobState
from ads_booster.knowledge.repository import JobRegistration, SqliteKnowledgeRepository
from ads_booster.marketing.agent_service.lifecycle import build_installed_knowledge_runtime
from ads_booster.providers.codex_cli import CodexCli

if TYPE_CHECKING:
    from pathlib import Path


def test_service_startup_recovers_batch_after_process_crash(tmp_path: Path) -> None:
    settings = KnowledgeSettings(
        root=tmp_path / "store",
        control_root=tmp_path / "control",
        policy_path=tmp_path / "control/policy.json",
    )
    _ = initialize_local_configuration(*settings.require_enabled(), workspace_id="trace")
    actor = initialize_knowledge_store(settings)
    root, _, _ = settings.require_enabled()
    repository = SqliteKnowledgeRepository(root)
    job = KnowledgeJob(
        schema="knowledge.job.v1",
        job_id="job.crashed-service",
        workspace_id=actor.workspace_id,
        scope=actor.conversation_scope,
        kind=JobKind.CURATION,
        state=JobState.QUEUED,
        priority=JobPriority.ROUTINE,
        root_event_id="event.crashed-service",
        policy_version="policy.v1",
        due_at=actor.authenticated_at,
        created_at=actor.authenticated_at,
    )
    repository.put_job(JobRegistration(job=job, unique_key=job.job_id))
    coordinator = BatchCurationCoordinator(repository)
    _ = coordinator.collect(
        CurationBatchItem(
            job.job_id,
            job.root_event_id,
            1,
            actor,
            job.policy_version,
            job.priority,
            job.created_at,
        )
    )

    def crash() -> None:
        owner = KnowledgeOwner(root, "old-service")
        owner.acquire()
        assert coordinator.claim(actor, actor.authenticated_at + timedelta(seconds=60)) is not None
        os._exit(23)

    process = get_context("fork").Process(target=crash)
    process.start()
    process.join(timeout=5)
    assert process.exitcode == 23
    installed = build_installed_knowledge_runtime(
        settings=settings,
        service_database=tmp_path / "agent.db",
        codex=CodexCli(executable=tmp_path / "codex", model="gpt-5"),
        model_id="gpt-5",
    )
    job_state: TypeAdapter[tuple[str, str | None, str]] = TypeAdapter(tuple[str, str | None, str])
    batch_state: TypeAdapter[tuple[str]] = TypeAdapter(tuple[str])
    try:
        with repository.connection() as connection:
            assert job_state.validate_python(
                connection.execute(
                    "SELECT state,batch_id,reason_code FROM jobs WHERE job_id=?",
                    (job.job_id,),
                ).fetchone()
            ) == ("queued", None, "batch_owner_recovered")
            assert batch_state.validate_python(
                connection.execute("SELECT state FROM curation_batches").fetchone()
            ) == ("cancelled",)
    finally:
        installed.runtime.close()
