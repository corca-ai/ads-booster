from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Never

from ads_booster.knowledge.change_validation import ChangeValidationError
from ads_booster.knowledge.contract_types import (
    ConversationRole,
    EvidenceKind,
    ScopeKind,
)
from ads_booster.knowledge.operation_enums import SkillOrigin
from ads_booster.knowledge.skill_authoring import requested_skill_action
from ads_booster.knowledge.skill_source_currentness import require_current_skill_sources
from ads_booster.knowledge.skills import builtin_skill_records, known_capability_ids

if TYPE_CHECKING:
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.scope_contracts import ActorContext
    from ads_booster.knowledge.skill_contracts import SkillOperation, SkillRecord
    from ads_booster.knowledge.tool_contracts import TrustedInvocationContext


@dataclass(frozen=True, slots=True)
class _RecordValidation:
    actor: ActorContext
    context: TrustedInvocationContext
    builtin: SkillRecord | None
    current: SkillRecord | None
    known_capabilities: frozenset[str]


def _reject(code: str, target_id: str | None = None) -> Never:
    raise ChangeValidationError(code, target_id)


def validate_skill_operations(
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    operations: tuple[SkillOperation, ...],
    context: TrustedInvocationContext,
) -> None:
    """Validate model-authored skill changes against server-owned state and authority."""
    if actor.conversation_scope.kind not in {ScopeKind.WORKSPACE, ScopeKind.CHANNEL}:
        _reject("skill_shared_write_required")
    _require_requested_mutation(repository, operations, context)
    if len({operation.skill_id for operation in operations}) != len(operations):
        _reject("skill_operation_target_duplicate")
    builtins = {record.skill_id: record for record in builtin_skill_records()}
    known_capabilities = known_capability_ids()
    for operation in operations:
        require_current_skill_sources(
            repository,
            actor,
            operation.skill_id,
            operation.source_refs,
        )
        current = repository.read_skill(actor, operation.skill_id)
        builtin = builtins.get(operation.skill_id)
        record = operation.record
        if record is not None:
            _validate_record(
                record,
                _RecordValidation(
                    actor=actor,
                    context=context,
                    builtin=builtin,
                    current=current.record if current is not None else None,
                    known_capabilities=known_capabilities,
                ),
            )
        if (current is not None and current.record.protected) or builtin is not None:
            _require_explicit_foreground(repository, operation, context)


def _require_requested_mutation(
    repository: SqliteKnowledgeRepository,
    operations: tuple[SkillOperation, ...],
    context: TrustedInvocationContext,
) -> None:
    event = context.source_fetch_event
    if (
        event is None
        or context.run_id.startswith("background.")
        or event.role is not ConversationRole.USER
        or event.speaker_ref != context.actor.actor_id
        or event.scope != context.actor.conversation_scope
        or repository.canonical_event(context.actor, event.message_id) != event
    ):
        _reject("skill_direct_user_request_required")
    requested = requested_skill_action(event.text)
    if requested is None:
        _reject("skill_explicit_request_required")
    if len(operations) != 1:
        _reject("skill_one_target_per_request")
    operation = operations[0]
    actual = "update" if operation.kind.value == "supersede" else operation.kind.value
    if actual == "create" and operation.skill_id in {
        item.skill_id for item in builtin_skill_records()
    }:
        actual = "update"
    if actual != requested.value:
        _reject("skill_requested_action_mismatch", operation.skill_id)
    if not any(
        reference.evidence_kind is EvidenceKind.CONVERSATION_EVENT
        and reference.evidence_id == event.message_id
        and reference.revision_id == str(event.revision)
        and reference.scope == event.scope
        for reference in operation.source_refs
    ):
        _reject("skill_request_source_mismatch", operation.skill_id)


def _validate_record(
    record: SkillRecord,
    validation: _RecordValidation,
) -> None:
    _validate_record_metadata(record, validation)
    _validate_record_origin(record, validation.builtin)
    _validate_existing_policy(record, validation.current, validation.builtin)


def _validate_record_metadata(
    record: SkillRecord,
    validation: _RecordValidation,
) -> None:
    if record.authority_ref is not None:
        _reject("skill_payload_authority_forbidden", record.skill_id)
    expected_creator = (
        validation.actor.actor_id if validation.current is None else validation.current.created_by
    )
    if record.created_by != expected_creator:
        _reject("skill_creator_mismatch", record.skill_id)
    if validation.current is not None and (
        record.created_at != validation.current.created_at
        or record.updated_at < validation.current.updated_at
    ):
        _reject("skill_revision_time_invalid", record.skill_id)
    if not set(record.required_capability_ids).issubset(validation.known_capabilities):
        _reject("skill_capability_unknown", record.skill_id)
    if (
        record.applicability.task_ref is not None
        and record.applicability.task_ref != validation.context.task_id
    ):
        _reject("skill_applicability_task_mismatch", record.skill_id)


def _validate_record_origin(
    record: SkillRecord,
    builtin: SkillRecord | None,
) -> None:
    if builtin is None:
        if record.origin is not SkillOrigin.AGENT_CREATED or record.protected:
            _reject("skill_origin_forbidden", record.skill_id)
    elif (
        record.origin is not SkillOrigin.BUILTIN_OVERRIDE
        or not record.protected
        or record.base_builtin_digest != builtin.digest
        or record.required_capability_ids != builtin.required_capability_ids
    ):
        _reject("skill_builtin_override_invalid", record.skill_id)


def _validate_existing_policy(
    record: SkillRecord,
    current: SkillRecord | None,
    builtin: SkillRecord | None,
) -> None:
    if (
        current is not None
        and builtin is None
        and (
            current.origin is not record.origin
            or current.protected != record.protected
            or current.base_builtin_digest != record.base_builtin_digest
        )
    ):
        _reject("skill_policy_change_forbidden", record.skill_id)


def _require_explicit_foreground(
    repository: SqliteKnowledgeRepository,
    operation: SkillOperation,
    context: TrustedInvocationContext,
) -> None:
    event = context.source_fetch_event
    if event is None or context.run_id.startswith("background."):
        _reject("skill_protected_foreground_required", operation.skill_id)
    if (
        event.role is not ConversationRole.USER
        or event.scope.kind not in {ScopeKind.WORKSPACE, ScopeKind.CHANNEL}
        or event.speaker_ref != context.actor.actor_id
    ):
        _reject("skill_protected_user_event_required", operation.skill_id)
    canonical = repository.canonical_event(context.actor, event.message_id)
    if canonical != event:
        _reject("skill_protected_event_stale", operation.skill_id)
    if not any(
        reference.evidence_kind is EvidenceKind.CONVERSATION_EVENT
        and reference.evidence_id == event.message_id
        and reference.revision_id == str(event.revision)
        and reference.scope == event.scope
        for reference in operation.source_refs
    ):
        _reject("skill_protected_source_mismatch", operation.skill_id)
    pattern = rf"(?<![\w.-]){re.escape(operation.skill_id)}(?![\w.-])"
    if re.search(pattern, event.text) is None:
        _reject("skill_protected_target_not_explicit", operation.skill_id)


__all__ = ["validate_skill_operations"]
