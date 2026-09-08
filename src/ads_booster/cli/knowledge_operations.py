from __future__ import annotations

import json
import os
import signal
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import uuid4

import typer
from pydantic import TypeAdapter

from ads_booster.cli.knowledge_runtime import CliKnowledgeSession, read_json, settings_from_options
from ads_booster.contracts.agent_run import CapabilitySnapshot
from ads_booster.contracts.knowledge_selection import ContextRequest
from ads_booster.knowledge.backup import create_backup
from ads_booster.knowledge.change_publication import ChangePublisher
from ads_booster.knowledge.configuration import load_local_actor
from ads_booster.knowledge.context_selection import KnowledgeContextAssembler
from ads_booster.knowledge.contract_types import (
    AuthorityClass,
    ConversationEventKind,
    ConversationRole,
    IngestEventKind,
    InstructionAuthority,
    Provenance,
)
from ads_booster.knowledge.deletion import (
    DeletionService,
    PurgeRequest,
    ReplicaDeletionState,
    ReplicaPurgeReceipt,
    ReplicaPurgeRequest,
    RetractionRequest,
)
from ads_booster.knowledge.erase_ledger import EraseLedger, EraseTarget
from ads_booster.knowledge.evidence_contracts import AuthenticatedEvent
from ads_booster.knowledge.memory_consolidation import MemoryConsolidationProcessor
from ads_booster.knowledge.repository_context import active_task_binding
from ads_booster.knowledge.restore import restore_backup
from ads_booster.knowledge.retrieval import KnowledgeRetriever
from ads_booster.knowledge.source_contracts import (
    ConversationEvent,
    IngestEnvelope,
    MessageEventRef,
)
from ads_booster.knowledge.tool_contracts import (
    ProposalTargetKind,
    QuestionRecord,
    QuestionStatus,
    TrustedQuestionAnswer,
)
from ads_booster.marketing.agent_service.knowledge import knowledge_descriptors
from ads_booster.marketing.agent_service.lifecycle import (
    InstalledKnowledgeRuntime,
    build_installed_knowledge_runtime,
)
from ads_booster.providers.codex_cli import CodexCli, resolve_codex_executable

if TYPE_CHECKING:
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.scope_contracts import ActorContext

Root = Annotated[Path, typer.Option("--root")]
ControlRoot = Annotated[Path, typer.Option("--control-root")]
Policy = Annotated[Path, typer.Option("--policy")]
ServiceDatabase = Annotated[Path | None, typer.Option("--service-database")]
Model = Annotated[str | None, typer.Option("--model")]

_QUESTION_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])
type _EraseTargetKind = Literal[
    "source", "page", "claim", "memory_document", "memory_entry", "transfer"
]
_TARGET_ROW: TypeAdapter[tuple[_EraseTargetKind, str, str] | None] = TypeAdapter(
    tuple[_EraseTargetKind, str, str] | None
)
_EXISTS_ROW: TypeAdapter[tuple[int] | None] = TypeAdapter(tuple[int] | None)


@dataclass(frozen=True, slots=True)
class _PendingReplicaPurge:
    def purge(self, request: ReplicaPurgeRequest) -> ReplicaPurgeReceipt:
        checked_at = datetime.now(UTC)
        receipt_id = "replica-purge." + sha256(request.model_dump_json().encode()).hexdigest()[:32]
        return ReplicaPurgeReceipt(
            receipt_id=receipt_id,
            request_id=request.request_id,
            transfer_id=request.transfer_id,
            system_id=request.system_id,
            replica_id=request.replica_id,
            state=ReplicaDeletionState.PURGE_PENDING,
            checked_at=checked_at,
        )


def register_operations(  # noqa: C901, PLR0915
    app: typer.Typer,
    memory_app: typer.Typer,
) -> None:
    @app.command("run")
    def _run(  # noqa: PLR0913, PLR0917
        root: Root,
        control_root: ControlRoot,
        policy: Policy,
        model: Model = None,
        service_database: ServiceDatabase = None,
        once: Annotated[bool, typer.Option("--once")] = False,
        until_idle: Annotated[bool, typer.Option("--until-idle")] = False,
        flush_batches: Annotated[bool, typer.Option("--flush-batches")] = False,
    ) -> None:
        if once and until_idle:
            message = "choose either --once or --until-idle"
            raise typer.BadParameter(message)
        if flush_batches and not until_idle:
            message = "--flush-batches requires --until-idle"
            raise typer.BadParameter(message)
        installed = _runtime(root, control_root, policy, service_database, model)
        previous_sigterm = signal.getsignal(signal.SIGTERM)

        def stop_runtime(_signum: int, _frame: object) -> None:
            installed.runtime.request_stop()

        _ = signal.signal(signal.SIGTERM, stop_runtime)
        try:
            if once:
                worked = installed.runtime.run_once()
                while installed.runtime.active:
                    _ = installed.runtime.stop.wait(0.01)
                    _ = installed.runtime.run_once()
                typer.echo(json.dumps({"state": "worked" if worked else "idle"}))
            elif until_idle:
                installed.runtime.run_until_idle(flush_batches=flush_batches)
                typer.echo(json.dumps({"state": "idle"}))
            else:
                installed.runtime.run_continuous()
        finally:
            _ = signal.signal(signal.SIGTERM, previous_sigterm)
            installed.runtime.close()

    @app.command("context")
    def _context(
        root: Root,
        control_root: ControlRoot,
        policy: Policy,
        request: Annotated[Path, typer.Option("--request")],
        brand: Annotated[str | None, typer.Option("--brand")] = None,
    ) -> None:
        payload = read_json(request)
        _ = payload.pop("trusted_binding_refs", None)
        requested = ContextRequest.model_validate(payload)
        with CliKnowledgeSession(settings_from_options(root, control_root, policy)) as session:
            selected_brand = brand if brand is not None else requested.brand_ref
            task = active_task_binding(
                session.repository,
                session.actor,
                action_kind=requested.action_kind,
                brand_id=selected_brand,
                require_brand_match=True,
            )
            if task is None:
                message = "knowledge_current_task_binding_required"
                raise typer.BadParameter(message)
            now = datetime.now(UTC)
            descriptors = knowledge_descriptors(
                session.host.catalog(), session.host.schemas(), now=now
            )
            snapshot = CapabilitySnapshot(
                schema_version="trace.capability-snapshot.v1",
                snapshot_id=f"cli.snapshot.{uuid4().hex}",
                run_id=f"cli.run.{uuid4().hex}",
                descriptors=descriptors,
                created_at=now,
            )
            result = KnowledgeContextAssembler(
                session.repository,
                KnowledgeRetriever(session.repository),
            ).prepare(
                session.actor,
                task,
                query=requested.query,
                tool_catalog=session.host.catalog(),
                capability_snapshot=snapshot,
                now=now,
                budget=requested.budget,
            )
            typer.echo(result.model_dump_json(indent=2))

    @app.command("backup")
    def _backup(
        root: Root,
        control_root: ControlRoot,
        policy: Policy,
        destination: Annotated[Path, typer.Option("--destination")],
    ) -> None:
        with CliKnowledgeSession(settings_from_options(root, control_root, policy)) as session:
            exported = EraseLedger(control_root).export(session.actor.workspace_id)
            result = create_backup(
                session.repository,
                destination,
                workspace_id=session.actor.workspace_id,
                erase_sequence=exported.sequence,
                erase_head_sha256=exported.head_sha256,
            )
            typer.echo(
                json.dumps(
                    {
                        "path": str(result.path),
                        "manifest": result.manifest.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                )
            )

    @app.command("restore")
    def _restore(
        root: Root,
        control_root: ControlRoot,
        policy: Policy,
        backup: Annotated[Path, typer.Option("--backup")],
    ) -> None:
        result = restore_backup(
            backup,
            root,
            erase_authority=EraseLedger(control_root),
        )
        _ = load_local_actor(settings_from_options(root, control_root, policy))
        typer.echo(
            json.dumps(
                {
                    "target": str(result.target),
                    "backup_id": result.backup_id,
                    "workspace_id": result.workspace_id,
                    "erase_sequence": result.erase_sequence,
                },
                ensure_ascii=False,
            )
        )

    @app.command("retract")
    def _retract(
        root: Root,
        control_root: ControlRoot,
        policy: Policy,
        source: Annotated[str, typer.Option("--source")],
    ) -> None:
        with CliKnowledgeSession(settings_from_options(root, control_root, policy)) as session:
            service = _deletion_service(session.repository, control_root)
            operation_id = (
                "retract."
                + sha256(f"{session.actor.workspace_id}\x1f{source}".encode()).hexdigest()[:32]
            )
            receipt = service.retract(
                RetractionRequest(
                    operation_id=operation_id,
                    actor=session.actor,
                    target=EraseTarget(kind="source", entity_id=source),
                    reason_code="explicit_local_admin_retraction",
                    occurred_at=datetime.now(UTC),
                )
            )
            typer.echo(receipt.model_dump_json(indent=2))

    @app.command("purge")
    def _purge(
        root: Root,
        control_root: ControlRoot,
        policy: Policy,
        request: Annotated[str, typer.Option("--request")],
    ) -> None:
        with CliKnowledgeSession(settings_from_options(root, control_root, policy)) as session:
            service = _deletion_service(session.repository, control_root)
            target = _retraction_target(session.repository, session.actor, request)
            purge_id = "purge." + sha256(request.encode()).hexdigest()[:32]
            receipt = service.request_purge(
                PurgeRequest(
                    request_id=purge_id,
                    actor=session.actor,
                    target=target,
                    reason_code="explicit_local_admin_purge",
                    explicit_admin_request=True,
                    occurred_at=datetime.now(UTC),
                )
            )
            typer.echo(receipt.model_dump_json(indent=2))

    @app.command("questions")
    def _questions(  # noqa: PLR0913, PLR0917
        root: Root,
        control_root: ControlRoot,
        policy: Policy,
        pending: Annotated[bool, typer.Option("--pending")] = False,
        answer: Annotated[str | None, typer.Option("--answer")] = None,
        text: Annotated[str | None, typer.Option("--text")] = None,
    ) -> None:
        if pending == (answer is not None):
            message = "choose --pending or --answer"
            raise typer.BadParameter(message)
        if answer is not None and not text:
            message = "--answer requires --text"
            raise typer.BadParameter(message)
        with CliKnowledgeSession(settings_from_options(root, control_root, policy)) as session:
            if pending:
                typer.echo(_pending_questions(session))
                return
            _answer_question(session, answer or "", text or "")

    @memory_app.command("consolidate")
    def _consolidate(  # noqa: PLR0913, PLR0917
        root: Root,
        control_root: ControlRoot,
        policy: Policy,
        model: Model = None,
        service_database: ServiceDatabase = None,
        until_idle: Annotated[bool, typer.Option("--until-idle")] = False,
    ) -> None:
        if not until_idle:
            message = "memory consolidate requires --until-idle"
            raise typer.BadParameter(message)
        installed = _runtime(root, control_root, policy, service_database, model)
        runtime = installed.runtime
        try:
            actor = load_local_actor(settings_from_options(root, control_root, policy))
            memory = MemoryConsolidationProcessor(
                installed.adapter.repository,
                actor,
                ChangePublisher(installed.adapter.repository, installed.adapter.host.state),
            )
            root_job = memory.schedule(
                root_event_id=f"memory-consolidation.{uuid4().hex}",
                due_at=datetime.now(UTC),
            )
            if root_job is None:
                typer.echo(json.dumps({"state": "idle", "job_id": None}))
                return
            _require_only_root_subtree(
                installed.adapter.repository,
                actor,
                root_job.root_event_id,
            )
            runtime.ingress = None
            runtime.index = None
            runtime.memory_views = None
            runtime.run_until_idle()
            typer.echo(json.dumps({"state": "idle", "job_id": root_job.job_id}))
        finally:
            runtime.close()

    _ = (_run, _context, _backup, _restore, _retract, _purge, _questions, _consolidate)


def _runtime(
    root: Path,
    control_root: Path,
    policy: Path,
    service_database: Path | None,
    model: str | None,
) -> InstalledKnowledgeRuntime:
    executable = resolve_codex_executable()
    if executable is None:
        message = "codex is not installed on PATH; install Codex CLI and log in"
        raise typer.BadParameter(message)
    selected_model = model or os.environ.get("TRACE_MARKETING_MODEL")
    if selected_model is None:
        message = "--model or TRACE_MARKETING_MODEL is required"
        raise typer.BadParameter(message)
    return build_installed_knowledge_runtime(
        settings=settings_from_options(root, control_root, policy),
        service_database=service_database or root / "agent-service.sqlite3",
        codex=CodexCli(executable=executable, model=selected_model),
        model_id=selected_model,
    )


def _deletion_service(repository: SqliteKnowledgeRepository, control_root: Path) -> DeletionService:
    return DeletionService(repository, EraseLedger(control_root), _PendingReplicaPurge())


def _retraction_target(
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    operation_id: str,
) -> EraseTarget:
    with repository.connection() as connection:
        row = _TARGET_ROW.validate_python(
            connection.execute(
                """SELECT json_extract(receipt_json,'$.target_kind'),
                    json_extract(receipt_json,'$.target_id'),
                    coalesce(json_extract(receipt_json,'$.target_revision_id'),'')
                FROM operations WHERE workspace_id=? AND operation_id=?
                    AND operation_kind='retract' AND status='applied' """,
                (actor.workspace_id, operation_id),
            ).fetchone()
        )
    if row is None:
        message = "knowledge_retraction_not_found"
        raise typer.BadParameter(message)
    return EraseTarget(kind=row[0], entity_id=row[1], revision_id=row[2] or None)


def _pending_questions(session: CliKnowledgeSession) -> str:
    with session.repository.connection() as connection:
        rows = _QUESTION_ROWS.validate_python(
            connection.execute(
                """SELECT question_json FROM knowledge_questions
                WHERE workspace_id=? AND actor_ref=? AND status='pending'
                ORDER BY created_at,question_id""",
                (session.actor.workspace_id, session.actor.actor_id),
            ).fetchall()
        )
    questions = tuple(QuestionRecord.model_validate_json(row[0]) for row in rows)
    return json.dumps(
        [
            {
                "question_id": item.question_id,
                "problem": item.problem,
                "recommendation": item.recommendation,
            }
            for item in questions
        ],
        ensure_ascii=False,
    )


def _answer_question(session: CliKnowledgeSession, question_id: str, text: str) -> None:
    question = session.repository.question(session.actor, question_id)
    if question is None:
        message = "knowledge_question_not_found"
        raise typer.BadParameter(message)
    if question.status is QuestionStatus.ANSWERED:
        if question.answer != text:
            message = "knowledge_question_answer_conflict"
            raise typer.BadParameter(message)
        typer.echo(question.model_dump_json(indent=2))
        return
    answered_at = datetime.now(UTC)
    identity = sha256(f"{question_id}\x1f{text}".encode()).hexdigest()[:32]
    message_id = f"answer.{identity}"
    event = ConversationEvent(
        conversation_id=f"questions.{identity}",
        message_id=message_id,
        revision=1,
        sequence=1,
        role=ConversationRole.USER,
        speaker_ref=session.actor.actor_id,
        created_at=answered_at,
        text=text,
        event_kind=ConversationEventKind.MESSAGE_FINALIZED,
        scope=session.actor.conversation_scope,
    )
    envelope = IngestEnvelope(
        schema="knowledge.ingest-envelope.v1",
        delivery_id=f"delivery.{identity}",
        event_kind=IngestEventKind.MESSAGE_FINALIZED,
        request_text=text,
        message_event=MessageEventRef(
            conversation_ref=event.conversation_id,
            message_ref=event.message_id,
            revision=event.revision,
        ),
        timestamp=answered_at,
    )
    _ = session.ingestion.ingest(session.actor, event, envelope)
    actor = session.actor
    canonical = session.repository.canonical_event(actor, message_id)
    capabilities = tuple(dict.fromkeys(grant.capability for grant in actor.grants))
    result = session.host.answer_question(
        TrustedQuestionAnswer(
            question_id=question_id,
            authenticated_event=AuthenticatedEvent(
                event_id=canonical.message_id,
                actor_ref=actor.actor_id,
                workspace_id=actor.workspace_id,
                scope=canonical.scope,
                instruction_authority=InstructionAuthority.AUTHORIZED_USER,
                provenance=Provenance.HUMAN_DIRECT,
                authority_class=AuthorityClass.AUTHORIZED_TASK_INSTRUCTION,
                capabilities=capabilities,
                policy_epoch=actor.policy_epoch,
                occurred_at=canonical.created_at,
            ),
            answer=text,
            explicitly_adopts=(
                question.pending_proposal is not None
                and question.pending_proposal.target_kind is ProposalTargetKind.SOUL
            ),
            answered_at=answered_at,
        ),
        session.trusted_context(
            brand_id=(
                None if question.pending_proposal is None else question.pending_proposal.brand_id
            )
        ).model_copy(update={"actor": actor, "invoked_at": answered_at}),
    )
    typer.echo(result.question.model_dump_json(indent=2))


def _require_only_root_subtree(
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    root_event_id: str,
) -> None:
    with repository.connection() as connection:
        row = _EXISTS_ROW.validate_python(
            connection.execute(
                """SELECT 1 FROM jobs WHERE workspace_id=? AND state='queued'
                AND due_at<=? AND root_event_id!=? LIMIT 1""",
                (actor.workspace_id, datetime.now(UTC).isoformat(), root_event_id),
            ).fetchone()
        )
    if row is not None:
        message = "knowledge_memory_consolidation_runtime_not_idle"
        raise typer.BadParameter(message)


__all__ = ["register_operations"]
