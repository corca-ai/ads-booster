from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.contracts import (
    ActorContext,
    ConversationEvent,
    IngestEnvelope,
    IngestReceipt,
)
from ads_booster.knowledge.errors import KnowledgePolicyError
from ads_booster.knowledge.ingest_receipts import (
    IngestDeliveryReceipt,
    IngestUnitKind,
    IngestUnitReceipt,
    ingest_unit_delivery_id,
)
from ads_booster.knowledge.jobs import BoundedJobRunner, JobProcessResult
from ads_booster.knowledge.maintenance import KnowledgeOwner
from ads_booster.knowledge.repository import SqliteKnowledgeRepository
from ads_booster.knowledge.runtime import KnowledgeRuntime
from ads_booster.agent.service.knowledge_ingress import CanonicalKnowledgeIngress
from ads_booster.channels.http.knowledge_ingress_api import (
    ApiIngressRequest,
    build_api_ingress,
)
from ads_booster.channels.http.oauth import OAuthIdentity
from tests.marketing.agent_service.test_http_api import NOW

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.knowledge.repository_types import JobLease


@dataclass(slots=True)
class ItemFailureError(Exception):
    code: str = "fixture_sink_failure"
    retryable: bool = True


@dataclass(slots=True)
class FailFirstSink:
    failure: Exception
    calls: int = 0

    def ingest(
        self,
        actor: ActorContext,
        event: ConversationEvent,
        envelope: IngestEnvelope,
    ) -> IngestDeliveryReceipt:
        self.calls += 1
        if self.calls == 1:
            raise self.failure
        assert actor.conversation_scope == event.scope
        return IngestDeliveryReceipt(
            schema="knowledge.ingest-delivery-receipt.v1",
            delivery_id=envelope.delivery_id,
            envelope_sha256=contract_sha256(envelope),
            unit_receipts=(
                IngestUnitReceipt(
                    kind=IngestUnitKind.MESSAGE,
                    receipt=IngestReceipt(
                        schema="knowledge.ingest-receipt.v1",
                        delivery_id=ingest_unit_delivery_id(
                            envelope.delivery_id, IngestUnitKind.MESSAGE, None
                        ),
                        source_id="source.fixture",
                        source_revision_id="source.fixture.rev1",
                        curation_job_id="job.fixture",
                        index_operation_id="index.fixture",
                        replayed=False,
                    ),
                ),
            ),
        )


@dataclass(slots=True)
class StopAfterTwoPasses:
    stop: Event
    calls: int = 0

    def dispatch_once(self) -> bool:
        self.calls += 1
        if self.calls == 2:
            self.stop.set()
        return False


class FixtureJobRunner(BoundedJobRunner):
    def close_queue(self) -> None:
        self._queue.close()
        self._queue.join_thread()


class IdleProcessor:
    def process(self, lease: JobLease, cancellation: Event) -> JobProcessResult:
        raise AssertionError((lease.job.job_id, cancellation.is_set()))


@pytest.mark.parametrize(
    ("failure", "expected_state"),
    [
        (KnowledgePolicyError("knowledge_ingress_actor_denied"), "failed"),
        (ItemFailureError(retryable=True), "retryable"),
        (ItemFailureError(retryable=False), "failed"),
    ],
)
def test_continuous_runtime_advances_after_recorded_item_failure(
    tmp_path: Path,
    failure: Exception,
    expected_state: str,
) -> None:
    # Given: two admitted items; the first will fail at the durable dispatcher boundary.
    sink = FailFirstSink(failure)
    ingress = CanonicalKnowledgeIngress(tmp_path / "service.sqlite", sink=sink)
    for suffix in ("first", "second"):
        assert ingress.admit_standalone(
            build_api_ingress(
                ApiIngressRequest(
                    request_id=suffix,
                    run_id=f"run-{suffix}",
                    action="create",
                    text="fixture",
                    identity=OAuthIdentity(tenant_id="trace", principal_id="member-one"),
                    revision=1,
                    occurred_at=NOW,
                )
            )
        )
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    owner = KnowledgeOwner(repository.root, "runtime-failure-test")
    owner.acquire()
    runner = FixtureJobRunner(repository, IdleProcessor(), owner.owner_id)
    stop = Event()
    later_work = StopAfterTwoPasses(stop)
    runtime = KnowledgeRuntime(
        workspace_id="trace",
        owner=owner,
        jobs=runner,
        ingress=ingress,
        memory_views=later_work,
        stop=stop,
    )
    try:
        # When: execute the actual daemon entry point until two complete passes.
        runtime.run_continuous()

        # Then: later work and the next delivery run; the failed item stays unacknowledged.
        assert sink.calls == 2
        assert later_work.calls == 2
        assert not runtime.activity.active
        with ingress.connect() as connection:
            assert connection.execute(
                "SELECT delivery_id,state,attempts FROM knowledge_ingress_outbox ORDER BY rowid",
            ).fetchall() == [("first", expected_state, 1), ("second", "acked", 1)]
        assert not ingress.dispatch_once()
    finally:
        runtime.close()
        runner.close_queue()
