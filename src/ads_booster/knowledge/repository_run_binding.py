from __future__ import annotations

import sqlite3
from hashlib import sha256
from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.knowledge_selection import KnowledgeActionKind
from ads_booster.knowledge.contracts import AccessScope
from ads_booster.knowledge.repository_identity import scope_key
from ads_booster.knowledge.repository_types import (
    RunBinding,
    RunBindingState,
    conflict,
    repository_error,
)

if TYPE_CHECKING:
    from ads_booster.knowledge.contracts import ActorContext
    from ads_booster.knowledge.repository_protocol import KnowledgeRepository


_RUN_OWNER: Final = "agent_service"
type RunBindingRow = tuple[
    str,
    str,
    int,
    str,
    str,
    str,
    str,
    str,
    int,
    str | None,
    str | None,
    str,
    str,
]
_RUN_BINDING_ROW: TypeAdapter[RunBindingRow | None] = TypeAdapter(RunBindingRow | None)


def put_run_binding(
    repository: KnowledgeRepository,
    actor: ActorContext,
    binding: RunBinding,
) -> RunBinding:
    _validate_actor_binding(actor, binding)
    try:
        with repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            existing = _select_binding(
                connection,
                binding.binding_id,
                binding.run_id,
                binding.binding_revision,
            )
            if existing is not None:
                if existing != binding:
                    conflict("run_binding_idempotency_conflict", binding.binding_id)
                return existing
            _ = connection.execute(
                """
                INSERT INTO run_bindings(
                    binding_id,run_owner,run_id,binding_revision,workspace_id,member_id,
                    session_id,scope_key,grant_set_sha256,policy_epoch,brand_id,
                    action_kind,state
                ) VALUES (?,'agent_service',?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    binding.binding_id,
                    binding.run_id,
                    binding.binding_revision,
                    binding.workspace_id,
                    binding.member_id,
                    binding.session_id,
                    scope_key(binding.scope),
                    binding.grant_set_sha256,
                    binding.policy_epoch,
                    binding.brand_id,
                    None if binding.action_kind is None else binding.action_kind.value,
                    binding.state.value,
                ),
            )
    except sqlite3.IntegrityError as error:
        error_code = "run_binding_integrity_conflict"
        raise repository_error(error_code, binding.binding_id) from error
    return binding


def run_binding(
    repository: KnowledgeRepository,
    actor: ActorContext,
    binding_id: str,
) -> RunBinding | None:
    with repository.connection() as connection:
        row = _select_binding(connection, binding_id)
    if row is None:
        return None
    if (
        row.workspace_id != actor.workspace_id
        or row.member_id != actor.member_id
        or row.session_id != actor.session_id
        or row.scope != actor.conversation_scope
        or row.policy_epoch != actor.policy_epoch
    ):
        return None
    return row


def _select_binding(
    connection: sqlite3.Connection,
    binding_id: str,
    run_id: str | None = None,
    binding_revision: int | None = None,
) -> RunBinding | None:
    row = _RUN_BINDING_ROW.validate_python(
        connection.execute(
            """
            SELECT binding.binding_id,binding.run_id,binding.binding_revision,
                binding.workspace_id,binding.member_id,binding.session_id,
                binding.grant_set_sha256,binding.scope_key,binding.policy_epoch,
                binding.brand_id,binding.action_kind,binding.state,scope.scope_json
            FROM run_bindings AS binding
            JOIN access_scopes AS scope USING(scope_key)
            WHERE binding.binding_id=? OR (
                ? IS NOT NULL AND binding.run_owner=? AND binding.run_id=?
                AND binding.binding_revision=?
            )
            """,
            (binding_id, run_id, _RUN_OWNER, run_id, binding_revision),
        ).fetchone()
    )
    if row is None:
        return None
    (
        stored_binding_id,
        stored_run_id,
        stored_revision,
        workspace_id,
        member_id,
        session_id,
        grant_set_sha256,
        _stored_scope_key,
        policy_epoch,
        brand_id,
        action_kind,
        state,
        scope_json,
    ) = row
    return RunBinding(
        binding_id=stored_binding_id,
        run_id=stored_run_id,
        binding_revision=stored_revision,
        workspace_id=workspace_id,
        member_id=member_id,
        session_id=session_id,
        scope=AccessScope.model_validate_json(scope_json),
        grant_set_sha256=grant_set_sha256,
        policy_epoch=policy_epoch,
        brand_id=brand_id,
        action_kind=None if action_kind is None else KnowledgeActionKind(action_kind),
        state=RunBindingState(state),
    )


def _validate_actor_binding(actor: ActorContext, binding: RunBinding) -> None:
    if (
        binding.workspace_id != actor.workspace_id
        or binding.member_id != actor.member_id
        or binding.session_id != actor.session_id
        or binding.scope != actor.conversation_scope
        or binding.policy_epoch != actor.policy_epoch
        or binding.binding_revision < 1
    ):
        conflict("run_binding_scope_conflict", binding.binding_id)
    expected_grants = sha256(
        "".join(sorted(contract_sha256(grant) for grant in actor.grants)).encode()
    ).hexdigest()
    if binding.grant_set_sha256 != expected_grants:
        conflict("run_binding_grant_set_conflict", binding.binding_id)


__all__ = ["put_run_binding", "run_binding"]
