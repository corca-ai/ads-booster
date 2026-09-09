from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

from ads_booster.agent.service.knowledge_ingress import (
    CanonicalKnowledgeIngress,
    PendingKnowledgeIngress,
    TrustedRunBinding,
)
from ads_booster.agent.service.learning_admission import TerminalExperienceAdmission
from ads_booster.agent.service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.channels.contracts import ChannelIdentityBinding
from ads_booster.channels.http.knowledge_ingress_api import ApiIngressRequest, build_api_ingress
from ads_booster.channels.http.oauth import OAuthIdentity
from ads_booster.channels.knowledge_ingress_slack import SlackIngressRequest, build_slack_ingress
from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentGoal,
    AgentRecord,
    AgentRecordKind,
    AgentRun,
    AgentRunState,
    AgentStep,
    AgentStepKind,
    ToolInvocation,
    ToolReceiptRecord,
    contract_sha256,
)
from ads_booster.knowledge.contracts import ConversationEvent, ConversationEventKind, IngestReceipt
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.repository import MembershipRole, SqliteKnowledgeRepository
from ads_booster.knowledge.repository_learning import LearningReviewCoordinator
from tests.knowledge.change_test_fixtures import NOW

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject

ReceiptDisposition = Literal["no_effect", "succeeded", "failed", "unknown_side_effect"]


@dataclass(frozen=True, slots=True)
class SharedMessage:
    message_id: str
    run_id: str
    member_id: str = "member.editor"
    conversation_id: str = "conversation.shared"
    private: bool = False
    revision: int = 1


@dataclass(frozen=True, slots=True)
class AdmittedSource:
    binding: TrustedRunBinding
    event: ConversationEvent
    receipt: IngestReceipt


@dataclass(frozen=True, slots=True)
class LearningFixture:
    knowledge: SqliteKnowledgeRepository
    agent_runs: SqliteAgentRunRepository
    ingress: CanonicalKnowledgeIngress
    learning: LearningReviewCoordinator
    terminal: TerminalExperienceAdmission


@dataclass(frozen=True, slots=True)
class TerminalWrite:
    fixture: LearningFixture
    source: AdmittedSource
    run: AgentRun
    receipt: ToolReceiptRecord
    capability_id: str | None = None
    output: JsonObject | None = None


def learning_fixture(root: Path) -> LearningFixture:
    knowledge = SqliteKnowledgeRepository(root / "knowledge")
    agent_runs = SqliteAgentRunRepository(root / "agent.sqlite3")
    ingress = CanonicalKnowledgeIngress(
        agent_runs.database_path,
        sink=KnowledgeIngestion(knowledge),
    )
    learning = LearningReviewCoordinator(knowledge)
    return LearningFixture(
        knowledge,
        agent_runs,
        ingress,
        learning,
        TerminalExperienceAdmission(learning),
    )


def admit_shared_source(fixture: LearningFixture, message: SharedMessage) -> AdmittedSource:
    pending = _private_pending(message) if message.private else _workspace_pending(message)
    fixture.knowledge.register_actor(pending.binding.actor, MembershipRole.EDITOR)
    assert fixture.ingress.admit_standalone(pending)
    assert fixture.ingress.dispatch_once()
    acknowledged = fixture.ingress.source_for_run(pending.binding.run_id)
    assert acknowledged is not None
    return AdmittedSource(acknowledged.binding, acknowledged.event, acknowledged.receipt)


def _workspace_pending(message: SharedMessage) -> PendingKnowledgeIngress:
    pending = build_api_ingress(
        ApiIngressRequest(
            request_id=f"{message.message_id}.{message.revision}",
            run_id=message.run_id,
            action="input",
            text="Use a cited workflow.",
            identity=OAuthIdentity(tenant_id="workspace.alpha", principal_id=message.member_id),
            revision=message.revision,
            occurred_at=NOW,
        )
    )
    actor = pending.binding.actor.model_copy(update={"session_id": message.conversation_id})
    event = pending.event.model_copy(
        update={"conversation_id": message.conversation_id, "message_id": message.message_id}
    )
    original_message = pending.envelope.message_event
    assert original_message is not None
    message_event = original_message.model_copy(
        update={"conversation_ref": message.conversation_id, "message_ref": message.message_id}
    )
    return PendingKnowledgeIngress(
        binding=pending.binding.model_copy(update={"actor": actor}),
        event=event,
        envelope=pending.envelope.model_copy(update={"message_event": message_event}),
    )


def _private_pending(message: SharedMessage) -> PendingKnowledgeIngress:
    return build_slack_ingress(
        SlackIngressRequest(
            conversation_id=message.conversation_id,
            message_id=message.message_id,
            run_id=message.run_id,
            action="input",
            text="Use a cited workflow.",
            revision=message.revision,
            external_revision=str(message.revision),
            created_revision=str(NOW.timestamp()),
            event_kind=ConversationEventKind.MESSAGE_FINALIZED,
            identity=ChannelIdentityBinding(
                schema_version="trace.channel-identity-binding.v1",
                binding_id=f"binding.{message.member_id}",
                installation_id="installation.slack",
                external_user_id=message.member_id,
                tenant_id="workspace.alpha",
                member_id=message.member_id,
                created_at=NOW,
            ),
            private=message.private,
            channel_id="D1",
            reply_to=None,
            attachments=(),
            observed_at=NOW,
        )
    )


def canonical_run(source: AdmittedSource) -> AgentRun:
    return AgentRun(
        schema_version="trace.agent-run.v1",
        run_id=source.binding.run_id,
        tenant_id=source.binding.actor.workspace_id,
        goal=AgentGoal(objective="Learn a procedure", success_criteria=("record receipt",)),
        budget=AgentBudget(max_tool_calls=2, max_cost_units=4),
        created_at=NOW,
        updated_at=NOW,
    )


def admitted_run(
    fixture: LearningFixture,
    source: AdmittedSource,
    *,
    tool_input: JsonObject | None = None,
) -> tuple[AgentRun, str]:
    created = fixture.agent_runs.create(canonical_run(source))
    invocation_input = {} if tool_input is None else tool_input
    invocation = ToolInvocation(
        tenant_id=created.tenant_id,
        schema_version="trace.tool-invocation.v1",
        invocation_id=f"{created.run_id}:invocation.1",
        run_id=created.run_id,
        step_id=f"{created.run_id}:step.invocation",
        intent_sha256="a" * 64,
        capability_snapshot_sha256="b" * 64,
        descriptor_sha256="c" * 64,
        idempotency_key=f"{created.run_id}:idempotency.1",
        input=invocation_input,
        input_sha256=contract_sha256(invocation_input),
    )
    payload = invocation.model_dump(mode="json")
    record = AgentRecord(
        schema_version="trace.agent-record.v1",
        record_id=invocation.invocation_id,
        run_id=created.run_id,
        kind=AgentRecordKind.INVOCATION,
        payload_schema_version=invocation.schema_version,
        payload=payload,
        payload_sha256=contract_sha256(payload),
        occurred_at=NOW,
    )
    updated = fixture.agent_runs.append_step(
        created,
        AgentStep(
            schema_version="trace.agent-step.v1",
            step_id=invocation.step_id,
            run_id=created.run_id,
            sequence=created.revision,
            kind=AgentStepKind.EXECUTE,
            state="completed",
            input_sha256=invocation.input_sha256,
            output_sha256=invocation.intent_sha256,
            parent_step_sha256=created.head_step_sha256,
            occurred_at=NOW,
        ),
        state=AgentRunState.RUNNING,
        expected_revision=created.revision,
        records=(record,),
    )
    return updated, contract_sha256(invocation)


def terminal_receipt(
    run: AgentRun,
    invocation_sha256: str,
    *,
    disposition: ReceiptDisposition = "succeeded",
    output: JsonObject | None = None,
) -> ToolReceiptRecord:
    return ToolReceiptRecord(
        schema_version="trace.tool-receipt.v1",
        receipt_id=f"{run.run_id}:receipt.1",
        invocation_sha256=invocation_sha256,
        disposition=disposition,
        actual_cost_units=1,
        output_schema_sha256="b" * 64,
        output_sha256="c" * 64 if output is None else contract_sha256(output),
        executor_id="fixture.executor",
        occurred_at=NOW,
    )


def receipt_record(run: AgentRun, receipt: ToolReceiptRecord) -> AgentRecord:
    payload = receipt.model_dump(mode="json")
    return AgentRecord(
        schema_version="trace.agent-record.v1",
        record_id=receipt.receipt_id,
        run_id=run.run_id,
        kind=AgentRecordKind.RECEIPT,
        payload_schema_version=receipt.schema_version,
        payload=payload,
        payload_sha256=contract_sha256(payload),
        occurred_at=receipt.occurred_at,
    )


def output_record(
    run: AgentRun,
    receipt: ToolReceiptRecord,
    capability_id: str,
    output: JsonObject,
) -> AgentRecord:
    payload: JsonObject = {
        "schema_version": "trace.tool-output-evidence.v1",
        "capability_id": capability_id,
        "receipt_sha256": contract_sha256(receipt),
        "output": output,
    }
    return AgentRecord(
        schema_version="trace.agent-record.v1",
        record_id=f"{receipt.receipt_id}:evidence",
        run_id=run.run_id,
        kind=AgentRecordKind.EVIDENCE,
        payload_schema_version="trace.tool-output-evidence.v1",
        payload=payload,
        payload_sha256=contract_sha256(payload),
        occurred_at=receipt.occurred_at,
    )


def terminal_step(run: AgentRun) -> AgentStep:
    return AgentStep(
        schema_version="trace.agent-step.v1",
        step_id=f"{run.run_id}:step.1",
        run_id=run.run_id,
        sequence=run.revision,
        kind=AgentStepKind.VERIFY,
        state="completed",
        input_sha256="d" * 64,
        output_sha256="e" * 64,
        parent_step_sha256=run.head_step_sha256,
        occurred_at=NOW + timedelta(seconds=1),
    )


def append_terminal_receipt(write: TerminalWrite) -> AgentRun:
    records = (receipt_record(write.run, write.receipt),)
    if write.output is not None:
        records = (
            *records,
            output_record(
                write.run,
                write.receipt,
                write.capability_id or write.receipt.executor_id,
                write.output,
            ),
        )
    return write.fixture.agent_runs.append_step(
        write.run,
        terminal_step(write.run),
        state=AgentRunState.RUNNING,
        expected_revision=write.run.revision,
        records=records,
        admission=write.fixture.terminal.for_receipt(
            write.source.binding,
            write.source.receipt,
            write.receipt,
            capability_id=write.capability_id,
        ),
        after_commit=write.fixture.terminal.after_commit,
    )
