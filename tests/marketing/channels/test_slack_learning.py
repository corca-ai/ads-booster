"""Shared Slack feedback learning stays scoped, quiet, and reuseable."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydantic import TypeAdapter

from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.bootstrap.lifecycle import build_installed_knowledge_runtime
from ads_booster.channels.slack_events import SlackEvents
from ads_booster.contracts.agent_run import AgentRecordKind, AgentRunState, contract_sha256
from ads_booster.contracts.reasoning import (
    ReasoningDecision,
    ReasoningProviderReceipt,
    ReasoningResult,
)
from ads_booster.knowledge.configuration import (
    KnowledgeSettings,
    initialize_knowledge_store,
    initialize_local_configuration,
)
from ads_booster.knowledge.contracts import (
    EvidenceKind,
    InstructionAuthority,
    MemoryKind,
    Provenance,
    ScopeKind,
)
from ads_booster.knowledge.evidence_contracts import EvidenceRef
from ads_booster.knowledge.operation_enums import SkillOperationKind, SkillOrigin
from ads_booster.knowledge.skill_contracts import SkillApplyInput, SkillOperation
from ads_booster.providers.codex_cli import CodexCli
from ads_booster.providers.codex_knowledge import CodexKnowledgeProvider
from tests.knowledge.installed_runtime_support import install_curation_provider
from tests.knowledge.procedural_skill_test_support import skill_record
from tests.knowledge.test_curation_remember import MemoryProvider
from tests.marketing.agent_service.test_application import AskThenStopReasoning
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import (
    RecordingReasoning,
    receive,
    setup_events,
)

if TYPE_CHECKING:
    import pytest

    from ads_booster.bootstrap.lifecycle import InstalledKnowledgeRuntime
    from ads_booster.contracts.reasoning import ReasoningRequest
    from ads_booster.knowledge.curation_contracts import (
        CurationBatchDecision,
        CurationBatchJobContext,
    )
    from ads_booster.knowledge.learning_contracts import LearningCounter
    from ads_booster.knowledge.source_contracts import ConversationEvent
    from ads_booster.transport.json_types import JsonObject


def _installed_events(
    tmp_path: Path,
) -> tuple[SlackEvents, InstalledKnowledgeRuntime, list[JsonObject]]:
    original, messages = setup_events(tmp_path)
    settings = KnowledgeSettings(
        root=tmp_path / "knowledge-root",
        control_root=tmp_path / "knowledge-control",
        policy_path=tmp_path / "knowledge-control/policy.json",
    )
    _ = initialize_local_configuration(*settings.require_enabled(), workspace_id="team")
    _ = initialize_knowledge_store(settings)
    installed = build_installed_knowledge_runtime(
        settings=settings,
        service_database=original.commands.application.service.repository.database_path,
        codex=CodexCli(executable=Path("/unused/codex"), model="test"),
        model_id="test",
    )
    try:
        original.commands.application.service.registry = ToolRegistry.from_registrations(
            (), now=NOW
        )
        original.commands.application.service.knowledge = installed.adapter
        original.commands.application.service.install_tool_catalog(installed.adapter, now=NOW)
        owner = SlackEvents(original.commands, "UBOT", frozenset({"C1"}))
        owner.workspace_mentions = True
    except RuntimeError, ValueError:
        installed.runtime.close()
        raise
    else:
        return owner, installed, messages


installed_events = _installed_events


def _prepared_learning_receipt(owner: SlackEvents, run_id: str) -> JsonObject:
    records = owner.commands.application.service.repository.records("team", run_id)
    record = next(
        item
        for item in reversed(records)
        if item.payload_schema_version == "trace.prepared-knowledge-context-record.v1"
    )
    prepared = record.payload["prepared_context"]
    assert isinstance(prepared, dict)
    receipt = prepared["receipt"]
    assert isinstance(receipt, dict)
    return receipt


def _skill_apply_input(event: ConversationEvent, *, created_by: str) -> JsonObject:
    source_ref = EvidenceRef(
        evidence_kind=EvidenceKind.CONVERSATION_EVENT,
        evidence_id=event.message_id,
        revision_id=str(event.revision),
        quote_sha256=sha256(event.text.encode()).hexdigest(),
        scope=event.scope,
        instruction_authority=InstructionAuthority.AUTHORIZED_USER,
        provenance=Provenance.HUMAN_DIRECT,
    )
    record = skill_record(
        skill_id="learned.u1.receipt-rules",
        version="learned.u1.receipt-rules.r1",
        origin=SkillOrigin.AGENT_CREATED,
        protected=False,
        source_refs=(source_ref,),
        created_by=created_by,
    )
    request = SkillApplyInput(
        schema="knowledge.tool.skill-apply.v1",
        operation_id="operation.learned.u1.receipt-rules",
        operations=(
            SkillOperation(
                operation_id="operation.learned.u1.receipt-rules",
                kind=SkillOperationKind.CREATE,
                skill_id=record.skill_id,
                replacement_revision_id=record.version,
                record=record,
                source_refs=record.source_refs,
                reason=(
                    "The current shared correction supplied a reusable, source-linked procedure."
                ),
            ),
        ),
    )
    return request.model_dump(mode="json", by_alias=True)


class BatchDecisionProbe(Protocol):
    def __call__(
        self,
        provider: CodexKnowledgeProvider,
        batch_id: str,
        jobs: tuple[CurationBatchJobContext, ...],
        *,
        timeout_seconds: float,
    ) -> CurationBatchDecision: ...


def _classifier_probe(calls: list[object]) -> BatchDecisionProbe:
    original = CodexKnowledgeProvider.decide_batch

    def record(
        provider: CodexKnowledgeProvider,
        batch_id: str,
        jobs: tuple[CurationBatchJobContext, ...],
        *,
        timeout_seconds: float,
    ) -> CurationBatchDecision:
        calls.append(batch_id)
        return original(provider, batch_id, jobs, timeout_seconds=timeout_seconds)

    return record


def _learning_counter(installed: InstalledKnowledgeRuntime) -> LearningCounter:
    coordinator = installed.adapter.learning
    assert coordinator is not None
    return coordinator.counter("team")


def _reasoning_result(request: ReasoningRequest, decision: ReasoningDecision) -> ReasoningResult:
    return ReasoningResult(
        schema_version="trace.reasoning-result.v1",
        decision=decision,
        receipt=ReasoningProviderReceipt(
            schema_version="trace.reasoning-provider-receipt.v1",
            provider_id="fake.reasoning",
            model_id="fake-model",
            request_sha256=contract_sha256(request),
            output_schema_sha256="d" * 64,
            decision_sha256=contract_sha256(decision),
        ),
    )


class ApplyLearningThenStop:
    def __init__(self, tool_input: JsonObject) -> None:
        self.tool_input: JsonObject = tool_input
        self.requests: list[ReasoningRequest] = []

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        self.requests.append(request)
        has_tool_result = any(
            item.get("schema_version") == "trace.tool-output-evidence.v1"
            for item in request.evidence
        )
        decision = ReasoningDecision(
            schema_version="trace.reasoning-decision.v1",
            action="stop" if has_tool_result else "invoke_tool",
            capability_id=None if has_tool_result else "skill_apply",
            tool_input=None if has_tool_result else self.tool_input,
            expected_outcome="Persist one source-bound shared procedure.",
            reasoning_summary="Use the bounded learning tool once, then finish.",
        )
        return _reasoning_result(request, decision)


class RequestMoreInput:
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        return _reasoning_result(
            request,
            ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="request_input",
                expected_outcome="Keep the ongoing task open while its correction is retained.",
                reasoning_summary="Further task details are still needed.",
            ),
        )


def test_channel_cannot_publish_workspace_skill(tmp_path: Path) -> None:
    # Given: an installed runtime and an actor admitted only to the Slack channel.
    owner, installed, _ = _installed_events(tmp_path)
    try:
        # When: U1 attempts a workspace skill write from the channel source.
        receive(owner, user="U1", text="<@UBOT> 앞으로는 완료된 도구 receipt만 절차로 남겨줘")
        assert owner.work_once(now=NOW)
        admitted = owner.store.latest_knowledge_event(owner._message_id("C1", "100.001"))  # pyright: ignore[reportPrivateUsage]
        assert admitted is not None
        owner.commands.application.service.reasoning = ApplyLearningThenStop(
            _skill_apply_input(admitted[0], created_by=admitted[1].actor.actor_id)
        )
        while owner.work_once(now=NOW):
            pass
        u1_run_id = owner.commands.application.service.repository.list_runs("team")[0].run_id
        u1_run = owner.commands.application.service.repository.get("team", u1_run_id)
        assert u1_run is not None
        assert u1_run.state is AgentRunState.COMPLETED
        u1_records = owner.commands.application.service.repository.records("team", u1_run_id)
        invocation = next(item for item in u1_records if item.kind is AgentRecordKind.INVOCATION)
        tool_receipt = next(item for item in u1_records if item.kind is AgentRecordKind.RECEIPT)
        assert tool_receipt.payload["invocation_sha256"] == invocation.payload_sha256
        assert tool_receipt.payload["disposition"] == "failed"
        output_record = next(
            item
            for item in u1_records
            if item.payload_schema_version == "trace.tool-output-evidence.v1"
        )
        output = output_record.payload["output"]
        assert isinstance(output, dict)
        assert output["error_code"] == "skill_shared_write_required"
        assert (
            installed.adapter.repository.read_skill(admitted[1].actor, "learned.u1.receipt-rules")
            is None
        )
        with installed.adapter.repository.connection() as connection:
            row = TypeAdapter(tuple[int]).validate_python(
                connection.execute(
                    "SELECT COUNT(*) FROM skills WHERE workspace_id=? AND skill_id=?",
                    ("team", "learned.u1.receipt-rules"),
                ).fetchone()
            )
        assert row[0] == 0
    finally:
        installed.runtime.close()


def test_u2_new_thread_reads_u1_learning(tmp_path: Path) -> None:
    # Given: two admitted Slack users and the installed channel memory writer.
    owner, installed, _ = _installed_events(tmp_path)
    install_curation_provider(installed, MemoryProvider())
    try:
        # When: U1's source is committed as channel memory before U2 opens a new thread.
        receive(
            owner,
            user="U1",
            text="<@UBOT> 앞으로는 완료된 도구 receipt만 절차로 남겨줘",
            ts=str(NOW.timestamp() - 2),
        )
        while owner.work_once(now=NOW):
            pass
        installed.runtime.run_until_idle(flush_batches=True)
        u1_run = owner.commands.application.service.repository.list_runs("team")[0]
        binding = installed.adapter.ingress.binding_for_run(u1_run.run_id)
        assert binding is not None
        document_id = installed.adapter.repository.find_memory_document_id(
            binding.actor, MemoryKind.CORE, None, None
        )
        assert document_id is not None
        committed = installed.adapter.repository.read_memory(binding.actor, document_id)
        assert committed is not None
        assert committed.document.owned_scope.kind is ScopeKind.CHANNEL
        assert committed.document.owned_scope.channel_id == "C1"
        assert any("완료된 도구 receipt" in entry.text for entry in committed.entries)
        receive(
            owner,
            user="U2",
            text="<@UBOT> 새 작업에서도 저장된 절차를 확인해줘",
            ts=str(NOW.timestamp() - 1),
        )
        while owner.work_once(now=NOW):
            pass

        # Then: U2's different Run selects the exact committed channel memory revision.
        runs = owner.commands.application.service.repository.list_runs("team")
        u2_run = next(run for run in runs if run.run_id != u1_run.run_id)
        receipt = _prepared_learning_receipt(owner, u2_run.run_id)
        selected = receipt["selected_memory_revisions"]
        assert isinstance(selected, list)
        assert any(
            isinstance(item, dict)
            and item["document_id"] == document_id
            and item["revision_id"] == committed.revision.revision_id
            for item in selected
        )
    finally:
        installed.runtime.close()


def test_correction_during_nonterminal_run_keeps_the_bound_run(
    tmp_path: Path,
) -> None:
    # Given: a shared Slack Run that is still awaiting input and has installed learning.
    owner, installed, _ = _installed_events(tmp_path)
    install_curation_provider(installed, MemoryProvider())
    owner.commands.application.service.reasoning = AskThenStopReasoning()
    try:
        receive(owner, ts=str(NOW.timestamp() - 2))
        while owner.work_once(now=NOW):
            pass
        run = owner.commands.application.service.repository.list_runs("team")[0]

        # When: its admitted member supplies a correction in the original thread.
        receive(
            owner,
            type="message",
            text="완료되지 않은 시도는 성공 절차로 저장하지 마",
            ts=str(NOW.timestamp() - 1),
            thread_ts=str(NOW.timestamp() - 2),
        )
        owner.commands.application.service.reasoning = RequestMoreInput()
        while owner.work_once(now=NOW):
            pass
        installed.runtime.run_until_idle(flush_batches=True)

        # Then: the correction remains bound to that Run and does not require terminality first.
        bound = owner.store.knowledge_ingress.execution_run_for_message(
            owner._message_id("C1", str(NOW.timestamp() - 1))  # pyright: ignore[reportPrivateUsage]
        )
        assert bound == run.run_id
        assert len(owner.commands.application.service.repository.list_runs("team")) == 1
        binding = owner.store.knowledge_ingress.binding_for_run(run.run_id)
        assert binding is not None
        document_id = installed.adapter.repository.find_memory_document_id(
            binding.actor, MemoryKind.CORE, None, None
        )
        assert document_id is not None
        learned = installed.adapter.repository.read_memory(binding.actor, document_id)
        assert learned is not None
        assert learned.document.owned_scope == binding.actor.conversation_scope
        assert any("완료되지 않은 시도" in entry.text for entry in learned.entries)
        current_run = owner.commands.application.service.repository.get("team", run.run_id)
        assert current_run is not None
        assert current_run.state is AgentRunState.AWAITING_INPUT
    finally:
        installed.runtime.close()


def test_reasoned_message_has_no_extra_classifier_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a normal foreground-reasoned message and a probe on the curation classifier seam.
    owner, installed, _ = _installed_events(tmp_path)
    reasoning = RecordingReasoning()
    classifier_calls: list[object] = []
    monkeypatch.setattr(CodexKnowledgeProvider, "decide_batch", _classifier_probe(classifier_calls))
    owner.commands.application.service.reasoning = reasoning
    try:
        # When: Slack processes one ordinary reasoned message.
        receive(owner)
        while owner.work_once(now=NOW):
            pass

        # Then: its existing reasoning call is reused and no classifier is added.
        assert len(reasoning.requests) == 1
        assert classifier_calls == []
    finally:
        installed.runtime.close()


def test_summary_branch_has_at_most_one_classifier_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an active shared thread whose status path has no foreground reasoning call.
    owner, installed, _ = _installed_events(tmp_path)
    classifier_calls: list[object] = []
    monkeypatch.setattr(CodexKnowledgeProvider, "decide_batch", _classifier_probe(classifier_calls))
    try:
        receive(owner)
        while owner.work_once(now=NOW):
            pass

        # When: the same thread takes its summary-only correction branch.
        receive(
            owner,
            type="message",
            text="상태",
            ts="100.002",
            thread_ts="100.001",
        )
        while owner.work_once(now=NOW):
            pass

        # Then: urgent classification is bounded to one call for that source revision.
        assert len(classifier_calls) <= 1
    finally:
        installed.runtime.close()


def test_normal_learning_is_silent_and_dm_does_not_increment_shared_learning(
    tmp_path: Path,
) -> None:
    # Given: installed shared learning and a sender that records Slack posts and updates.
    owner, installed, messages = _installed_events(tmp_path)

    try:
        # When: a shared learning message completes and a DM arrives afterwards.
        receive(owner, text="<@UBOT> 완료 receipt만 재사용 규칙으로 반영해줘")
        while owner.work_once(now=NOW):
            pass
        counter_before_dm = _learning_counter(installed)
        delivered_before_dm = len(messages)
        receive(
            owner,
            type="message",
            channel="D1",
            channel_type="im",
            text="개인 규칙",
            ts="100.002",
        )
        while owner.work_once(now=NOW):
            pass

        # Then: successful background learning is silent and the DM adds no shared turn.
        counter = _learning_counter(installed)
        dm_deliveries = messages[delivered_before_dm:]
        assert dm_deliveries
        assert all(payload["channel"] == "D1" for payload in dm_deliveries)
        assert sum("ts" not in payload for payload in dm_deliveries) == 1
        assert all(payload.get("ts") == "123.456" for payload in dm_deliveries[1:])
        assert all(str(payload["text"]).startswith("⏳ ") for payload in dm_deliveries[:-1])
        assert dm_deliveries[-1]["text"] == "No execution tool is needed"
        assert counter.conversation_turns == counter_before_dm.conversation_turns
    finally:
        installed.runtime.close()
