"""Procedural-skill learning contracts for the shared knowledge repository."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from ads_booster.agent.service.skills import SKILLS
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.knowledge_selection import KnowledgeActionKind
from ads_booster.knowledge.contracts import ConversationEventKind
from ads_booster.knowledge.governance_contracts import AppliesTo
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.operation_enums import SkillOperationKind, SkillOrigin
from ads_booster.knowledge.repository import MembershipRole
from ads_booster.knowledge.skill_contracts import (
    SkillData,
    SkillGetInput,
    SkillOperation,
)
from ads_booster.knowledge.skills import KnowledgeSkills
from ads_booster.knowledge.tool_contracts import ToolResultStatus
from ads_booster.knowledge.tools import ToolHost
from ads_booster.transport.json_types import JsonObject
from tests.knowledge.procedural_skill_test_support import (
    apply_skill,
    foreground_override_context,
    private_source_ref,
    skill_record,
)
from tests.knowledge.test_curation_inputs import curation_input as fixture_curation_input
from tests.knowledge.test_curation_inputs import envelope

if TYPE_CHECKING:
    from pathlib import Path

    from tests.knowledge.test_curation_inputs import CurationInput

curation_input = fixture_curation_input
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


def test_model_skill_draft_derives_strict_persisted_record(
    curation_input: CurationInput,
) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    work = processor.build_curation_work(job)
    payload: JsonObject = {
        "schema": "knowledge.tool.skill-apply.v1",
        "operation_id": "operation.skill.model-draft",
        "operations": [
            {
                "operation_id": "operation.skill.model-draft.item",
                "kind": "create",
                "skill_id": "learned.model-draft",
                "draft": {
                    "description": "Reuse a verified campaign handoff.",
                    "procedure": "Read the brief, preserve scope, and verify the receipt.",
                    "pitfalls": ["Do not infer approval from a draft."],
                    "verification": ["Check the terminal receipt."],
                    "required_capability_ids": ["knowledge_search"],
                },
                "reason": "The current trusted source contains a reusable procedure.",
            }
        ],
    }

    # When
    result = ToolHost(repository).execute("skill_apply", payload, work.trusted_context)

    # Then
    stored = repository.read_skill(processor.actor, "learned.model-draft")
    assert result.status is ToolResultStatus.APPLIED
    assert stored is not None
    assert stored.record.origin is SkillOrigin.AGENT_CREATED
    assert not stored.record.protected
    assert stored.record.created_by == processor.actor.actor_id
    assert stored.record.created_at == work.trusted_context.invoked_at
    assert stored.record.updated_at == work.trusted_context.invoked_at
    assert stored.record.version.startswith("skillrev.")
    assert stored.record.source_refs
    assert stored.record.digest == contract_sha256(stored.record.stable_content())


def test_complete_skill_record_with_forged_digest_is_rejected(
    curation_input: CurationInput,
) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    work = processor.build_curation_work(job)
    authenticated = work.request.authenticated_user_event
    assert authenticated is not None
    record = skill_record(
        skill_id="learned.forged-digest",
        version="learned.forged-digest.r1",
        origin=SkillOrigin.AGENT_CREATED,
        protected=False,
        source_refs=(authenticated.evidence_ref,),
    )
    forged_record: JsonObject = {
        **_JSON_OBJECT.validate_python(record.model_dump(mode="json", by_alias=True)),
        "digest": "0" * 64,
    }
    payload: JsonObject = {
        "schema": "knowledge.tool.skill-apply.v1",
        "operation_id": "operation.skill.forged-digest",
        "operations": [
            {
                "operation_id": "operation.skill.forged-digest",
                "kind": "create",
                "skill_id": record.skill_id,
                "replacement_revision_id": record.version,
                "record": forged_record,
                "source_refs": [
                    _JSON_OBJECT.validate_python(authenticated.evidence_ref.model_dump(mode="json"))
                ],
                "reason": "A complete strict payload must retain its exact digest.",
            }
        ],
    }

    # When
    result = ToolHost(repository).execute("skill_apply", payload, work.trusted_context)

    # Then
    assert result.status is ToolResultStatus.REJECTED
    assert repository.read_skill(processor.actor, record.skill_id) is None


def test_generic_catalog_discovers_subject_skill_but_explicit_mismatch_excludes(
    curation_input: CurationInput,
) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    work = processor.build_curation_work(job)
    payload: JsonObject = {
        "schema": "knowledge.tool.skill-apply.v1",
        "operation_id": "operation.skill.subject-filter",
        "operations": [
            {
                "kind": "create",
                "skill_id": "learned.subject-filter",
                "draft": {
                    "description": "Answer the synthetic fern readiness question.",
                    "procedure": "Answer only the stored readiness code.",
                    "applicability": {
                        "action_kinds": ["team_chat"],
                        "subject_key": "synthetic fern readiness code",
                    },
                },
                "reason": "Retain the subject-scoped team procedure.",
            }
        ],
    }
    applied = ToolHost(repository).execute("skill_apply", payload, work.trusted_context)
    catalog = KnowledgeSkills(repository)

    # When
    generic = catalog.list(
        processor.actor,
        AppliesTo(action_kinds=(KnowledgeActionKind.TEAM_CHAT,)),
    )
    mismatched = catalog.list(
        processor.actor,
        AppliesTo(
            action_kinds=(KnowledgeActionKind.TEAM_CHAT,),
            subject_key="different subject",
        ),
    )

    # Then
    assert applied.status is ToolResultStatus.APPLIED
    assert any(item.reference.skill_id == "learned.subject-filter" for item in generic)
    assert all(item.reference.skill_id != "learned.subject-filter" for item in mismatched)


def test_agent_created_skill_is_mutable(curation_input: CurationInput) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    host = ToolHost(repository)
    work = processor.build_curation_work(job)
    authenticated = work.request.authenticated_user_event
    assert authenticated is not None
    created = skill_record(
        skill_id="learned.campaign-handoff",
        version="learned.campaign-handoff.r1",
        origin=SkillOrigin.AGENT_CREATED,
        protected=False,
        source_refs=(authenticated.evidence_ref,),
    )

    # When
    first = apply_skill(
        host,
        work.trusted_context,
        operation_id="operation.skill.create",
        operation=SkillOperation(
            operation_id="operation.skill.create",
            kind=SkillOperationKind.CREATE,
            skill_id=created.skill_id,
            replacement_revision_id=created.version,
            record=created,
            source_refs=created.source_refs,
            reason="A source-linked successful procedure can be reused.",
        ),
    )
    revised = skill_record(
        skill_id=created.skill_id,
        version="learned.campaign-handoff.r2",
        origin=created.origin,
        protected=created.protected,
        source_refs=created.source_refs,
        procedure="Read the current brief, preserve scope, then retain the terminal receipt.",
    )
    second = apply_skill(
        host,
        work.trusted_context,
        operation_id="operation.skill.update",
        operation=SkillOperation(
            operation_id="operation.skill.update",
            kind=SkillOperationKind.UPDATE,
            skill_id=revised.skill_id,
            expected_revision_id=created.version,
            replacement_revision_id=revised.version,
            record=revised,
            source_refs=revised.source_refs,
            reason="The same source clarified the handoff order.",
        ),
    )

    # Then
    stored = repository.read_skill(processor.actor, created.skill_id)
    assert first.status is ToolResultStatus.APPLIED
    assert second.status is ToolResultStatus.APPLIED
    assert stored is not None
    assert stored.record == revised


def test_entire_builtin_catalog_is_protected(curation_input: CurationInput) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    host = ToolHost(repository)
    work = processor.build_curation_work(job)
    authenticated = work.request.authenticated_user_event
    assert authenticated is not None

    # When
    receipts = tuple(
        apply_skill(
            host,
            work.trusted_context,
            operation_id=f"operation.skill.auto.{builtin.skill_id}",
            operation=SkillOperation(
                operation_id=f"operation.skill.auto.{builtin.skill_id}",
                kind=SkillOperationKind.CREATE,
                skill_id=builtin.skill_id,
                replacement_revision_id=f"{builtin.skill_id}.automatic.r1",
                record=skill_record(
                    skill_id=builtin.skill_id,
                    version=f"{builtin.skill_id}.automatic.r1",
                    origin=SkillOrigin.AGENT_CREATED,
                    protected=False,
                    source_refs=(authenticated.evidence_ref,),
                ),
                source_refs=(authenticated.evidence_ref,),
                reason="Background learning attempted to shadow a builtin procedure.",
            ),
        )
        for builtin in SKILLS
    )

    # Then
    assert receipts
    assert all(receipt.status is ToolResultStatus.REJECTED for receipt in receipts)
    assert all(
        repository.read_skill(processor.actor, builtin.skill_id) is None for builtin in SKILLS
    )


def test_explicit_builtin_override_stays_protected(
    curation_input: CurationInput, tmp_path: Path
) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    host = ToolHost(repository)
    work = processor.build_curation_work(job)
    builtin = SKILLS[0]
    projected = host.execute(
        "skill_get",
        SkillGetInput(schema="knowledge.tool.skill-get.v1", skill_id=builtin.skill_id).model_dump(
            mode="json", by_alias=True
        ),
        work.trusted_context,
    )
    assert projected.status is ToolResultStatus.SUCCEEDED
    assert isinstance(projected.data, SkillData)
    foreground, source_ref = foreground_override_context(
        repository, tmp_path / "foreground.sqlite", builtin.skill_id
    )
    override = skill_record(
        skill_id=builtin.skill_id,
        version=f"{builtin.skill_id}.override.r1",
        origin=SkillOrigin.BUILTIN_OVERRIDE,
        protected=True,
        base_builtin_digest=projected.data.record.digest,
        source_refs=(source_ref,),
        created_by=foreground.actor.actor_id,
        required_capability_ids=builtin.required_capabilities,
    )

    # When
    accepted = apply_skill(
        host,
        foreground,
        operation_id="operation.skill.override",
        operation=SkillOperation(
            operation_id="operation.skill.override",
            kind=SkillOperationKind.CREATE,
            skill_id=override.skill_id,
            replacement_revision_id=override.version,
            record=override,
            source_refs=override.source_refs,
            reason="The authenticated current user explicitly requested this target override.",
        ),
    )
    automatic_revision = skill_record(
        skill_id=override.skill_id,
        version=f"{builtin.skill_id}.override.r2",
        origin=override.origin,
        protected=override.protected,
        base_builtin_digest=override.base_builtin_digest,
        source_refs=override.source_refs,
        created_by=override.created_by,
        required_capability_ids=override.required_capability_ids,
    )
    automatic_rewrite = apply_skill(
        host,
        work.trusted_context,
        operation_id="operation.skill.override.auto-rewrite",
        operation=SkillOperation(
            operation_id="operation.skill.override.auto-rewrite",
            kind=SkillOperationKind.UPDATE,
            skill_id=override.skill_id,
            expected_revision_id=override.version,
            replacement_revision_id=f"{builtin.skill_id}.override.r2",
            record=automatic_revision,
            source_refs=override.source_refs,
            reason="Background learning attempted to rewrite the protected override.",
        ),
    )

    # Then
    assert accepted.status is ToolResultStatus.APPLIED
    assert automatic_rewrite.status is ToolResultStatus.REJECTED
    stored = repository.read_skill(foreground.actor, builtin.skill_id)
    assert stored is not None
    assert stored.record == override


@pytest.mark.parametrize("private", [False, True])
def test_skill_apply_rejects_stale_or_private_source(
    curation_input: CurationInput, private: bool
) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    host = ToolHost(repository)
    work = processor.build_curation_work(job)
    authenticated = work.request.authenticated_user_event
    assert authenticated is not None
    source_ref = authenticated.evidence_ref
    if private:
        source_ref = private_source_ref(repository, processor.actor)
    else:
        stale = event.model_copy(update={"revision": 2, "text": "The prior source is stale."})
        _ = KnowledgeIngestion(repository).ingest(
            processor.actor, stale, envelope(stale, "delivery.stale")
        )
    record = skill_record(
        skill_id="learned.source-bound",
        version="learned.source-bound.r1",
        origin=SkillOrigin.AGENT_CREATED,
        protected=False,
        source_refs=(source_ref,),
    )

    # When
    receipt = apply_skill(
        host,
        work.trusted_context,
        operation_id="operation.skill.reject-source",
        operation=SkillOperation(
            operation_id="operation.skill.reject-source",
            kind=SkillOperationKind.CREATE,
            skill_id=record.skill_id,
            replacement_revision_id=record.version,
            record=record,
            source_refs=record.source_refs,
            reason="A stale or private source cannot create shared learning.",
        ),
    )

    # Then
    assert receipt.status is ToolResultStatus.REJECTED
    assert repository.read_skill(processor.actor, record.skill_id) is None


@pytest.mark.parametrize(
    ("event_kind", "text"),
    [
        (ConversationEventKind.MESSAGE_EDITED, "The source procedure was revised."),
        (ConversationEventKind.MESSAGE_DELETED, ""),
    ],
)
def test_skill_selection_tracks_exact_source_currentness(
    curation_input: CurationInput,
    event_kind: ConversationEventKind,
    text: str,
) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    work = processor.build_curation_work(job)
    authenticated = work.request.authenticated_user_event
    assert authenticated is not None
    record = skill_record(
        skill_id="learned.source-currentness",
        version="learned.source-currentness.r1",
        origin=SkillOrigin.AGENT_CREATED,
        protected=False,
        source_refs=(authenticated.evidence_ref,),
    )
    applied = apply_skill(
        ToolHost(repository),
        work.trusted_context,
        operation_id="operation.skill.source-currentness",
        operation=SkillOperation(
            operation_id="operation.skill.source-currentness",
            kind=SkillOperationKind.CREATE,
            skill_id=record.skill_id,
            replacement_revision_id=record.version,
            record=record,
            source_refs=record.source_refs,
            reason="Persist a skill whose exact source revision must remain current.",
        ),
    )
    reader = processor.actor.model_copy(
        update={
            "actor_id": "actor.reader",
            "member_id": "member.reader",
            "session_id": "session.reader",
            "grants": tuple(
                grant.model_copy(update={"grant_id": f"{grant.grant_id}.reader"})
                for grant in processor.actor.grants
            ),
        }
    )
    repository.register_actor(reader, MembershipRole.EDITOR)
    catalog = KnowledgeSkills(repository)
    selected = catalog.reference(reader, record.skill_id)
    assert applied.status is ToolResultStatus.APPLIED
    assert selected is not None
    assert selected.source_refs == record.source_refs
    assert catalog.is_current(reader, selected)

    # When
    revised = event.model_copy(
        update={
            "revision": 2,
            "event_kind": event_kind,
            "edited_at": event.created_at + timedelta(seconds=1),
            "text": text,
        }
    )
    _ = KnowledgeIngestion(repository).ingest(
        processor.actor,
        revised,
        envelope(revised, f"delivery.source-currentness.{event_kind.value}"),
    )

    # Then
    assert not catalog.is_current(reader, selected)
    assert catalog.get(reader, record.skill_id) is None
    assert all(item.reference.skill_id != record.skill_id for item in catalog.list(reader))
    builtin = catalog.reference(reader, SKILLS[0].skill_id)
    assert builtin is not None
    assert not builtin.source_refs
    assert catalog.is_current(reader, builtin)
