from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from ads_booster.cli.knowledge_admin import list_brands, open_task, register_brand
from ads_booster.cli.knowledge_runtime import CliKnowledgeSession, read_json, settings_from_options
from ads_booster.cli.knowledge_operations import register_operations
from ads_booster.knowledge.configuration import (
    initialize_knowledge_store,
    initialize_local_configuration,
    load_local_actor,
)
from ads_booster.knowledge.contract_types import ConversationEventKind, ConversationRole
from ads_booster.knowledge.maintenance import inspect_owner
from ads_booster.knowledge.erase_ledger import EraseLedger
from ads_booster.knowledge.repository import SqliteKnowledgeRepository
from ads_booster.knowledge.source_contracts import ConversationEvent, IngestEnvelope

app = typer.Typer(no_args_is_help=True, help="Operate one private knowledge store.")
memory_app = typer.Typer(no_args_is_help=True)
brand_app = typer.Typer(no_args_is_help=True)
task_app = typer.Typer(no_args_is_help=True)
app.add_typer(memory_app, name="memory")
app.add_typer(brand_app, name="brand")
app.add_typer(task_app, name="task")
register_operations(app, memory_app)

Root = Annotated[Path, typer.Option("--root")]
ControlRoot = Annotated[Path, typer.Option("--control-root")]
Policy = Annotated[Path, typer.Option("--policy")]


@app.command("init")
def init(
    root: Root,
    control_root: ControlRoot,
    policy: Policy,
    workspace: Annotated[str, typer.Option("--workspace")],
) -> None:
    _ = initialize_local_configuration(root, control_root, policy, workspace_id=workspace)
    _ = EraseLedger(control_root).initialize()
    actor = initialize_knowledge_store(settings_from_options(root, control_root, policy))
    typer.echo(json.dumps({"state": "initialized", "workspace_id": actor.workspace_id}))


@app.command("doctor")
def doctor(root: Root, control_root: ControlRoot, policy: Policy) -> None:
    settings = settings_from_options(root, control_root, policy)
    actor = load_local_actor(settings)
    repository = SqliteKnowledgeRepository(root)
    with repository.connection() as connection:
        counts = {
            state: int(
                connection.execute("SELECT count(*) FROM jobs WHERE state=?", (state,)).fetchone()[0]
            )
            for state in ("queued", "running", "waiting_dependency", "awaiting_answer")
        }
        ingress = _count(connection, "knowledge_ingress_outbox", "pending")
        index = _count(connection, "index_outbox", "pending")
    owner = inspect_owner(root)
    typer.echo(
        json.dumps(
            {
                "schema": "trace.knowledge-doctor.v1",
                "state": "configured",
                "workspace_id": actor.workspace_id,
                "owner": owner.state,
                "owner_id": owner.owner_id,
                "jobs": counts,
                "ingress_pending": ingress,
                "index_pending": index,
            },
            ensure_ascii=False,
        )
    )


@app.command("ingest")
def ingest(
    root: Root,
    control_root: ControlRoot,
    policy: Policy,
    envelope: Annotated[Path, typer.Option("--envelope")],
    attachment: Annotated[list[str] | None, typer.Option("--attachment")] = None,
) -> None:
    paths = _attachment_paths(attachment or [])
    settings = settings_from_options(root, control_root, policy)
    with CliKnowledgeSession(settings, attachment_paths=paths) as session:
        request = IngestEnvelope.model_validate(read_json(envelope))
        reference = request.message_event or request.conversation
        if reference is None:
            raise typer.BadParameter("knowledge_ingest_conversation_reference_required")
        event = ConversationEvent(
            conversation_id=reference.conversation_ref,
            message_id=reference.message_ref,
            revision=reference.revision,
            sequence=reference.revision,
            role=ConversationRole.USER,
            speaker_ref=session.actor.actor_id,
            created_at=request.timestamp,
            text=request.request_text,
            event_kind=ConversationEventKind(request.event_kind.value),
            scope=session.actor.conversation_scope,
        )
        receipt = session.ingestion.ingest(session.actor, event, request)
        typer.echo(receipt.model_dump_json(indent=2))


@app.command("search")
def search(
    root: Root,
    control_root: ControlRoot,
    policy: Policy,
    query: Annotated[str, typer.Option("--query")],
    limit: Annotated[int, typer.Option("--limit", min=1, max=20)] = 8,
) -> None:
    _execute(root, control_root, policy, "knowledge_search", {"schema": "knowledge.tool.search.v1", "query": query, "limit": limit})


@app.command("get")
def get(
    root: Root,
    control_root: ControlRoot,
    policy: Policy,
    item_id: Annotated[str, typer.Option("--id")],
    revision: Annotated[str | None, typer.Option("--revision")] = None,
) -> None:
    payload = {"schema": "knowledge.tool.get.v1", "page_id": item_id}
    if revision not in (None, "current"):
        payload["revision_id"] = revision
    _execute(root, control_root, policy, "knowledge_get", payload)


@app.command("schedule")
def schedule(root: Root, control_root: ControlRoot, policy: Policy, request: Annotated[Path, typer.Option("--request")]) -> None:
    _execute(root, control_root, policy, "knowledge_schedule", read_json(request))


@memory_app.command("get")
def memory_get(
    root: Root,
    control_root: ControlRoot,
    policy: Policy,
    kind: Annotated[str, typer.Option("--kind")],
    date: Annotated[str | None, typer.Option("--date")] = None,
    brand: Annotated[str | None, typer.Option("--brand")] = None,
) -> None:
    payload = {"schema": "knowledge.tool.memory-get.v1", "kind": kind}
    if date is not None:
        payload["local_date"] = date
    if brand is not None:
        payload["brand_id"] = brand
    _execute(root, control_root, policy, "memory_get", payload, brand_id=brand)


@memory_app.command("explain")
def memory_explain(root: Root, control_root: ControlRoot, policy: Policy, entry: Annotated[str, typer.Option("--entry")]) -> None:
    _execute(root, control_root, policy, "memory_explain", {"schema": "knowledge.tool.memory-explain.v1", "target_id": entry})


@memory_app.command("correct")
def memory_correct(root: Root, control_root: ControlRoot, policy: Policy, request: Annotated[Path, typer.Option("--request")], task: Annotated[str | None, typer.Option("--task")] = None) -> None:
    _execute(root, control_root, policy, "memory_correct", read_json(request), task_id=task)


@brand_app.command("register")
def brand_register(root: Root, control_root: ControlRoot, policy: Policy, request: Annotated[Path, typer.Option("--request")]) -> None:
    with CliKnowledgeSession(settings_from_options(root, control_root, policy)) as session:
        typer.echo(register_brand(session, read_json(request)).model_dump_json(indent=2))


@brand_app.command("list")
def brand_list(root: Root, control_root: ControlRoot, policy: Policy) -> None:
    with CliKnowledgeSession(settings_from_options(root, control_root, policy)) as session:
        typer.echo(json.dumps([item.model_dump(mode="json") for item in list_brands(session)], ensure_ascii=False))


@task_app.command("open")
def task_open(root: Root, control_root: ControlRoot, policy: Policy, request: Annotated[Path, typer.Option("--request")]) -> None:
    with CliKnowledgeSession(settings_from_options(root, control_root, policy)) as session:
        typer.echo(open_task(session, read_json(request)).model_dump_json(indent=2))


@task_app.command("close")
def task_close(root: Root, control_root: ControlRoot, policy: Policy, task_id: Annotated[str, typer.Option("--id")]) -> None:
    with CliKnowledgeSession(settings_from_options(root, control_root, policy)) as session:
        closed = session.host.close_task(session.actor, task_id, session.actor.policy_epoch, datetime.now(UTC))
        typer.echo(closed.model_dump_json(indent=2))


def _execute(root: Path, control_root: Path, policy: Path, name: str, payload: dict[str, object], *, task_id: str | None = None, brand_id: str | None = None) -> None:
    with CliKnowledgeSession(settings_from_options(root, control_root, policy)) as session:
        typer.echo(session.execute(name, payload, task_id=task_id, brand_id=brand_id))


def _attachment_paths(values: list[str]) -> dict[int, Path]:
    result: dict[int, Path] = {}
    for value in values:
        ordinal, separator, raw = value.partition("=")
        if not separator or not raw:
            raise typer.BadParameter("attachments use ORDINAL=/absolute/path")
        path = Path(raw)
        if not path.is_absolute():
            raise typer.BadParameter("attachment paths must be absolute")
        result[int(ordinal)] = path
    return result


def _count(connection: sqlite3.Connection, table: str, state: str) -> int | None:
    try:
        cursor = connection.execute(f"SELECT count(*) FROM {table} WHERE state=?", (state,))
    except sqlite3.OperationalError:
        return None
    return int(cursor.fetchone()[0])


__all__ = ["app"]
