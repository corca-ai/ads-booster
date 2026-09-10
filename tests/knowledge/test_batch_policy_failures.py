from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, override

import pytest
from pydantic import TypeAdapter

from ads_booster.knowledge.contracts import (
    AccessScope,
    ConversationEvent,
    ConversationEventKind,
    ConversationRole,
    ScopeKind,
)
from ads_booster.knowledge.curation_contracts import (
    CurationBatchDecision,
    CurationBatchJobContext,
    CurationBatchJobDecision,
    CurationDecision,
    CurationDecisionAction,
    CurationProviderError,
)
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.maintenance_jobs import CanonicalJobProcessor
from ads_booster.knowledge.repository import MembershipRole
from ads_booster.knowledge.tool_contracts import KnowledgeToolName
from tests.knowledge.batch_runtime_support import BatchFixture, ControlledProvider, batch_fixture
from tests.knowledge.change_test_fixtures import NOW
from tests.knowledge.test_curation_inputs import envelope

if TYPE_CHECKING:
    from pathlib import Path


class UnavailableProvider(ControlledProvider):
    @override
    def decide_batch(
        self,
        batch_id: str,
        jobs: tuple[CurationBatchJobContext, ...],
        *,
        timeout_seconds: float,
    ) -> CurationBatchDecision:
        assert timeout_seconds > 0
        raise CurationProviderError(code="invalid_json_schema")


def _set_provider(fixture: BatchFixture, provider_type: type[ControlledProvider]) -> None:
    provider = provider_type(
        fixture.provider.started,
        fixture.provider.release,
        fixture.provider.calls,
    )
    curation = fixture.runtime.jobs.curation
    fixture.runtime.jobs = replace(
        fixture.runtime.jobs,
        curation=replace(curation, dependencies=replace(curation.dependencies, provider=provider)),
    )


def test_provider_failure_persists_failed_receipt_without_requeue(tmp_path: Path) -> None:
    fixture = batch_fixture(tmp_path)
    _set_provider(fixture, UnavailableProvider)
    try:
        fixture.put("provider-failure")
        assert fixture.runtime.tick(now=NOW + timedelta(seconds=60))
        fixture.runtime.reap(NOW + timedelta(seconds=60))

        assert fixture.states() == (("job.provider-failure", "failed"),)
        with fixture.repository.connection() as db:
            receipts = TypeAdapter(list[tuple[str, str]]).validate_python(
                db.execute(
                    "SELECT result_status,json_extract(receipt_json,'$.reason') FROM batch_items",
                ).fetchall()
            )
        assert receipts == [("failed", "knowledge_provider_batch_result_invalid")]
        with fixture.repository.connection() as db:
            assert TypeAdapter(tuple[str | None]).validate_python(
                db.execute("SELECT reason_code FROM jobs").fetchone()
            ) == ("knowledge_provider_batch_result_invalid",)
        assert not fixture.runtime.tick(now=NOW + timedelta(seconds=120))
    finally:
        fixture.close()


class SearchProvider(ControlledProvider):
    @override
    def decide_batch(
        self,
        batch_id: str,
        jobs: tuple[CurationBatchJobContext, ...],
        *,
        timeout_seconds: float,
    ) -> CurationBatchDecision:
        assert timeout_seconds > 0
        return CurationBatchDecision(
            schema="knowledge.curation-batch-decision.v1",
            batch_id=batch_id,
            decisions=tuple(
                CurationBatchJobDecision(
                    job_id=job.request.job_id,
                    decision=CurationDecision(
                        schema="knowledge.curation-decision.v1",
                        action=CurationDecisionAction.TOOL_CALL,
                        tool_name=KnowledgeToolName.SOURCE_SEARCH,
                        tool_arguments_json="{}",
                    ),
                )
                for job in jobs
            ),
        )


def test_budget_exhaustion_persists_failed_receipt_without_requeue(tmp_path: Path) -> None:
    fixture = batch_fixture(tmp_path)
    _set_provider(fixture, SearchProvider)
    fixture.runtime.jobs = replace(
        fixture.runtime.jobs,
        curation=replace(
            fixture.runtime.jobs.curation,
            limits=fixture.runtime.jobs.curation.limits.model_copy(update={"max_search_calls": 0}),
        ),
    )
    try:
        fixture.put("budget-failure")
        assert fixture.runtime.tick(now=NOW + timedelta(seconds=60))
        fixture.runtime.reap(NOW + timedelta(seconds=60))

        assert fixture.states() == (("job.budget-failure", "failed"),)
        with fixture.repository.connection() as db:
            receipts = TypeAdapter(list[tuple[str, str]]).validate_python(
                db.execute(
                    "SELECT result_status,json_extract(receipt_json,'$.reason') FROM batch_items",
                ).fetchall()
            )
        assert receipts == [("failed", "curation_search_budget_exhausted")]
        with fixture.repository.connection() as db:
            assert TypeAdapter(tuple[str | None]).validate_python(
                db.execute("SELECT reason_code FROM jobs").fetchone()
            ) == ("curation_search_budget_exhausted",)
        assert not fixture.runtime.tick(now=NOW + timedelta(seconds=120))
    finally:
        fixture.close()


def test_collection_denial_settles_item_and_continues(tmp_path: Path) -> None:
    fixture = batch_fixture(tmp_path)
    try:
        fixture.put("historical", at=NOW - timedelta(minutes=1))
        fixture.put("current")
        fixture.provider.release.set()
        assert fixture.runtime.tick(now=NOW + timedelta(seconds=60))
        fixture.runtime.reap(NOW + timedelta(seconds=60))
        assert fixture.states() == (("job.current", "completed"), ("job.historical", "failed"))
    finally:
        fixture.close()


def test_claim_denial_settles_only_unclaimed_batch(tmp_path: Path) -> None:
    fixture = batch_fixture(tmp_path)
    try:
        fixture.put("stale")
        assert fixture.runtime.tick(now=NOW)
        with fixture.repository.connection() as connection:
            _ = connection.execute("UPDATE workspaces SET policy_epoch=policy_epoch+1")
        _ = fixture.runtime.tick(now=NOW + timedelta(seconds=60))
        assert fixture.states() == (("job.stale", "failed"),)
        assert not fixture.runtime.active
    finally:
        fixture.close()


@pytest.mark.parametrize("blocked", [False, True])
def test_unavailable_source_settles_job_and_processes_current_revision(
    tmp_path: Path,
    blocked: bool,
) -> None:
    fixture = batch_fixture(tmp_path)
    scope = AccessScope(
        kind=ScopeKind.MEMBER,
        workspace_id=fixture.actor.workspace_id,
        member_id=fixture.actor.member_id,
        session_id=fixture.actor.session_id,
    )
    actor = fixture.actor.model_copy(
        update={
            "conversation_scope": scope,
            "grants": tuple(
                grant.model_copy(update={"scope": scope}) for grant in fixture.actor.grants
            ),
        }
    )
    fixture.repository.register_actor(actor, MembershipRole.ADMIN)
    fixture = replace(fixture, actor=actor)
    fixture.runtime.actor = actor
    fixture.runtime.jobs = CanonicalJobProcessor(
        fixture.repository,
        fixture.actor,
        fixture.runtime.jobs.curation,
        fixture.runtime.jobs.memory,
    )
    event = ConversationEvent(
        conversation_id="conversation.superseded",
        message_id="message.superseded",
        revision=1,
        sequence=1,
        role=ConversationRole.USER,
        speaker_ref=fixture.actor.actor_id,
        created_at=NOW,
        event_kind=ConversationEventKind.MESSAGE_FINALIZED,
        scope=fixture.actor.conversation_scope,
        text="Original preference",
    )
    ingestion = KnowledgeIngestion(fixture.repository)
    try:
        old = ingestion.ingest(fixture.actor, event, envelope(event, "delivery.old"))
        edited = event.model_copy(
            update={
                "revision": 2,
                "event_kind": ConversationEventKind.MESSAGE_EDITED,
                "edited_at": NOW + timedelta(seconds=1),
                "text": "Corrected preference",
            }
        )
        if blocked:
            with fixture.repository.connection() as connection:
                _ = connection.execute(
                    "UPDATE sources SET visibility='blocked' WHERE source_id=?",
                    (old.unit_receipts[0].receipt.source_id,),
                )
            edited = event.model_copy(update={"message_id": "message.healthy"})
        current = ingestion.ingest(fixture.actor, edited, envelope(edited, "delivery.current"))
        fixture.provider.release.set()
        instant = NOW + timedelta(seconds=120)
        assert fixture.runtime.tick(now=instant)
        fixture.runtime.reap(instant)
        old_job = old.unit_receipts[0].receipt.curation_job_id
        current_job = current.unit_receipts[0].receipt.curation_job_id
        assert dict(fixture.states()) == {old_job: "failed", current_job: "completed"}
        with fixture.repository.connection() as connection:
            assert TypeAdapter(tuple[str]).validate_python(
                connection.execute(
                    "SELECT reason_code FROM jobs WHERE job_id=?",
                    (old_job,),
                ).fetchone()
            ) == ("curation_source_unavailable",)
        assert not fixture.runtime.tick(now=instant + timedelta(minutes=1))
        assert dict(fixture.states()) == {old_job: "failed", current_job: "completed"}
    finally:
        fixture.close()
