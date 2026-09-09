"""Service boundaries for reusable shared feedback learning."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from jsonschema import Draft202012Validator

import ads_booster.agent.service.skills as skill_module
from ads_booster.agent.core.registry import CapabilityPolicy, ToolRegistry
from ads_booster.agent.service.skills import SKILLS
from ads_booster.bootstrap.lifecycle import build_installed_knowledge_runtime
from ads_booster.channels.contracts import ChannelIdentityBinding
from ads_booster.contracts.tool_capability import (
    AUTHENTICATED_SOURCE_REGISTRATIONS,
    EffectClass,
    ToolExecutionResult,
)
from ads_booster.knowledge.configuration import (
    KnowledgeSettings,
    initialize_knowledge_store,
    initialize_local_configuration,
)
from ads_booster.knowledge.contracts import (
    AuthenticatedEvent,
    AuthorityClass,
    GrantCapability,
    Provenance,
)
from ads_booster.knowledge.operation_enums import SkillOperationKind, SkillOrigin
from ads_booster.knowledge.skill_contracts import SkillData, SkillGetInput, SkillOperation
from ads_booster.knowledge.tool_contracts import (
    KnowledgeQuestionInput,
    ToolResultStatus,
    TrustedQuestionAnswer,
)
from ads_booster.knowledge.tools import ToolHost
from ads_booster.providers.codex_cli import CodexCli
from tests.knowledge.change_test_fixtures import NOW
from tests.knowledge.procedural_skill_test_support import (
    apply_skill,
    foreground_override_context,
    skill_record,
)
from tests.knowledge.test_curation_inputs import curation_input as fixture_curation_input

if TYPE_CHECKING:
    import pytest

    from tests.knowledge.test_curation_inputs import CurationInput

curation_input = fixture_curation_input


def test_conflict_answer_is_not_tool_approval(curation_input: CurationInput) -> None:
    # Given: a pending learning question and U2's admitted event from its original thread.
    repository, processor, job, _, _ = curation_input
    host = ToolHost(repository)
    work = processor.build_curation_work(job)
    admitted = work.request.authenticated_user_event
    assert admitted is not None
    canonical = repository.canonical_event(
        work.trusted_context.actor, admitted.evidence_ref.evidence_id
    )
    nonapprover = ChannelIdentityBinding(
        schema_version="trace.channel-identity-binding.v1",
        binding_id="slack-team-u2",
        installation_id="slack-team",
        external_user_id="U2",
        tenant_id=work.trusted_context.actor.workspace_id,
        member_id=work.trusted_context.actor.member_id,
        can_approve=False,
        created_at=NOW,
    )
    question = host.questions.ask(
        KnowledgeQuestionInput(
            schema="knowledge.tool.question.v1",
            question_id="question.feedback.conflict",
            problem="Two current shared procedures conflict.",
            recommendation="Choose the procedure backed by the current source revision.",
        ),
        work.trusted_context,
    ).question

    # When: U2 answers through the trusted question API, with no ToolApproval input.
    result = host.answer_question(
        TrustedQuestionAnswer(
            question_id=question.question_id,
            authenticated_event=AuthenticatedEvent(
                event_id=canonical.message_id,
                actor_ref=work.trusted_context.actor.actor_id,
                workspace_id=work.trusted_context.actor.workspace_id,
                scope=canonical.scope,
                provenance=Provenance.HUMAN_DIRECT,
                authority_class=AuthorityClass.DELEGATED_TEAM_RULE,
                capabilities=(GrantCapability.WRITE,),
                policy_epoch=work.trusted_context.capability_epoch,
                occurred_at=canonical.created_at,
            ),
            answer="현재 revision의 절차를 적용합니다.",
            explicitly_adopts=False,
            answered_at=question.created_at,
        ),
        work.trusted_context,
    )

    # Then: the current-thread answer resolves without effect-approval authority or receipt.
    assert not nonapprover.can_approve
    assert "can_approve" not in TrustedQuestionAnswer.model_fields
    assert result.question.answer_event_id == canonical.message_id
    assert result.adoption_receipt is None


def test_builtin_release_mismatch_uses_current_builtin_fallback(
    curation_input: CurationInput, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: an explicit builtin override stored against a stale base digest.
    repository, _, _, _, _ = curation_input
    host = ToolHost(repository)
    builtin = SKILLS[0]
    foreground, source_ref = foreground_override_context(
        repository, tmp_path / "slack-ingress.sqlite", builtin.skill_id
    )
    current = host.execute(
        "skill_get",
        SkillGetInput(schema="knowledge.tool.skill-get.v1", skill_id=builtin.skill_id).model_dump(
            mode="json", by_alias=True
        ),
        foreground,
    )
    assert current.status is ToolResultStatus.SUCCEEDED
    assert isinstance(current.data, SkillData)
    override = skill_record(
        skill_id=builtin.skill_id,
        version=f"{builtin.skill_id}.override.r1",
        origin=SkillOrigin.BUILTIN_OVERRIDE,
        protected=True,
        base_builtin_digest=current.data.record.digest,
        source_refs=(source_ref,),
        required_capability_ids=current.data.record.required_capability_ids,
        created_by=foreground.actor.actor_id,
    )
    written = apply_skill(
        host,
        foreground,
        operation_id="operation.skill.stale-builtin-override",
        operation=SkillOperation(
            operation_id="operation.skill.stale-builtin-override",
            kind=SkillOperationKind.CREATE,
            skill_id=override.skill_id,
            replacement_revision_id=override.version,
            record=override,
            source_refs=override.source_refs,
            reason="The current authenticated user explicitly requested this builtin override.",
        ),
    )
    assert written.status is ToolResultStatus.APPLIED
    monkeypatch.setattr(
        skill_module,
        "SKILLS",
        (
            replace(builtin, procedure=builtin.procedure + "\nRelease change."),
            *skill_module.SKILLS[1:],
        ),
    )

    # When: the learning tool resolves that skill against the current installed catalog.
    result = host.execute(
        "skill_get",
        SkillGetInput(schema="knowledge.tool.skill-get.v1", skill_id=builtin.skill_id).model_dump(
            mode="json", by_alias=True
        ),
        foreground,
    )

    # Then: it returns the current builtin and holds the stale override.
    assert result.status is ToolResultStatus.SUCCEEDED
    assert isinstance(result.data, SkillData)
    assert result.data.record.origin is SkillOrigin.BUILTIN
    assert result.data.override_status == "base_release_mismatch"
    held = repository.read_skill(foreground.actor, builtin.skill_id)
    assert held is not None
    assert held.record == override


def test_installed_knowledge_receipt_schema_accepts_adapter_execution_envelope(
    tmp_path: Path,
) -> None:
    # Given: descriptors from the installed knowledge catalog and an adapter result envelope.
    settings = KnowledgeSettings(
        root=tmp_path / "knowledge-root",
        control_root=tmp_path / "knowledge-control",
        policy_path=tmp_path / "knowledge-control/policy.json",
    )
    _ = initialize_local_configuration(*settings.require_enabled(), workspace_id="team")
    _ = initialize_knowledge_store(settings)
    installed = build_installed_knowledge_runtime(
        settings=settings,
        service_database=tmp_path / "service.sqlite",
        codex=CodexCli(executable=Path("/unused/codex"), model="test"),
        model_id="test",
    )
    try:
        receipt = ToolExecutionResult(
            schema_version="trace.tool-execution-result.v1",
            disposition="succeeded",
            invocation_sha256="a" * 64,
            output={},
            actual_cost_units=1,
            executor_id="knowledge.skill_apply",
        ).model_dump(mode="json")

        # When: the backend's persisted receipt envelope is checked against each descriptor.
        for descriptor in installed.adapter.descriptors(now=NOW):
            Draft202012Validator(descriptor.receipt_schema).validate(receipt)  # pyright: ignore[reportUnknownMemberType]

        # Then: all knowledge descriptors declare the execution envelope, not ToolResult output.
        assert all(
            descriptor.receipt_schema == ToolExecutionResult.model_json_schema()
            for descriptor in installed.adapter.descriptors(now=NOW)
        )
    finally:
        installed.runtime.close()


def test_unbound_run_exposes_no_learning_tools_or_context(tmp_path: Path) -> None:
    # Given: enabled installed knowledge whose candidate Run has no trusted ingress binding.
    settings = KnowledgeSettings(
        root=tmp_path / "knowledge-root",
        control_root=tmp_path / "knowledge-control",
        policy_path=tmp_path / "knowledge-control/policy.json",
    )
    _ = initialize_local_configuration(*settings.require_enabled(), workspace_id="team")
    _ = initialize_knowledge_store(settings)
    installed = build_installed_knowledge_runtime(
        settings=settings,
        service_database=tmp_path / "service.sqlite",
        codex=CodexCli(executable=Path("/unused/codex"), model="test"),
        model_id="test",
    )
    try:
        descriptors = installed.adapter.descriptors(now=NOW)
        source_authorized = {
            (descriptor.capability_id, descriptor.owner, descriptor.installation_id)
            for descriptor in descriptors
            if (
                descriptor.effect_class is EffectClass.CONTROL_PLANE_WRITE
                and descriptor.approval_policy.mode == "none"
            )
        }
        assert source_authorized == AUTHENTICATED_SOURCE_REGISTRATIONS
        snapshot = ToolRegistry(descriptors).snapshot_for_plan(
            snapshot_id="snapshot.unbound",
            run_id="run.unbound",
            remaining_tool_calls=4,
            remaining_cost_units=20,
            policy=CapabilityPolicy(),
            now=NOW,
        )

        # When: the adapter filters that unbound Run's planner snapshot.
        filtered = installed.adapter.filter_snapshot("run.unbound", snapshot)

        # Then: neither learned-skill nor feedback-write capability is exposed.
        assert {descriptor.capability_id for descriptor in filtered.descriptors}.isdisjoint(
            {"skill_list", "skill_get", "skill_apply", "memory_correct"}
        )
    finally:
        installed.runtime.close()
