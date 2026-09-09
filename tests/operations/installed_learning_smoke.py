"""Opt-in fresh-wheel rehearsal for shared feedback learning.

Run with an installed interpreter from outside the checkout. The fixture mode
replaces only the curation provider and Slack sender; the HTTP router, signed
Slack ingress, service, and knowledge runtime are the installed composition.
"""

# pyright: reportAny=false, reportExplicitAny=false, reportPrivateUsage=false
# ruff: noqa: C901, E501, EM101, EM102, N803, PLR0912, PLR0913, PLR0915, S603, S106
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from typing import TYPE_CHECKING
from unittest.mock import patch

from ads_booster.agent.service.skills import SKILLS
from ads_booster.bootstrap.channel_setup import slack_from_env
from ads_booster.bootstrap.lifecycle import (
    InstalledServicePaths,
    build_installed_knowledge_runtime,
    build_installed_marketing_agent_service,
)
from ads_booster.channels.http import http_api
from ads_booster.channels.http.http_api import MarketingAgentApi, serve_marketing_agent_api
from ads_booster.channels.slack_events import SlackEvents
from ads_booster.contracts.agent_run import AgentRecordKind, contract_sha256
from ads_booster.contracts.knowledge_selection import KnowledgeActionKind
from ads_booster.contracts.reasoning import (
    ReasoningDecision,
    ReasoningProviderReceipt,
    ReasoningRequest,
    ReasoningResult,
)
from ads_booster.knowledge.configuration import (
    KnowledgeSettings,
    initialize_knowledge_store,
    initialize_local_configuration,
)
from ads_booster.knowledge.contract_types import EvidenceKind, InstructionAuthority, Provenance
from ads_booster.knowledge.curation_contracts import (
    CurationBatchDecision,
    CurationBatchJobContext,
    CurationBatchJobDecision,
    CurationDecision,
    CurationDecisionAction,
)
from ads_booster.knowledge.evidence_contracts import EvidenceRef
from ads_booster.knowledge.governance_contracts import AppliesTo
from ads_booster.knowledge.learning_contracts import LearningPurpose
from ads_booster.knowledge.operation_enums import SkillOperationKind, SkillOrigin
from ads_booster.knowledge.repository_learning import LearningReviewCoordinator
from ads_booster.knowledge.skill_contracts import SkillApplyInput, SkillOperation, SkillRecord
from ads_booster.knowledge.source_contracts import ConversationEvent
from ads_booster.knowledge.tool_contracts import (
    KnowledgeToolName,
    QuestionStatus,
    ToolResult,
    ToolResultStatus,
)
from ads_booster.providers.codex_cli import CodexCli, resolve_codex_executable
from ads_booster.providers.codex_knowledge import CodexKnowledgeProvider
from ads_booster.transport.http import create_http_client

if TYPE_CHECKING:
    from collections.abc import Generator

    from ads_booster.agent.service.application import MarketingAgentService
    from ads_booster.bootstrap.lifecycle import InstalledKnowledgeRuntime
    from ads_booster.transport.json_types import JsonObject


@dataclass(slots=True)
class FixtureSender:
    """Captures durable sender intents without contacting Slack."""

    messages: list[JsonObject] = field(default_factory=list)
    unknown: bool = False

    def send(self, payload: JsonObject) -> JsonObject:
        self.messages.append(payload)
        if self.unknown:
            raise TimeoutError("fixture_unknown_send")
        return {"ok": True, "ts": f"fixture.{len(self.messages):03d}"}


@dataclass(slots=True)
class FixtureReasoning:
    """Records prepared context while leaving feedback admission to the real service."""

    requests: list[ReasoningRequest] = field(default_factory=list)

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        self.requests.append(request)
        decision = ReasoningDecision(
            schema_version="trace.reasoning-decision.v1",
            action="stop",
            expected_outcome="Synthetic feedback rehearsal records the planning boundary",
            reasoning_summary="fixture stop",
        )
        return ReasoningResult(
            schema_version="trace.reasoning-result.v1",
            decision=decision,
            receipt=ReasoningProviderReceipt(
                schema_version="trace.reasoning-provider-receipt.v1",
                provider_id="fixture.reasoning",
                model_id="fixture",
                request_sha256=contract_sha256(request),
                output_schema_sha256="a" * 64,
                decision_sha256=contract_sha256(decision),
            ),
        )


@dataclass(frozen=True, slots=True)
class SyntheticCase:
    case_id: str
    expected: str


_CASES = (
    SyntheticCase(
        "learning-before-after", "U1 correction is source-linked in U2 new-thread context"
    ),
    SyntheticCase(
        "restart-new-member-thread", "runtime recreation retains committed shared learning"
    ),
    SyntheticCase("shared-private", "DM is excluded from workspace learning counters and writes"),
    SyntheticCase("builtin-protection", "background learning cannot mutate a builtin or override"),
    SyntheticCase(
        "conflict-unknown-send", "one original-thread question remains unknown and is not retried"
    ),
    SyntheticCase("normal-silence", "a normal learning round creates no Slack notification intent"),
)


def _fixture_batch(
    _self: CodexKnowledgeProvider,
    batch_id: str,
    jobs: tuple[CurationBatchJobContext, ...],
    *,
    timeout_seconds: float,
) -> CurationBatchDecision:
    """Script source-bound learning only through the installed guarded ToolHost."""
    _ = timeout_seconds
    return CurationBatchDecision(
        schema="knowledge.curation-batch-decision.v1",
        batch_id=batch_id,
        decisions=tuple(
            CurationBatchJobDecision(
                job_id=job.request.job_id,
                decision=_fixture_decision(job),
            )
            for job in jobs
        ),
    )


def _fixture_decision(job: CurationBatchJobContext) -> CurationDecision:
    """Return a source-bound question only for the explicit conflict case."""
    conflict = any("CONFLICT" in excerpt.text.upper() for excerpt in job.request.excerpts)
    if not conflict:
        if (
            job.request.learning_purpose is not None
            and job.request.learning_purpose is LearningPurpose.CONVERSATIONAL_FEEDBACK
            and not job.observations
        ):
            authenticated = job.request.authenticated_user_event
            if authenticated is None:
                raise RuntimeError("fixture_learning_requires_authenticated_event")
            record = _installed_skill_record(
                skill_id="learned.fixture-receipt-procedure",
                source_ref=authenticated.evidence_ref,
                actor_id=authenticated.authority_ref.actor_ref,
            )
            operation_id = f"operation.fixture.learn.{job.request.job_id}"
            request = SkillApplyInput(
                schema="knowledge.tool.skill-apply.v1",
                operation_id=operation_id,
                operations=(
                    SkillOperation(
                        operation_id=operation_id,
                        kind=SkillOperationKind.CREATE,
                        skill_id=record.skill_id,
                        replacement_revision_id=record.version,
                        record=record,
                        source_refs=record.source_refs,
                        reason="A source-linked fixture procedure is reusable after review.",
                    ),
                ),
            )
            return CurationDecision(
                schema="knowledge.curation-decision.v1",
                action=CurationDecisionAction.TOOL_CALL,
                tool_name=KnowledgeToolName.SKILL_APPLY,
                tool_arguments_json=request.model_dump_json(by_alias=True),
            )
        return CurationDecision(
            schema="knowledge.curation-decision.v1",
            action=CurationDecisionAction.FINISH,
            finish_summary="fixture completion without invented learning",
        )
    authenticated = job.request.authenticated_user_event
    if authenticated is None:
        raise RuntimeError("fixture_conflict_requires_authenticated_event")
    return CurationDecision(
        schema="knowledge.curation-decision.v1",
        action=CurationDecisionAction.QUESTION,
        question_arguments_json=json.dumps(
            {
                "schema": "knowledge.tool.question.v1",
                "question_id": "question.installed-learning-conflict",
                "problem": "Two current shared procedures conflict.",
                "evidence_ids": [authenticated.evidence_ref.evidence_id],
                "source_event_id": authenticated.evidence_ref.evidence_id,
                "recommendation": "Choose the procedure backed by the current source revision.",
            }
        ),
    )


@contextmanager
def _loopback(api: MarketingAgentApi) -> Generator[str]:
    """Run the installed HTTP server on an ephemeral captured loopback port."""
    started = Event()
    captured: list[ThreadingHTTPServer] = []
    original = ThreadingHTTPServer

    class CapturingServer(original):
        def __init__(
            self,
            server_address: tuple[str | bytes | bytearray, int],
            RequestHandlerClass: type[BaseHTTPRequestHandler],
            bind_and_activate: bool = True,
        ) -> None:
            super().__init__(server_address, RequestHandlerClass, bind_and_activate)
            captured.append(self)
            started.set()

    with patch.object(http_api, "ThreadingHTTPServer", CapturingServer):
        thread = Thread(
            target=serve_marketing_agent_api,
            kwargs={"api": api, "host": "127.0.0.1", "port": 0},
            daemon=True,
        )
        thread.start()
        if not started.wait(5):
            raise RuntimeError("installed_loopback_server_not_started")
        server = captured[0]
        host, port = server.server_address[:2]
        try:
            yield f"http://{host}:{port}"
        finally:
            server.shutdown()
            thread.join(timeout=5)


def _event(
    *,
    event_id: str,
    user: str,
    channel: str,
    text: str,
    timestamp: str,
    private: bool = False,
    thread_timestamp: str = "",
) -> bytes:
    return json.dumps(
        {
            "type": "event_callback",
            "api_app_id": "A1",
            "team_id": "T1",
            "event_id": event_id,
            "event": {
                "type": "message" if private else "app_mention",
                "channel": channel,
                "user": user,
                "text": text,
                "ts": timestamp,
                "channel_type": "im" if private else "channel",
                **({"thread_ts": thread_timestamp} if thread_timestamp else {}),
            },
        },
        separators=(",", ":"),
    ).encode()


def _post_signed(base_url: str, body: bytes) -> JsonObject:
    timestamp = str(int(datetime.now(UTC).timestamp()))
    signature = (
        "v0="
        + hmac.new(
            b"fixture-signing", b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256
        ).hexdigest()
    )
    with create_http_client(read_timeout=5) as client:
        response = client.post_json(
            f"{base_url}/channels/slack/events",
            json.loads(body),
            {
                "x-slack-request-timestamp": timestamp,
                "x-slack-signature": signature,
            },
        )
    if response.status_code != 200:
        raise RuntimeError(f"installed_signed_ingress_status:{response.status_code}")
    return response.json_object()


def _drain(events: SlackEvents, runtime: InstalledKnowledgeRuntime) -> None:
    while events.work_once(now=datetime.now(UTC)):
        pass
    runtime.runtime.run_until_idle(flush_batches=False)


def _cli_smoke(home: Path) -> tuple[str, str]:
    """Exercise installed command exposure in a separate process without patches."""
    command = Path(sys.executable).with_name("trace-marketing")
    doctor = subprocess.run(
        [str(command), "service", "doctor", "--home", str(home)],
        check=False,
        capture_output=True,
        text=True,
    )
    help_result = subprocess.run(
        [str(command), "service", "run", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    if doctor.returncode != 0 or help_result.returncode != 0:
        raise RuntimeError("installed_cli_smoke_failed")
    return doctor.stdout, help_result.stdout


def _learning_round_count(runtime: InstalledKnowledgeRuntime) -> int:
    """Read the durable learning-round observable owned by the knowledge repository."""
    with runtime.adapter.repository.connection() as database:
        row = database.execute(
            """SELECT count(*) FROM learning_rounds
            WHERE workspace_id=? AND state IN ('ready', 'running', 'completed')""",
            (runtime.runtime.workspace_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError("installed_learning_round_count_missing")
    return int(row[0])


def _learning_source_ref(runtime: InstalledKnowledgeRuntime) -> EvidenceRef:
    """Use only the server-recorded source reference from the sealed learning round."""
    rounds = LearningReviewCoordinator(runtime.adapter.repository).rounds(
        runtime.runtime.workspace_id
    )
    if not rounds:
        raise RuntimeError("installed_learning_round_missing")
    round_id = rounds[0].round_id
    with runtime.adapter.repository.connection() as database:
        row = database.execute(
            """SELECT event_json FROM learning_admissions
            WHERE sealed_round_id=? AND invalidated=0 AND event_json IS NOT NULL
            ORDER BY occurred_at,admission_id LIMIT 1""",
            (round_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError("installed_learning_event_missing")
    event = ConversationEvent.model_validate_json(str(row[0]))
    return EvidenceRef(
        evidence_kind=EvidenceKind.CONVERSATION_EVENT,
        evidence_id=event.message_id,
        revision_id=str(event.revision),
        quote_sha256=hashlib.sha256(event.text.encode()).hexdigest(),
        scope=event.scope,
        instruction_authority=InstructionAuthority.AUTHORIZED_USER,
        provenance=Provenance.HUMAN_DIRECT,
    )


def _installed_skill_record(
    *,
    skill_id: str,
    source_ref: EvidenceRef,
    actor_id: str,
) -> SkillRecord:
    """Create a deterministic attempted shadow for the real guarded ToolHost."""
    version = f"{skill_id}.automatic.r1"
    description = "Attempt to shadow an installed builtin."
    procedure = "Use only a source-linked successful procedure."
    applicability = AppliesTo(action_kinds=(KnowledgeActionKind.TEAM_CHAT,))
    stable: JsonObject = {
        "skill_id": skill_id,
        "version": version,
        "description": description,
        "procedure": procedure,
        "pitfalls": [],
        "verification": [],
        "required_capability_ids": [],
        "origin": SkillOrigin.AGENT_CREATED.value,
        "protected": False,
        "base_builtin_digest": None,
        "source_refs": [source_ref.model_dump(mode="json")],
        "applicability": applicability.model_dump(mode="json", exclude_defaults=True),
        "created_by": actor_id,
    }
    now = datetime.now(UTC)
    return SkillRecord(
        schema="knowledge.skill-record.v1",
        skill_id=skill_id,
        version=version,
        description=description,
        procedure=procedure,
        pitfalls=(),
        verification=(),
        required_capability_ids=(),
        origin=SkillOrigin.AGENT_CREATED,
        protected=False,
        base_builtin_digest=None,
        digest=contract_sha256(stable),
        applicability=applicability,
        source_refs=(source_ref,),
        created_by=actor_id,
        created_at=now,
        updated_at=now,
    )


def _assert_builtin_autoedit_rejected(
    service: MarketingAgentService,
    runtime: InstalledKnowledgeRuntime,
) -> None:
    """Prove every installed builtin rejects a background agent-created shadow."""
    run = service.repository.list_runs(runtime.runtime.workspace_id)[0]
    context = runtime.adapter.resolve_invocation(run.run_id, "installed-skill-autoedit")
    source_ref = _learning_source_ref(runtime)
    results: list[ToolResult] = []
    for builtin in SKILLS:
        record = _installed_skill_record(
            skill_id=builtin.skill_id,
            source_ref=source_ref,
            actor_id=context.actor.actor_id,
        )
        operation_id = f"operation.installed.autoedit.{builtin.skill_id}"
        request = SkillApplyInput(
            schema="knowledge.tool.skill-apply.v1",
            operation_id=operation_id,
            operations=(
                SkillOperation(
                    operation_id=operation_id,
                    kind=SkillOperationKind.CREATE,
                    skill_id=record.skill_id,
                    replacement_revision_id=record.version,
                    record=record,
                    source_refs=record.source_refs,
                    reason="Background learning attempted to shadow an installed builtin.",
                ),
            ),
        )
        results.append(
            runtime.adapter.host.execute(
                "skill_apply", request.model_dump(mode="json", by_alias=True), context
            )
        )
    if not results or any(result.status is not ToolResultStatus.REJECTED for result in results):
        raise RuntimeError("installed_builtin_autoedit_not_rejected")


def _installed_codex(mode: str) -> CodexCli:
    if mode == "fixture":
        return CodexCli(executable=Path("/fixture/codex"), model="fixture")
    executable = resolve_codex_executable()
    if executable is None:
        raise RuntimeError("official_codex_cli_unavailable")
    return CodexCli(
        executable=executable,
        model=os.environ.get("TRACE_MARKETING_MODEL") or "gpt-6-astra",
    )


def _model_id(codex: CodexCli) -> str:
    if codex.model is None:
        raise RuntimeError("installed_model_id_missing")
    return codex.model


def _assert_paths(home: Path, output: Path) -> None:
    if not home.is_absolute() or not output.is_absolute():
        raise RuntimeError("installed_learning_paths_must_be_absolute")
    if os.environ.get("PYTHONPATH"):
        raise RuntimeError("installed_learning_pythonpath_must_be_empty")
    package_path = sys.modules["ads_booster"].__file__
    if package_path is None:
        raise RuntimeError("installed_learning_package_path_missing")
    package_file = Path(package_path).resolve()
    if Path.cwd().resolve() in package_file.parents:
        raise RuntimeError("installed_learning_imported_from_checkout")


def _run(mode: str, home: Path) -> JsonObject:
    started = time.monotonic()
    settings = KnowledgeSettings(
        root=home / "knowledge",
        control_root=home / "knowledge-control",
        policy_path=home / "knowledge-control" / "policy.json",
    )
    identity, _ = initialize_local_configuration(*settings.require_enabled(), workspace_id="trace")
    _ = initialize_knowledge_store(settings)
    paths = InstalledServicePaths(home / "service")
    paths.prepare()
    doctor, service_help = _cli_smoke(home)
    codex = _installed_codex(mode)
    model_id = _model_id(codex)
    provider_calls = 0
    original_batch = CodexKnowledgeProvider.decide_batch

    def observed_batch(
        provider: CodexKnowledgeProvider,
        batch_id: str,
        jobs: tuple[CurationBatchJobContext, ...],
        *,
        timeout_seconds: float,
    ) -> CurationBatchDecision:
        nonlocal provider_calls
        provider_calls += 1
        if mode == "fixture":
            return _fixture_batch(provider, batch_id, jobs, timeout_seconds=timeout_seconds)
        return original_batch(provider, batch_id, jobs, timeout_seconds=timeout_seconds)

    with patch.object(CodexKnowledgeProvider, "decide_batch", observed_batch):
        installed = build_installed_knowledge_runtime(
            settings=settings,
            service_database=paths.database,
            codex=codex,
            model_id=model_id,
        )
        reasoning = FixtureReasoning()
        service: MarketingAgentService = replace(
            build_installed_marketing_agent_service(
                paths=paths,
                codex_executable=codex.executable,
                model_id=model_id,
                timeout_seconds=30,
                knowledge=installed.adapter,
            ),
            reasoning=reasoning,
        )
        installation = home / "slack-installation.json"
        _ = installation.write_text(
            json.dumps(
                {
                    "app_id": "A1",
                    "team_id": "T1",
                    "tenant_id": "trace",
                    "members": [
                        {"slack_user_id": "U1", "member_id": "member-one"},
                        {"slack_user_id": "U2", "member_id": "member-two"},
                    ],
                }
            )
        )
        commands = slack_from_env(
            {
                "TRACE_MARKETING_SLACK_INSTALLATION": str(installation),
                "TRACE_MARKETING_SLACK_SIGNING_SECRET": "fixture-signing",
                "TRACE_MARKETING_SLACK_BOT_TOKEN": "fixture-token",
                "TRACE_MARKETING_SLACK_CHANNEL_ID": "C1",
                "TRACE_MARKETING_PUBLIC_ORIGIN": "https://fixture.invalid",
            },
            service,
            tenant_id="trace",
        )
        if commands is None:
            raise RuntimeError("installed_fixture_slack_commands_missing")
        sender = FixtureSender()
        commands.sender = sender.send
        events = SlackEvents(commands, "UBOT", frozenset({"C1"}))
        api = MarketingAgentApi(
            service=service,
            tenant_id="trace",
            principal_id=identity.actor_id,
            bearer_token="fixture-token",
            slack_events=events,
            knowledge_ingress=installed.adapter.ingress,
        )
        try:
            with _loopback(api) as base_url:
                for index in range(9):
                    acknowledged = _post_signed(
                        base_url,
                        _event(
                            event_id=f"Ev{index:02d}",
                            user="U1",
                            channel="C1",
                            text=(
                                "<@UBOT> Correction: do not state an unverified launch date."
                                if index == 0
                                else (
                                    "<@UBOT> CONFLICT: the two current shared procedures disagree."
                                    if index == 8
                                    else f"<@UBOT> shared learning turn {index}"
                                )
                            ),
                            timestamp=f"{100 + index}.001",
                        ),
                    )
                    if acknowledged != {"ok": True}:
                        raise RuntimeError("installed_signed_ingress_not_acknowledged")
                    while events.work_once(now=datetime.now(UTC)):
                        pass
                    messages_before_curation = len(sender.messages)
                    installed.runtime.run_until_idle(flush_batches=False)
                    if len(sender.messages) != messages_before_curation:
                        raise RuntimeError("installed_normal_learning_was_not_silent")
                    if _learning_round_count(installed) != 0:
                        raise RuntimeError(f"installed_learning_rounds_at_turn_{index + 1}")
                private_acknowledged = _post_signed(
                    base_url,
                    _event(
                        event_id="EvPrivate",
                        user="U1",
                        channel="D1",
                        text="Private correction must stay private.",
                        timestamp="150.001",
                        private=True,
                    ),
                )
                if private_acknowledged != {"ok": True}:
                    raise RuntimeError("installed_private_ingress_not_acknowledged")
                _drain(events, installed)
                if _learning_round_count(installed) != 0:
                    raise RuntimeError("installed_private_message_counted_as_shared_learning")
                acknowledged = _post_signed(
                    base_url,
                    _event(
                        event_id="Ev09",
                        user="U1",
                        channel="C1",
                        text="<@UBOT> shared learning turn 9",
                        timestamp="109.001",
                    ),
                )
                if acknowledged != {"ok": True}:
                    raise RuntimeError("installed_tenth_shared_ingress_not_acknowledged")
                while events.work_once(now=datetime.now(UTC)):
                    pass
                messages_before_curation = len(sender.messages)
                installed.runtime.run_until_idle(flush_batches=False)
                if len(sender.messages) != messages_before_curation:
                    raise RuntimeError("installed_normal_learning_was_not_silent")
                if _learning_round_count(installed) != 1:
                    raise RuntimeError("installed_learning_rounds_at_turn_10")
                installed.runtime.run_until_idle(flush_batches=True)
                learning_source_digest = contract_sha256(_learning_source_ref(installed))
                _assert_builtin_autoedit_rejected(service, installed)
                conflict_conversation = next(
                    (item for item in events.store.conversations() if item.thread_ts == "108.001"),
                    None,
                )
                if conflict_conversation is None:
                    raise RuntimeError("installed_conflict_conversation_missing")
                sender.unknown = True
                while events.work_once(now=datetime.now(UTC)):
                    pass
                unknown_calls = len(sender.messages)
                events.recover()
                while events.work_once(now=datetime.now(UTC)):
                    pass
                if len(sender.messages) != unknown_calls:
                    raise RuntimeError("installed_unknown_question_send_retried")
                with events.store.connect() as database:
                    unknown_row = database.execute(
                        """SELECT count(*) FROM slack_message_jobs
                        WHERE notification_state='unknown'"""
                    ).fetchone()
                if unknown_row is None or int(unknown_row[0]) != 1:
                    raise RuntimeError("installed_conflict_unknown_delivery_not_durable")
                sender.unknown = False
                conflict_run = service.repository.get("trace", conflict_conversation.current_run)
                if conflict_run is None:
                    raise RuntimeError("installed_conflict_run_missing")
                conflict_context = installed.adapter.resolve_invocation(
                    conflict_run.run_id, "installed-question-state"
                )
                question = installed.adapter.host.state.question(
                    conflict_context.actor, "question.installed-learning-conflict"
                )
                if question is None or question.status is not QuestionStatus.PENDING:
                    raise RuntimeError("installed_conflict_question_not_pending")
                if any(
                    record.kind is AgentRecordKind.APPROVAL
                    for record in service.repository.records("trace", conflict_run.run_id)
                ):
                    raise RuntimeError("installed_learning_question_became_tool_approval")
                answer_acknowledged = _post_signed(
                    base_url,
                    _event(
                        event_id="EvConflictAnswer",
                        user="U2",
                        channel="C1",
                        text=(
                            "<@UBOT> question.installed-learning-conflict: "
                            "use the current source revision"
                        ),
                        timestamp="300.002",
                        thread_timestamp="108.001",
                    ),
                )
                if answer_acknowledged != {"ok": True}:
                    raise RuntimeError("installed_conflict_answer_not_acknowledged")
                _drain(events, installed)
                answered = installed.adapter.host.state.question(
                    conflict_context.actor, "question.installed-learning-conflict"
                )
                if answered is None or answered.status is not QuestionStatus.ANSWERED:
                    raise RuntimeError("installed_conflict_answer_not_resolved")
            installed.runtime.close()
            installed = build_installed_knowledge_runtime(
                settings=settings,
                service_database=paths.database,
                codex=codex,
                model_id=model_id,
            )
            service = replace(
                build_installed_marketing_agent_service(
                    paths=paths,
                    codex_executable=codex.executable,
                    model_id=model_id,
                    timeout_seconds=30,
                    knowledge=installed.adapter,
                ),
                reasoning=reasoning,
            )
            commands = slack_from_env(
                {
                    "TRACE_MARKETING_SLACK_INSTALLATION": str(installation),
                    "TRACE_MARKETING_SLACK_SIGNING_SECRET": "fixture-signing",
                    "TRACE_MARKETING_SLACK_BOT_TOKEN": "fixture-token",
                    "TRACE_MARKETING_SLACK_CHANNEL_ID": "C1",
                    "TRACE_MARKETING_PUBLIC_ORIGIN": "https://fixture.invalid",
                },
                service,
                tenant_id="trace",
            )
            if commands is None:
                raise RuntimeError("installed_restart_slack_commands_missing")
            commands.sender = sender.send
            events = SlackEvents(commands, "UBOT", frozenset({"C1"}))
            api = MarketingAgentApi(
                service=service,
                tenant_id="trace",
                principal_id=identity.actor_id,
                bearer_token="fixture-token",
                slack_events=events,
                knowledge_ingress=installed.adapter.ingress,
            )
            before = len(reasoning.requests)
            with _loopback(api) as base_url:
                acknowledged = _post_signed(
                    base_url,
                    _event(
                        event_id="EvU2",
                        user="U2",
                        channel="C1",
                        # This fixture checks exact revision/source retention, not semantic search.
                        text="<@UBOT> use learned.fixture-receipt-procedure",
                        timestamp="200.001",
                    ),
                )
                if acknowledged != {"ok": True}:
                    raise RuntimeError("installed_second_member_not_acknowledged")
                _drain(events, installed)
            if len(reasoning.requests) <= before:
                raise RuntimeError("installed_new_thread_not_planned")
            prepared = reasoning.requests[-1].prepared_context
            learned = (
                None
                if prepared is None
                else next(
                    (
                        item
                        for item in prepared.receipt.selected_skill_revisions
                        if item.skill_id == "learned.fixture-receipt-procedure"
                    ),
                    None,
                )
            )
            if (
                learned is None
                or learned.source_refs != (_learning_source_ref(installed),)
                or not learned.source_revisions
            ):
                raise RuntimeError("installed_learning_not_selected_for_new_thread")
            return {
                "mode": mode,
                "installed_package": str(Path(sys.modules["ads_booster"].__file__ or "").resolve()),
                "knowledge_roots": [
                    str(settings.root),
                    str(settings.control_root),
                    str(settings.policy_path),
                ],
                "signed_ingress": "acknowledged",
                "cli_doctor": "executed" if doctor else "empty_output",
                "cli_service_run_help": "executed" if service_help else "empty_output",
                "u2_new_thread": "prepared_context_selected",
                "normal_learning_notifications": 0,
                "ordinary_slack_reply_intents": len(sender.messages),
                "fixture_provider": mode == "fixture",
                "provider_model": model_id,
                "provider_call_count": provider_calls,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "learning_source_digest": learning_source_digest,
                "synthetic_cases": [
                    {"case_id": item.case_id, "expected": item.expected} for item in _CASES
                ],
                "case_results": {item.case_id: "passed" for item in _CASES},
                "limitations": [
                    "CLI subprocess smoke is separate from this same-process fixture wiring.",
                    "No Slack network delivery is attempted.",
                    "Fixture provider output is fixed; only the installed ToolHost validates and applies it.",
                ],
            }
        finally:
            installed.runtime.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("--mode", choices=("fixture", "real-model"), required=True)
    _ = parser.add_argument("--home", type=Path, required=True)
    _ = parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "real-model":
        parser.error(
            "real-model F4 moved to installed_learning_model_canary.py; this harness is fixture-only"
        )
    _assert_paths(args.home, args.output)
    result = _run(args.mode, args.home)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
