from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.errors import KnowledgePolicyError
from ads_booster.knowledge.governance_contracts import ConstraintBinding, TaskBinding
from ads_booster.knowledge.grant_policy import authorize_read
from ads_booster.knowledge.memory_contracts import MemoryEntry
from ads_booster.knowledge.scope_contracts import AccessScope

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.contracts.knowledge_selection import ContextReceipt, KnowledgeActionKind
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.scope_contracts import ActorContext

type ReceiptDependency = tuple[str, str, str, str | None]

_OPTIONAL_INTEGER_ROW: TypeAdapter[tuple[int] | None] = TypeAdapter(tuple[int] | None)
_OPTIONAL_STRING_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)
_STRING_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])
_STRING_TRIPLE_ROWS: TypeAdapter[list[tuple[str, str, str]]] = TypeAdapter(
    list[tuple[str, str, str]]
)


def current_policy_epoch(repository: SqliteKnowledgeRepository, workspace_id: str) -> int | None:
    with repository.connection() as connection:
        row = _OPTIONAL_INTEGER_ROW.validate_python(
            connection.execute(
                "SELECT policy_epoch FROM workspaces WHERE workspace_id=? AND state='active'",
                (workspace_id,),
            ).fetchone(),
        )
    return None if row is None else row[0]


def active_task_binding(
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    *,
    action_kind: KnowledgeActionKind | None = None,
    brand_id: str | None = None,
    require_brand_match: bool = False,
) -> TaskBinding | None:
    with repository.connection() as connection:
        rows = _STRING_ROWS.validate_python(
            connection.execute(
                """SELECT binding_json FROM task_bindings
                WHERE workspace_id=? AND actor_ref=? AND capability_epoch=? AND state='active'
                ORDER BY opened_at DESC""",
                (actor.workspace_id, actor.actor_id, actor.policy_epoch),
            ).fetchall(),
        )
    bindings = tuple(TaskBinding.model_validate_json(row[0]) for row in rows)
    matched = tuple(
        binding
        for binding in bindings
        if (action_kind is None or binding.action_kind is action_kind)
        and (not require_brand_match or binding.brand_id == brand_id)
    )
    return matched[0] if len(matched) == 1 else None


def memory_document_ids(
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
) -> tuple[str, ...]:
    with repository.connection() as connection:
        rows = _STRING_ROWS.validate_python(
            connection.execute(
                """SELECT document_id FROM memory_documents
                WHERE workspace_id=? ORDER BY kind,document_id""",
                (actor.workspace_id,),
            ).fetchall(),
        )
    return tuple(row[0] for row in rows)


def applicable_constraints(
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    *,
    action_kind: KnowledgeActionKind,
    task_id: str,
    now: datetime,
) -> tuple[tuple[ConstraintBinding, MemoryEntry], ...]:
    with repository.connection() as connection:
        rows = _STRING_TRIPLE_ROWS.validate_python(
            connection.execute(
                """SELECT binding.binding_json,entry.entry_json,scope.scope_json
                FROM constraint_bindings AS binding
                JOIN memory_heads AS head ON head.workspace_id=binding.workspace_id
                    AND head.document_id=binding.document_id
                    AND head.revision_id=binding.memory_revision_id
                JOIN memory_entries AS entry ON entry.workspace_id=binding.workspace_id
                    AND entry.document_id=binding.document_id
                    AND entry.memory_revision_id=binding.memory_revision_id
                    AND entry.entry_id=binding.entry_id
                JOIN access_scopes AS scope ON scope.scope_key=entry.scope_key
                WHERE binding.workspace_id=? AND entry.status='active'
                    AND entry.dependency_state='current'
                ORDER BY binding.constraint_id""",
                (actor.workspace_id,),
            ).fetchall(),
        )
    values: list[tuple[ConstraintBinding, MemoryEntry]] = []
    for row in rows:
        scope = AccessScope.model_validate_json(row[2])
        try:
            _ = authorize_read(actor=actor, target_scope=scope, at=now)
        except KnowledgePolicyError:
            continue
        values.append(
            (
                ConstraintBinding.model_validate_json(row[0]),
                MemoryEntry.model_validate_json(row[1]),
            )
        )
    return tuple(
        (binding, entry)
        for binding, entry in values
        if (not binding.applies_to.action_kinds or action_kind in binding.applies_to.action_kinds)
        and (binding.applies_to.task_ref is None or binding.applies_to.task_ref == task_id)
    )


def persist_context_receipt(
    repository: SqliteKnowledgeRepository,
    receipt: ContextReceipt,
) -> None:
    digest = contract_sha256(receipt)
    dependencies = _receipt_dependencies(receipt)
    with repository.connection() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        row = _OPTIONAL_STRING_ROW.validate_python(
            connection.execute(
                "SELECT receipt_sha256 FROM context_receipts WHERE receipt_id=?",
                (receipt.receipt_id,),
            ).fetchone(),
        )
        if row is not None:
            if row[0] != digest:
                message = "context_receipt_idempotency_conflict"
                raise ValueError(message)
            return
        _ = connection.execute(
            """INSERT INTO context_receipts(
                receipt_id,workspace_id,actor_ref,task_id,policy_version,action_kind,
                brand_id,receipt_sha256,receipt_json,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                receipt.receipt_id,
                receipt.team_id,
                receipt.scoped_actor_ref,
                receipt.task_ref,
                receipt.policy_version,
                receipt.action_kind.value,
                receipt.resolved_brand_ref,
                digest,
                receipt.model_dump_json(),
                receipt.created_at.isoformat(),
            ),
        )
        for ordinal, (kind, entity_id, revision_id, content_digest) in enumerate(dependencies):
            _ = connection.execute(
                """INSERT INTO context_dependencies(
                    receipt_id,ordinal,dependency_kind,entity_id,revision_id,content_sha256
                ) VALUES (?,?,?,?,?,?)""",
                (receipt.receipt_id, ordinal, kind, entity_id, revision_id, content_digest),
            )


def context_receipt_is_current(
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    receipt: ContextReceipt,
) -> bool:
    if current_policy_epoch(repository, actor.workspace_id) != actor.policy_epoch:
        return False
    task = active_task_binding(
        repository,
        actor,
        action_kind=receipt.action_kind,
        brand_id=receipt.resolved_brand_ref,
        require_brand_match=True,
    )
    if task is None or task.task_id != receipt.task_ref:
        return False
    with repository.connection() as connection:
        for selected in receipt.selected_memory_revisions:
            row = _OPTIONAL_INTEGER_ROW.validate_python(
                connection.execute(
                    """SELECT 1 FROM memory_heads WHERE workspace_id=? AND document_id=?
                    AND revision_id=?""",
                    (actor.workspace_id, selected.document_id, selected.revision_id),
                ).fetchone()
            )
            if row is None:
                return False
        for selected in receipt.selected_wiki_claims:
            row = _OPTIONAL_INTEGER_ROW.validate_python(
                connection.execute(
                    """SELECT 1 FROM knowledge_heads WHERE workspace_id=? AND page_id=?
                    AND revision_id=?""",
                    (actor.workspace_id, selected.page_id, selected.revision_id),
                ).fetchone()
            )
            if row is None:
                return False
            if any(
                repository.claim_dependency_state(actor, claim_id, selected.revision_id) is not None
                for claim_id in selected.claim_ids
            ):
                return False
        for selected in receipt.selected_source_revisions:
            row = _OPTIONAL_INTEGER_ROW.validate_python(
                connection.execute(
                    """SELECT 1 FROM source_heads AS head
                    JOIN sources AS source USING(workspace_id,source_id)
                    WHERE head.workspace_id=? AND head.source_id=? AND head.revision_id=?
                    AND source.visibility='searchable'
                    AND NOT EXISTS (
                        SELECT 1 FROM tombstones AS tomb
                        WHERE tomb.workspace_id=head.workspace_id AND tomb.target_id=head.source_id
                        AND tomb.state IN ('blocked','purge_pending','purged')
                    )""",
                    (actor.workspace_id, selected.source_id, selected.revision_id),
                ).fetchone()
            )
            if row is None:
                return False
        for selected in receipt.required_constraints:
            row = _OPTIONAL_INTEGER_ROW.validate_python(
                connection.execute(
                    """SELECT 1 FROM constraint_bindings AS binding
                    JOIN memory_heads AS head ON head.workspace_id=binding.workspace_id
                        AND head.document_id=binding.document_id
                        AND head.revision_id=binding.memory_revision_id
                    WHERE binding.workspace_id=? AND binding.constraint_id=?
                    AND binding.memory_revision_id=?""",
                    (actor.workspace_id, selected.constraint_id, selected.revision_id),
                ).fetchone()
            )
            if row is None:
                return False
        if receipt.soul_revision_id is not None:
            row = _OPTIONAL_INTEGER_ROW.validate_python(
                connection.execute(
                    """SELECT 1 FROM memory_heads AS head
                    JOIN memory_documents AS document USING(workspace_id,document_id)
                    WHERE head.workspace_id=? AND document.kind='soul'
                    AND document.brand_id IS ? AND head.revision_id=?""",
                    (actor.workspace_id, receipt.resolved_brand_ref, receipt.soul_revision_id),
                ).fetchone()
            )
            if row is None:
                return False
    return True


def _receipt_dependencies(receipt: ContextReceipt) -> tuple[ReceiptDependency, ...]:
    values: list[ReceiptDependency] = [
        ("memory", item.document_id, item.revision_id, item.content_sha256)
        for item in receipt.selected_memory_revisions
    ]
    values.extend(
        ("wiki_claim", item.page_id, item.revision_id, item.content_sha256)
        for item in receipt.selected_wiki_claims
    )
    values.extend(
        ("source", item.source_id, item.revision_id, item.content_sha256)
        for item in receipt.selected_source_revisions
    )
    values.extend(
        ("constraint", item.constraint_id, item.revision_id, None)
        for item in receipt.required_constraints
    )
    if receipt.soul_revision_id is not None:
        values.append(
            ("soul", receipt.resolved_brand_ref or "unconfigured", receipt.soul_revision_id, None)
        )
    return tuple(values)


__all__ = [
    "active_task_binding",
    "applicable_constraints",
    "context_receipt_is_current",
    "current_policy_epoch",
    "memory_document_ids",
    "persist_context_receipt",
]
