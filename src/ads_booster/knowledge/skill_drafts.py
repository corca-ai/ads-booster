from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING, Never

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.change_validation import ChangeValidationError
from ads_booster.knowledge.contract_types import (
    EvidenceKind,
    InstructionAuthority,
    Provenance,
)
from ads_booster.knowledge.evidence_contracts import EvidenceRef
from ads_booster.knowledge.governance_contracts import AppliesTo
from ads_booster.knowledge.operation_enums import SkillOrigin
from ads_booster.knowledge.skill_contracts import (
    SkillApplyOperation,
    SkillDraftOperation,
    SkillOperation,
    SkillRecord,
)
from ads_booster.knowledge.skills import builtin_skill_records
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from ads_booster.knowledge.tool_contracts import TrustedInvocationContext
    from ads_booster.knowledge.tool_dependencies import ToolDependencies

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


def _reject(code: str, target_id: str) -> Never:
    raise ChangeValidationError(code, target_id)


def normalize_skill_operations(
    dependencies: ToolDependencies,
    operations: tuple[SkillApplyOperation, ...],
    context: TrustedInvocationContext,
    canonical_operation_id: str,
) -> tuple[SkillOperation, ...]:
    """Bind all mutations to admitted evidence and normalize semantic drafts."""
    source_refs: tuple[EvidenceRef, ...] | None = None
    normalized: list[SkillOperation] = []
    for operation in operations:
        match operation:
            case SkillOperation():
                if source_refs is None:
                    source_refs = _trusted_source_refs(dependencies, context)
                if any(reference not in source_refs for reference in operation.source_refs):
                    _reject("skill_operation_source_mismatch", operation.skill_id)
                normalized.append(operation)
            case SkillDraftOperation():
                if source_refs is None:
                    source_refs = _trusted_source_refs(dependencies, context)
                normalized.append(
                    _normalize_draft(
                        dependencies,
                        operation,
                        context,
                        source_refs,
                        canonical_operation_id,
                    )
                )
    return tuple(normalized)


def _normalize_draft(
    dependencies: ToolDependencies,
    operation: SkillDraftOperation,
    context: TrustedInvocationContext,
    source_refs: tuple[EvidenceRef, ...],
    canonical_operation_id: str,
) -> SkillOperation:
    draft = operation.draft
    if draft is None:
        return SkillOperation(
            operation_id=canonical_operation_id,
            kind=operation.kind,
            skill_id=operation.skill_id,
            expected_revision_id=operation.expected_revision_id,
            source_refs=source_refs,
            reason=operation.reason,
        )
    revision_id = operation.replacement_revision_id or _revision_id(
        canonical_operation_id,
        operation.skill_id,
    )
    builtin = next(
        (record for record in builtin_skill_records() if record.skill_id == operation.skill_id),
        None,
    )
    current = dependencies.repository.read_skill(context.actor, operation.skill_id)
    current_record = None if current is None else current.record
    if builtin is not None:
        origin = SkillOrigin.BUILTIN_OVERRIDE
        protected = True
        base_builtin_digest = builtin.digest
        fallback_capabilities = builtin.required_capability_ids
    elif current_record is not None:
        origin = current_record.origin
        protected = current_record.protected
        base_builtin_digest = current_record.base_builtin_digest
        fallback_capabilities = current_record.required_capability_ids
    else:
        origin = SkillOrigin.AGENT_CREATED
        protected = False
        base_builtin_digest = None
        fallback_capabilities = ()
    capabilities = (
        fallback_capabilities
        if draft.required_capability_ids is None
        else draft.required_capability_ids
    )
    applicability = (
        current_record.applicability
        if draft.applicability is None and current_record is not None
        else draft.applicability or AppliesTo()
    )
    created_by = context.actor.actor_id if current_record is None else current_record.created_by
    created_at = context.invoked_at if current_record is None else current_record.created_at
    stable: JsonObject = {
        "skill_id": operation.skill_id,
        "version": revision_id,
        "description": draft.description,
        "procedure": draft.procedure,
        "pitfalls": list(draft.pitfalls),
        "verification": list(draft.verification),
        "required_capability_ids": list(capabilities),
        "origin": origin.value,
        "protected": protected,
        "base_builtin_digest": base_builtin_digest,
        "source_refs": [
            _JSON_OBJECT.validate_python(reference.model_dump(mode="json"))
            for reference in source_refs
        ],
        "applicability": applicability.model_dump(mode="json", exclude_defaults=True),
        "created_by": created_by,
    }
    record = SkillRecord(
        schema="knowledge.skill-record.v1",
        skill_id=operation.skill_id,
        version=revision_id,
        description=draft.description,
        procedure=draft.procedure,
        pitfalls=draft.pitfalls,
        verification=draft.verification,
        required_capability_ids=capabilities,
        origin=origin,
        protected=protected,
        base_builtin_digest=base_builtin_digest,
        digest=contract_sha256(stable),
        applicability=applicability,
        source_refs=source_refs,
        created_by=created_by,
        created_at=created_at,
        updated_at=context.invoked_at,
    )
    return SkillOperation(
        operation_id=canonical_operation_id,
        kind=operation.kind,
        skill_id=operation.skill_id,
        expected_revision_id=operation.expected_revision_id,
        replacement_revision_id=revision_id,
        record=record,
        source_refs=source_refs,
        reason=operation.reason,
    )


def _revision_id(operation_id: str, skill_id: str) -> str:
    digest = sha256(f"{operation_id}\0{skill_id}".encode()).hexdigest()
    return f"skillrev.{digest[:32]}"


def _trusted_source_refs(
    dependencies: ToolDependencies,
    context: TrustedInvocationContext,
) -> tuple[EvidenceRef, ...]:
    event = context.source_fetch_event
    if event is not None:
        return (
            EvidenceRef(
                evidence_kind=EvidenceKind.CONVERSATION_EVENT,
                evidence_id=event.message_id,
                revision_id=str(event.revision),
                quote_sha256=sha256(event.text.encode()).hexdigest(),
                scope=event.scope,
                instruction_authority=InstructionAuthority.AUTHORIZED_USER,
                provenance=Provenance.HUMAN_DIRECT,
            ),
        )
    references: list[EvidenceRef] = []
    for capability in context.source_capabilities:
        stored = dependencies.repository.read_source(context.actor, capability.source_id)
        if stored is None or stored.source.revision_id != capability.revision_id:
            _reject("skill_draft_source_stale", capability.source_id)
        segments = {segment.segment_id: segment for segment in stored.segments}
        for segment_id in capability.segment_ids:
            segment = segments.get(segment_id)
            if segment is None:
                _reject("skill_draft_segment_unavailable", segment_id)
            references.append(
                EvidenceRef(
                    evidence_kind=EvidenceKind.SOURCE_SEGMENT,
                    evidence_id=segment.segment_id,
                    revision_id=segment.revision_id,
                    segment_id=segment.segment_id,
                    quote_sha256=segment.content_sha256,
                    scope=stored.source.scope,
                    instruction_authority=InstructionAuthority.DATA,
                    provenance=Provenance.EXTERNAL,
                )
            )
    unique = tuple(
        {
            (
                reference.evidence_kind,
                reference.evidence_id,
                reference.revision_id,
                reference.segment_id,
            ): reference
            for reference in references
        }.values()
    )
    if not unique:
        _reject("skill_draft_source_required", context.invocation_id)
    return unique[:128]


__all__ = ["normalize_skill_operations"]
