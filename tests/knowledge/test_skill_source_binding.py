"""Strict skill mutations must use evidence admitted to their invocation."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING, assert_never

import pytest

from ads_booster.knowledge.contract_types import EvidenceKind, InstructionAuthority, Provenance
from ads_booster.knowledge.evidence_contracts import EvidenceRef
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.operation_enums import SkillOperationKind, SkillOrigin
from ads_booster.knowledge.skill_contracts import SkillOperation, SkillRecord
from ads_booster.knowledge.tool_contracts import ToolResultStatus
from ads_booster.knowledge.tools import ToolHost
from tests.knowledge.procedural_skill_test_support import apply_skill, skill_record
from tests.knowledge.test_curation_inputs import curation_input as fixture_curation_input
from tests.knowledge.test_curation_inputs import envelope

if TYPE_CHECKING:
    from tests.knowledge.test_curation_inputs import CurationInput

curation_input = fixture_curation_input


def _replacement(
    kind: SkillOperationKind, record: SkillRecord
) -> tuple[str | None, SkillRecord | None]:
    match kind:
        case SkillOperationKind.RETRACT:
            return None, None
        case SkillOperationKind.CREATE | SkillOperationKind.UPDATE | SkillOperationKind.SUPERSEDE:
            return record.version, record
        case _:
            assert_never(kind)


@pytest.mark.parametrize("capability_only", [False, True])
@pytest.mark.parametrize("kind", list(SkillOperationKind))
def test_strict_mutation_rejects_unrelated_current_source(
    curation_input: CurationInput,
    kind: SkillOperationKind,
    capability_only: bool,
) -> None:
    # Given: both sources are current and readable in the same workspace.
    repository, processor, job, event, receipt = curation_input
    work = processor.build_curation_work(job)
    context = work.trusted_context
    authenticated = work.request.authenticated_user_event
    assert authenticated is not None
    other = event.model_copy(
        update={"message_id": "message.unrelated", "conversation_id": "conversation.unrelated"}
    )
    delivery = KnowledgeIngestion(repository).ingest(
        processor.actor, other, envelope(other, "delivery.unrelated")
    )
    if capability_only:
        context = context.model_copy(update={"source_fetch_event": None})
        sources = (
            repository.read_source(processor.actor, receipt.source_id),
            repository.read_source(processor.actor, delivery.unit_receipts[0].receipt.source_id),
        )
        assert all(source is not None for source in sources)
        references = tuple(
            EvidenceRef(
                evidence_kind=EvidenceKind.SOURCE_SEGMENT,
                evidence_id=segment.segment_id,
                revision_id=segment.revision_id,
                segment_id=segment.segment_id,
                quote_sha256=segment.content_sha256,
                scope=source.source.scope,
                instruction_authority=InstructionAuthority.DATA,
                provenance=Provenance.EXTERNAL,
            )
            for source in sources
            if source is not None
            for segment in source.segments[:1]
        )
        admitted, unrelated = references
    else:
        admitted = authenticated.evidence_ref
        unrelated = EvidenceRef(
            evidence_kind=EvidenceKind.CONVERSATION_EVENT,
            evidence_id=other.message_id,
            revision_id=str(other.revision),
            quote_sha256=sha256(other.text.encode()).hexdigest(),
            scope=other.scope,
            instruction_authority=InstructionAuthority.AUTHORIZED_USER,
            provenance=Provenance.HUMAN_DIRECT,
        )
    host = ToolHost(repository)
    skill_id = "learned.source-binding"
    seed = skill_record(
        skill_id=skill_id,
        version="skill.r1",
        origin=SkillOrigin.AGENT_CREATED,
        protected=False,
        source_refs=(admitted,),
    )
    match kind:
        case SkillOperationKind.CREATE:
            expected_revision = None
        case SkillOperationKind.UPDATE | SkillOperationKind.SUPERSEDE | SkillOperationKind.RETRACT:
            expected_revision = seed.version
        case _:
            assert_never(kind)
    if expected_revision is not None:
        result = apply_skill(
            host,
            context,
            operation_id="seed",
            operation=SkillOperation(
                operation_id="seed",
                kind=SkillOperationKind.CREATE,
                skill_id=skill_id,
                replacement_revision_id=seed.version,
                record=seed,
                source_refs=seed.source_refs,
                reason="Seed an admitted procedure.",
            ),
        )
        assert result.status is ToolResultStatus.APPLIED
    before = repository.read_skill(processor.actor, skill_id)

    # When: an unrelated source must not authorize the mutation; the admitted one can.
    for reference, rejected in (
        (unrelated, True),
        (admitted, False),
    ):
        record = skill_record(
            skill_id=skill_id,
            version="skill.r2",
            origin=SkillOrigin.AGENT_CREATED,
            protected=False,
            source_refs=(reference,),
        )
        replacement_revision, replacement_record = _replacement(kind, record)
        expected = ToolResultStatus.REJECTED if rejected else ToolResultStatus.APPLIED
        request = SkillOperation(
            operation_id=f"mutation.{expected.value}",
            kind=kind,
            skill_id=skill_id,
            expected_revision_id=expected_revision,
            replacement_revision_id=replacement_revision,
            record=replacement_record,
            source_refs=(reference,),
            reason="Apply a source-bound mutation.",
        )
        result = apply_skill(host, context, operation_id=request.operation_id, operation=request)
        assert result.status is expected
        if rejected:
            assert repository.read_skill(processor.actor, skill_id) == before
        else:
            stored = repository.read_skill(processor.actor, skill_id)
            if replacement_record is None:
                assert stored is None
            else:
                assert stored is not None
                assert stored.record == replacement_record
