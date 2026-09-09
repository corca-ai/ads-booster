from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.knowledge_context import (
    ContextTransferValidationAccepted,
    ContextTransferValidationRejected,
    ContextTransferValidationRequest,
    KnowledgeContextTransfer,
    knowledge_context_sha256,
)
from ads_booster.knowledge.repository_types import conflict

if TYPE_CHECKING:
    from ads_booster.knowledge.repository_protocol import KnowledgeRepository

_STRING = TypeAdapter(str)
type ValidationResult = ContextTransferValidationAccepted | ContextTransferValidationRejected
type Dependency = tuple[str, str, str, str | None]
_VALIDATION_RESULT: TypeAdapter[ValidationResult] = TypeAdapter(ValidationResult)


def record_context_transfer(
    repository: KnowledgeRepository,
    transfer: KnowledgeContextTransfer,
) -> bool:
    context_digest = knowledge_context_sha256(transfer)
    with repository.connection() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        row = cast(
            "tuple[object, ...] | None",
            connection.execute(
                "SELECT context_sha256 FROM context_transfers WHERE transfer_id=?",
                (transfer.transfer_id,),
            ).fetchone(),
        )
        if row is not None:
            if _STRING.validate_python(row[0]) == context_digest:
                return False
            conflict("transfer_idempotency_conflict", transfer.transfer_id)
        receipt = transfer.receipt
        receipt_digest = contract_sha256(receipt)
        _ = connection.execute(
            """
            INSERT OR IGNORE INTO context_receipts(
                receipt_id,workspace_id,actor_ref,task_id,policy_version,action_kind,
                brand_id,receipt_sha256,receipt_json,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                receipt.receipt_id,
                transfer.workspace_id,
                receipt.scoped_actor_ref,
                receipt.task_ref,
                receipt.policy_version,
                receipt.action_kind.value,
                receipt.resolved_brand_ref,
                receipt_digest,
                receipt.model_dump_json(),
                receipt.created_at.isoformat(),
            ),
        )
        dependencies = _dependencies(transfer)
        for ordinal, dependency in enumerate(dependencies):
            kind, entity_id, revision_id, content_digest = dependency
            _ = connection.execute(
                """
                INSERT OR IGNORE INTO context_dependencies(
                    receipt_id,ordinal,dependency_kind,entity_id,revision_id,content_sha256
                ) VALUES (?,?,?,?,?,?)
                """,
                (receipt.receipt_id, ordinal, kind, entity_id, revision_id, content_digest),
            )
        _ = connection.execute(
            """
            INSERT INTO context_transfers(
                transfer_id,workspace_id,account_id,actor_ref,brand_id,action_kind,
                run_ref,task_ref,invocation_ref,context_sha256,receipt_id,policy_revision,
                transfer_json,created_at,expires_at,state
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active')
            """,
            (
                transfer.transfer_id,
                transfer.workspace_id,
                transfer.account_id,
                transfer.scoped_actor_ref,
                transfer.brand_ref,
                transfer.action_kind.value,
                transfer.run_ref,
                transfer.task_ref,
                transfer.invocation_ref,
                context_digest,
                receipt.receipt_id,
                transfer.policy_revision,
                transfer.model_dump_json(),
                transfer.created_at.isoformat(),
                transfer.expires_at.isoformat(),
            ),
        )
        for ordinal, dependency in enumerate(dependencies):
            kind, entity_id, revision_id, content_digest = dependency
            _ = connection.execute(
                """
                INSERT INTO transfer_dependencies(
                    transfer_id,ordinal,dependency_kind,entity_id,revision_id,content_sha256
                ) VALUES (?,?,?,?,?,?)
                """,
                (transfer.transfer_id, ordinal, kind, entity_id, revision_id, content_digest),
            )
    return True


def _dependencies(transfer: KnowledgeContextTransfer) -> tuple[Dependency, ...]:
    receipt = transfer.receipt
    values: list[Dependency] = []
    if receipt.soul_revision_id is not None:
        values.append(
            ("soul", receipt.resolved_brand_ref or "unconfigured", receipt.soul_revision_id, None)
        )
    values.extend(
        ("memory", item.document_id, item.revision_id, item.content_sha256)
        for item in receipt.selected_memory_revisions
    )
    values.extend(
        ("wiki_claim", item.page_id, item.revision_id, item.content_sha256)
        for item in receipt.selected_wiki_claims
    )
    values.extend(
        ("source", item.source_id, item.revision_id, item.content_sha256)
        for item in receipt.selected_source_revisions
    )
    values.extend(
        ("source", item.source_id, item.revision_id, item.content_sha256)
        for skill in receipt.selected_skill_revisions
        for item in skill.source_revisions
    )
    values.extend(
        ("constraint", item.constraint_id, item.revision_id, None)
        for item in receipt.required_constraints
    )
    values.extend(
        ("soul_example", item.page_id, item.revision_id, None) for item in receipt.soul_example_refs
    )
    return tuple(values)


def context_transfer(
    repository: KnowledgeRepository,
    transfer_id: str,
) -> KnowledgeContextTransfer | None:
    with repository.connection() as connection:
        row = cast(
            "tuple[object, ...] | None",
            connection.execute(
                """SELECT transfer_json FROM context_transfers
                WHERE transfer_id=? AND state='active'""",
                (transfer_id,),
            ).fetchone(),
        )
    return (
        None
        if row is None
        else KnowledgeContextTransfer.model_validate_json(_STRING.validate_python(row[0]))
    )


def transfer_dependencies(
    repository: KnowledgeRepository,
    transfer_id: str,
) -> tuple[tuple[str, str, str], ...]:
    with repository.connection() as connection:
        rows = cast(
            "list[tuple[object, ...]]",
            connection.execute(
                """
                SELECT dependency_kind,entity_id,revision_id FROM transfer_dependencies
                WHERE transfer_id=? ORDER BY ordinal
                """,
                (transfer_id,),
            ).fetchall(),
        )
    return tuple(
        (
            _STRING.validate_python(row[0]),
            _STRING.validate_python(row[1]),
            _STRING.validate_python(row[2]),
        )
        for row in rows
    )


def transfer_validation(
    repository: KnowledgeRepository,
    transfer_id: str,
    stage: str,
    request_id: str,
) -> ValidationResult | None:
    with repository.connection() as connection:
        row = cast(
            "tuple[object, ...] | None",
            connection.execute(
                """
                SELECT validation_json FROM transfer_validations
                WHERE transfer_id=? AND stage=? AND request_id=?
                """,
                (transfer_id, stage, request_id),
            ).fetchone(),
        )
    return (
        None if row is None else _VALIDATION_RESULT.validate_json(_STRING.validate_python(row[0]))
    )


def record_transfer_validation(
    repository: KnowledgeRepository,
    request: ContextTransferValidationRequest,
    result: ValidationResult,
) -> None:
    if (
        request.request_id != result.request_id
        or request.principal_id != result.principal_id
        or request.stage is not result.stage
        or request.transfer_id != result.transfer_id
        or request.workspace_id != result.workspace_id
        or request.account_id != result.account_id
        or request.knowledge_context_sha256 != result.knowledge_context_sha256
    ):
        conflict("transfer_validation_binding_conflict", request.transfer_id)
    match result:
        case ContextTransferValidationAccepted(
            dependency_set_sha256=dependency_digest,
            valid_until=valid_until,
        ):
            status = "accepted"
            rejection_code = None
            validity = valid_until.isoformat()
        case ContextTransferValidationRejected(rejection_code=rejection):
            status = "rejected"
            dependency_digest = None
            rejection_code = rejection.value
            validity = None
    with repository.connection() as connection:
        transfer = cast(
            "tuple[object, ...] | None",
            connection.execute(
                """
                SELECT workspace_id,account_id,context_sha256 FROM context_transfers
                WHERE transfer_id=? AND state='active'
                """,
                (request.transfer_id,),
            ).fetchone(),
        )
        if transfer is None or (
            _STRING.validate_python(transfer[0]) != request.workspace_id
            or _STRING.validate_python(transfer[1]) != request.account_id
            or _STRING.validate_python(transfer[2]) != request.knowledge_context_sha256
        ):
            conflict("transfer_validation_stale", request.transfer_id)
        try:
            _ = connection.execute(
                """
                INSERT INTO transfer_validations(
                    transfer_id,stage,request_id,principal_id,context_sha256,status,
                    dependency_set_sha256,rejection_code,checked_at,valid_until,validation_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    request.transfer_id,
                    request.stage.value,
                    request.request_id,
                    request.principal_id,
                    request.knowledge_context_sha256,
                    status,
                    dependency_digest,
                    rejection_code,
                    result.checked_at.isoformat(),
                    validity,
                    result.model_dump_json(),
                ),
            )
        except sqlite3.IntegrityError as error:
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    """
                    SELECT validation_json FROM transfer_validations
                    WHERE transfer_id=? AND stage=? AND request_id=?
                    """,
                    (request.transfer_id, request.stage.value, request.request_id),
                ).fetchone(),
            )
            if row is not None and _STRING.validate_python(row[0]) == result.model_dump_json():
                return
            conflict(
                "transfer_validation_idempotency_conflict",
                request.request_id,
            ).with_traceback(error.__traceback__)


def record_transfer_replica(
    repository: KnowledgeRepository,
    transfer_id: str,
    system_id: str,
    replica_id: str,
) -> None:
    with repository.connection() as connection:
        try:
            _ = connection.execute(
                """
                INSERT INTO transfer_replicas(
                    transfer_id,system_id,replica_id,deletion_state,receipt_sha256
                ) VALUES (?,?,?,'retained',NULL)
                """,
                (transfer_id, system_id, replica_id),
            )
        except sqlite3.IntegrityError as error:
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    """
                    SELECT deletion_state FROM transfer_replicas
                    WHERE transfer_id=? AND system_id=? AND replica_id=?
                    """,
                    (transfer_id, system_id, replica_id),
                ).fetchone(),
            )
            if row is not None and _STRING.validate_python(row[0]) == "retained":
                return
            conflict("transfer_replica_conflict", transfer_id).with_traceback(error.__traceback__)


__all__ = [
    "ValidationResult",
    "context_transfer",
    "record_context_transfer",
    "record_transfer_replica",
    "record_transfer_validation",
    "transfer_dependencies",
    "transfer_validation",
]
