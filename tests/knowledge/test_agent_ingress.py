from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, override

import pytest
from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.contracts import (
    ActorContext,
    ConversationEvent,
    IngestEnvelope,
    IngestReceipt,
)
from ads_booster.knowledge.ingest_receipts import (
    IngestDeliveryReceipt,
    IngestDeliveryReceiptValidationError,
    IngestUnitKind,
    IngestUnitReceipt,
    ingest_unit_delivery_id,
)
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.repository import SqliteKnowledgeRepository
from ads_booster.knowledge.repository_types import MembershipRole
from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.agent.service.application import MarketingAgentService
from ads_booster.channels.http.http_api import MarketingAgentApi
from ads_booster.agent.service.knowledge_ingress import (
    CanonicalKnowledgeIngress,
    PendingKnowledgeIngress,
    TrustedRunBinding,
)
from ads_booster.channels.http.knowledge_ingress_api import (
    ApiIngressRequest,
    build_api_ingress,
)
from ads_booster.channels.http.oauth import OAuthIdentity
from ads_booster.agent.service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.agent.runtime import SqliteSessionStore
from tests.marketing.agent_service.test_application import AskThenStopReasoning
from tests.marketing.agent_service.test_http_api import NOW, StopReasoning

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.agent.core.ports import ReasoningProvider

_INTEGER_ROW: TypeAdapter[tuple[int]] = TypeAdapter(tuple[int])
_FIVE_INTEGER_ROW: TypeAdapter[tuple[int, int, int, int, int]] = TypeAdapter(
    tuple[int, int, int, int, int]
)
_PRESENCE_ROW: TypeAdapter[tuple[int] | None] = TypeAdapter(tuple[int] | None)
_TEXT_ROWS: TypeAdapter[list[tuple[str, ...]]] = TypeAdapter(list[tuple[str, ...]])


@dataclass(frozen=True, slots=True)
class CommitThenLoseReceiptSink:
    database_path: Path

    def ingest(
        self,
        actor: ActorContext,
        event: ConversationEvent,
        envelope: IngestEnvelope,
    ) -> IngestDeliveryReceipt:
        with closing(sqlite3.connect(self.database_path)) as db, db:
            _ = db.execute("CREATE TABLE IF NOT EXISTS receipts(delivery_id TEXT PRIMARY KEY)")
            present = _PRESENCE_ROW.validate_python(
                db.execute(
                    "SELECT 1 FROM receipts WHERE delivery_id=?", (envelope.delivery_id,)
                ).fetchone()
            )
            replayed = present is not None
            if not replayed:
                _ = db.execute("INSERT INTO receipts VALUES (?)", (envelope.delivery_id,))
        if not replayed:
            message = "controlled_lost_receipt"
            raise TimeoutError(message)
        assert actor.workspace_id == event.scope.workspace_id
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
                        source_id="source-one",
                        source_revision_id="source-revision-one",
                        curation_job_id="curation-job-one",
                        index_operation_id="index-operation-one",
                        replayed=True,
                    ),
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class WrongEnvelopeReceiptSink:
    def ingest(
        self,
        actor: ActorContext,
        event: ConversationEvent,
        envelope: IngestEnvelope,
    ) -> IngestDeliveryReceipt:
        assert actor.workspace_id == event.scope.workspace_id
        return IngestDeliveryReceipt(
            schema="knowledge.ingest-delivery-receipt.v1",
            delivery_id=envelope.delivery_id,
            envelope_sha256="0" * 64,
            unit_receipts=(
                IngestUnitReceipt(
                    kind=IngestUnitKind.MESSAGE,
                    receipt=IngestReceipt(
                        schema="knowledge.ingest-receipt.v1",
                        delivery_id=ingest_unit_delivery_id(
                            envelope.delivery_id, IngestUnitKind.MESSAGE, None
                        ),
                        source_id="source-one",
                        source_revision_id="source-revision-one",
                        curation_job_id="curation-job-one",
                        index_operation_id="index-operation-one",
                        replayed=False,
                    ),
                ),
            ),
        )


class FailingAdmissionIngress(CanonicalKnowledgeIngress):
    @override
    def admit(
        self,
        db: sqlite3.Connection,
        binding: TrustedRunBinding,
        event: ConversationEvent,
        envelope: IngestEnvelope,
    ) -> bool:
        _ = super().admit(db, binding, event, envelope)
        message = "controlled_atomic_admission_failure"
        raise RuntimeError(message)


@dataclass(slots=True)
class ControlledRetryableIngressError(Exception):
    code: str = "controlled_retryable_ingress"
    retryable: bool = True


@dataclass(frozen=True, slots=True)
class RetryableFailureSink:
    def ingest(
        self,
        actor: ActorContext,
        event: ConversationEvent,
        envelope: IngestEnvelope,
    ) -> IngestDeliveryReceipt:
        assert actor.workspace_id == event.scope.workspace_id
        assert envelope.delivery_id
        raise ControlledRetryableIngressError


def test_api_admission_uses_authenticated_identity_and_one_transaction(tmp_path: Path) -> None:
    # Given: authenticated API ingress whose body claims another workspace.
    api = _api(tmp_path)
    body = json.dumps(
        {
            "request_id": "request-one",
            "run_id": "run-one",
            "goal": {
                "objective": "가격을 기억해",
                "success_criteria": ["stored"],
                "context": {"workspace_id": "forged-workspace"},
            },
            "budget": {"max_tool_calls": 2, "max_cost_units": 4},
        }
    ).encode()

    # When: the trusted HTTP boundary accepts the request twice.
    assert (
        api.dispatch(
            "POST",
            "/v1/runs",
            authorization="Bearer secret",
            body=body,
            now=NOW + timedelta(seconds=30),
        ).status
        == 202
    )
    assert (
        api.dispatch("POST", "/v1/runs", authorization="Bearer secret", body=body, now=NOW).status
        == 202
    )
    changed_body = json.dumps(
        {
            "request_id": "request-one",
            "run_id": "run-one",
            "goal": {
                "objective": "다른 가격을 기억해",
                "success_criteria": ["stored"],
                "context": {"workspace_id": "forged-workspace"},
            },
            "budget": {"max_tool_calls": 2, "max_cost_units": 4},
        }
    ).encode()
    assert (
        api.dispatch(
            "POST",
            "/v1/runs",
            authorization="Bearer secret",
            body=changed_body,
            now=NOW + timedelta(minutes=1),
        ).status
        == 409
    )
    rejected_create = json.dumps(
        {
            "request_id": "request-two",
            "run_id": "run-one",
            "goal": {
                "objective": "새 요청은 거절해",
                "success_criteria": ["different"],
                "context": {},
            },
            "budget": {"max_tool_calls": 2, "max_cost_units": 4},
        }
    ).encode()
    assert (
        api.dispatch(
            "POST",
            "/v1/runs",
            authorization="Bearer secret",
            body=rejected_create,
            now=NOW + timedelta(minutes=2),
        ).status
        == 409
    )
    rejected_input = json.dumps(
        {"request_id": "input-after-completion", "evidence": {"note": "거절해"}}
    ).encode()
    assert (
        api.dispatch(
            "POST",
            "/v1/runs/run-one/input",
            authorization="Bearer secret",
            body=rejected_input,
            now=NOW + timedelta(minutes=3),
        ).status
        == 409
    )

    # Then: one immutable event/outbox pair is bound to the authenticated workspace.
    with closing(sqlite3.connect(tmp_path / "agent.sqlite3")) as db:
        rows = _TEXT_ROWS.validate_python(
            db.execute(
                """SELECT binding_json,state FROM knowledge_run_bindings
                JOIN knowledge_ingress_outbox USING(binding_id)"""
            ).fetchall()
        )
    assert len(rows) == 1
    binding = TrustedRunBinding.model_validate_json(rows[0][0])
    assert binding.actor.workspace_id == "trace"
    assert binding.actor.member_id == "member-one"
    assert rows[0][1] == "pending"


def test_api_input_uses_new_request_identity_bound_to_existing_run(tmp_path: Path) -> None:
    # Given: an authenticated Run waiting for one user input.
    api = _api(tmp_path, reasoning=AskThenStopReasoning())
    create = json.dumps(
        {
            "request_id": "create-request",
            "run_id": "run-one",
            "goal": {
                "objective": "대상을 정해줘",
                "success_criteria": ["answer"],
                "context": {},
            },
            "budget": {"max_tool_calls": 2, "max_cost_units": 4},
        }
    ).encode()
    assert (
        api.dispatch("POST", "/v1/runs", authorization="Bearer secret", body=create, now=NOW).status
        == 202
    )

    # When: the same authenticated principal submits a request-identified input.
    response = api.dispatch(
        "POST",
        "/v1/runs/run-one/input",
        authorization="Bearer secret",
        body=json.dumps(
            {
                "request_id": "input-request",
                "corrects_revision_ref": "source-revision-prior",
                "evidence": {"note": "대학생"},
            }
        ).encode(),
        now=NOW,
    )

    # Then: create and input have distinct durable bindings to the same trusted Run.
    assert response.status == 202
    with closing(sqlite3.connect(tmp_path / "agent.sqlite3")) as db:
        rows = _TEXT_ROWS.validate_python(
            db.execute("SELECT binding_json FROM knowledge_run_bindings ORDER BY rowid").fetchall()
        )
    bindings = tuple(TrustedRunBinding.model_validate_json(row[0]) for row in rows)
    assert tuple(item.action for item in bindings) == ("create", "input")
    assert {item.run_id for item in bindings} == {"run-one"}
    assert {item.actor.actor_id for item in bindings} == {"member-one"}
    with closing(sqlite3.connect(tmp_path / "agent.sqlite3")) as db:
        fences = _TEXT_ROWS.validate_python(
            db.execute("SELECT reason,state FROM knowledge_ingress_fences").fetchall()
        )
    assert fences == [("correction_pending", "pending")]


def test_api_run_mutation_and_knowledge_admission_roll_back_together(tmp_path: Path) -> None:
    # Given: the admission hook fails after writing knowledge rows in the shared transaction.
    database_path = tmp_path / "agent.sqlite3"
    api = _api(tmp_path)
    failing_api = MarketingAgentApi(
        api.service,
        "trace",
        "member-one",
        "secret",
        knowledge_ingress=FailingAdmissionIngress(database_path),
    )
    create_body = json.dumps(
        {
            "request_id": "atomic-create",
            "run_id": "atomic-run",
            "goal": {"objective": "원자적 생성", "success_criteria": ["stored"]},
            "budget": {"max_tool_calls": 2, "max_cost_units": 4},
        }
    ).encode()

    # When: canonical Run creation reaches the failing admission hook.
    with pytest.raises(RuntimeError, match="controlled_atomic_admission_failure"):
        _ = failing_api.dispatch(
            "POST", "/v1/runs", authorization="Bearer secret", body=create_body, now=NOW
        )

    # Then: neither side of the shared-database admission transaction remains.
    with closing(sqlite3.connect(database_path)) as db:
        counts = _FIVE_INTEGER_ROW.validate_python(
            db.execute(
                """SELECT
                (SELECT COUNT(*) FROM agent_runs),
                (SELECT COUNT(*) FROM agent_run_events),
                (SELECT COUNT(*) FROM knowledge_run_bindings),
                (SELECT COUNT(*) FROM knowledge_conversation_events),
                (SELECT COUNT(*) FROM knowledge_ingress_outbox)"""
            ).fetchone()
        )
    assert counts == (0, 0, 0, 0, 0)

    awaiting_api = _api(tmp_path, reasoning=AskThenStopReasoning())
    awaiting_body = json.dumps(
        {
            "request_id": "awaiting-create",
            "run_id": "awaiting-run",
            "goal": {"objective": "입력을 기다려", "success_criteria": ["answer"]},
            "budget": {"max_tool_calls": 2, "max_cost_units": 4},
        }
    ).encode()
    assert (
        awaiting_api.dispatch(
            "POST", "/v1/runs", authorization="Bearer secret", body=awaiting_body, now=NOW
        ).status
        == 202
    )
    before = awaiting_api.service.repository.get("trace", "awaiting-run")
    assert before is not None
    failing_input_api = MarketingAgentApi(
        awaiting_api.service,
        "trace",
        "member-one",
        "secret",
        knowledge_ingress=FailingAdmissionIngress(database_path),
    )

    with pytest.raises(RuntimeError, match="controlled_atomic_admission_failure"):
        _ = failing_input_api.dispatch(
            "POST",
            "/v1/runs/awaiting-run/input",
            authorization="Bearer secret",
            body=json.dumps({"request_id": "atomic-input", "evidence": {"note": "응답"}}).encode(),
            now=NOW + timedelta(minutes=1),
        )
    after = awaiting_api.service.repository.get("trace", "awaiting-run")
    assert after == before
    with closing(sqlite3.connect(database_path)) as db:
        outbox_count = _INTEGER_ROW.validate_python(
            db.execute("SELECT COUNT(*) FROM knowledge_ingress_outbox").fetchone()
        )[0]
    assert outbox_count == 1


def test_canonical_transaction_rolls_back_event_and_outbox_together(tmp_path: Path) -> None:
    # Given: one fully typed API ingress delivery.
    owner = CanonicalKnowledgeIngress(tmp_path / "agent.sqlite3")
    ingress = _api_ingress("transaction-request")

    # When: the canonical transaction is interrupted after both inserts.
    with pytest.raises(RuntimeError, match="controlled_rollback"):
        _admit_then_rollback(owner, ingress)

    # Then: neither the event nor outbox becomes durable.
    with closing(sqlite3.connect(tmp_path / "agent.sqlite3")) as db:
        event_count = _INTEGER_ROW.validate_python(
            db.execute("SELECT COUNT(*) FROM knowledge_conversation_events").fetchone()
        )[0]
        outbox_count = _INTEGER_ROW.validate_python(
            db.execute("SELECT COUNT(*) FROM knowledge_ingress_outbox").fetchone()
        )[0]
    assert (event_count, outbox_count) == (0, 0)


def test_lost_knowledge_receipt_replays_without_duplicate_commit(tmp_path: Path) -> None:
    # Given: canonical ingress and a separate knowledge sink database.
    sink = CommitThenLoseReceiptSink(tmp_path / "knowledge.sqlite3")
    owner = CanonicalKnowledgeIngress(tmp_path / "agent.sqlite3", sink=sink)
    assert owner.admit_standalone(_api_ingress("crash-request"))

    # When: knowledge commits but its first receipt is lost, then the service restarts.
    with pytest.raises(TimeoutError, match="controlled_lost_receipt"):
        _ = owner.dispatch_once()
    owner.recover()
    assert owner.dispatch_once()

    # Then: the knowledge database has one receipt and canonical outbox is acknowledged.
    with closing(sqlite3.connect(tmp_path / "knowledge.sqlite3")) as db:
        count = _INTEGER_ROW.validate_python(
            db.execute("SELECT COUNT(*) FROM receipts").fetchone()
        )[0]
    with closing(sqlite3.connect(tmp_path / "agent.sqlite3")) as db:
        states = _TEXT_ROWS.validate_python(
            db.execute("SELECT state FROM knowledge_ingress_outbox").fetchall()
        )
    assert count == 1
    assert states == [("acked",)]


def test_retryable_sink_failure_is_durable_without_blind_retry(tmp_path: Path) -> None:
    # Given: a classified retryable sink failure and one pending parent delivery.
    owner = CanonicalKnowledgeIngress(tmp_path / "agent.sqlite3", sink=RetryableFailureSink())
    assert owner.admit_standalone(_api_ingress("retryable-request"))

    # When: dispatch records the failure and recovery runs.
    assert owner.dispatch_once()
    owner.recover()

    # Then: the attempt remains explicitly retryable and is not selected again automatically.
    with closing(sqlite3.connect(tmp_path / "agent.sqlite3")) as db:
        row = TypeAdapter(tuple[str, str, int]).validate_python(
            db.execute("SELECT state,error_code,attempts FROM knowledge_ingress_outbox").fetchone()
        )
    assert row == ("retryable", "controlled_retryable_ingress", 1)
    assert not owner.dispatch_once()


def test_dispatch_refuses_to_ack_receipt_for_another_envelope(tmp_path: Path) -> None:
    # Given: a sink returns a structurally valid receipt bound to the wrong envelope digest.
    owner = CanonicalKnowledgeIngress(tmp_path / "agent.sqlite3", sink=WrongEnvelopeReceiptSink())
    assert owner.admit_standalone(_api_ingress("wrong-receipt-request"))

    # When: the dispatcher validates the aggregate delivery receipt.
    with pytest.raises(
        IngestDeliveryReceiptValidationError,
        match="ingest_delivery_receipt_envelope_sha256_mismatch",
    ):
        _ = owner.dispatch_once()

    # Then: the parent delivery remains unacknowledged for explicit recovery.
    with closing(sqlite3.connect(tmp_path / "agent.sqlite3")) as db:
        states = _TEXT_ROWS.validate_python(
            db.execute("SELECT state FROM knowledge_ingress_outbox").fetchall()
        )
    assert states == [("dispatching",)]


def test_dispatch_commits_real_knowledge_source_before_canonical_ack(tmp_path: Path) -> None:
    # Given: separate canonical and knowledge databases with an admitted editor.
    ingress = _api_ingress("real-sink-request")
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge-root")
    repository.register_actor(ingress.binding.actor, MembershipRole.EDITOR)
    owner = CanonicalKnowledgeIngress(
        tmp_path / "agent.sqlite3", sink=KnowledgeIngestion(repository)
    )
    assert owner.admit_standalone(ingress)

    # When: the outbox dispatcher delivers the persisted typed contracts.
    assert owner.dispatch_once()

    # Then: source, curation job, and index operation exist before the canonical ack.
    with repository.connection() as db:
        knowledge_counts = (
            _INTEGER_ROW.validate_python(db.execute("SELECT COUNT(*) FROM sources").fetchone())[0],
            _INTEGER_ROW.validate_python(db.execute("SELECT COUNT(*) FROM jobs").fetchone())[0],
            _INTEGER_ROW.validate_python(
                db.execute("SELECT COUNT(*) FROM index_outbox").fetchone()
            )[0],
        )
    with closing(sqlite3.connect(tmp_path / "agent.sqlite3")) as db:
        states = _TEXT_ROWS.validate_python(
            db.execute("SELECT state FROM knowledge_ingress_outbox").fetchall()
        )
    assert knowledge_counts == (1, 1, 1)
    assert states == [("acked",)]


def _api(root: Path, *, reasoning: ReasoningProvider | None = None) -> MarketingAgentApi:
    database_path = root / "agent.sqlite3"
    service = MarketingAgentService(
        SqliteAgentRunRepository(database_path),
        ToolRegistry(()),
        StopReasoning() if reasoning is None else reasoning,
        {},
        SqliteSessionStore(database_path),
    )
    return MarketingAgentApi(service, "trace", "member-one", "secret")


def _admit_then_rollback(
    owner: CanonicalKnowledgeIngress, ingress: PendingKnowledgeIngress
) -> None:
    with owner.connect() as db:
        _ = db.execute("BEGIN IMMEDIATE")
        assert owner.admit(db, ingress.binding, ingress.event, ingress.envelope)
        message = "controlled_rollback"
        raise RuntimeError(message)


def _api_ingress(request_id: str) -> PendingKnowledgeIngress:
    return build_api_ingress(
        ApiIngressRequest(
            request_id=request_id,
            run_id="run-one",
            action="create",
            text="가격을 기억해",
            identity=OAuthIdentity(tenant_id="trace", principal_id="member-one"),
            revision=1,
            occurred_at=NOW,
        )
    )
