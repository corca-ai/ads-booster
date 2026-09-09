"""Shared builders for procedural-skill learning contract tests."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.agent.service.knowledge_ingress import CanonicalKnowledgeIngress
from ads_booster.channels.contracts import ChannelIdentityBinding
from ads_booster.channels.knowledge_ingress_slack import SlackIngressRequest, build_slack_ingress
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.contracts import (
    ConversationEvent,
    ConversationEventKind,
    ConversationRole,
    EvidenceKind,
    InstructionAuthority,
    Provenance,
)
from ads_booster.knowledge.evidence_contracts import EvidenceRef
from ads_booster.knowledge.governance_contracts import AppliesTo
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.repository import MembershipRole, SqliteKnowledgeRepository
from ads_booster.knowledge.skill_contracts import SkillApplyInput, SkillOperation, SkillRecord
from ads_booster.knowledge.tool_contracts import ToolResult, TrustedInvocationContext
from ads_booster.transport.json_types import JsonObject
from tests.knowledge.change_test_fixtures import NOW, PRIVATE_SCOPE
from tests.knowledge.test_curation_inputs import envelope

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.knowledge.operation_enums import SkillOrigin
    from ads_booster.knowledge.scope_contracts import ActorContext
    from ads_booster.knowledge.tools import ToolHost


def skill_record(  # noqa: PLR0913 - test builder exposes independent contract fields
    *,
    skill_id: str,
    version: str,
    origin: SkillOrigin,
    protected: bool,
    source_refs: tuple[EvidenceRef, ...],
    base_builtin_digest: str | None = None,
    procedure: str = "Read the current brief, preserve scope, and retain the terminal receipt.",
    created_by: str = "actor.editor",
    required_capability_ids: tuple[str, ...] = ("knowledge_search",),
) -> SkillRecord:
    """Build a source-linked skill with the record's stable content digest."""
    description = "Keep a verified campaign handoff procedure."
    pitfalls = ("Do not infer approval from a draft.",)
    verification = ("Check the terminal receipt before reuse.",)
    stable_content: JsonObject = {
        "skill_id": skill_id,
        "version": version,
        "description": description,
        "procedure": procedure,
        "pitfalls": list(pitfalls),
        "verification": list(verification),
        "required_capability_ids": list(required_capability_ids),
        "origin": origin.value,
        "protected": protected,
        "base_builtin_digest": base_builtin_digest,
        "source_refs": [
            _JSON_OBJECT.validate_python(item.model_dump(mode="json")) for item in source_refs
        ],
        "applicability": {},
        "created_by": created_by,
    }
    return SkillRecord(
        schema="knowledge.skill-record.v1",
        skill_id=skill_id,
        version=version,
        description=description,
        procedure=procedure,
        pitfalls=pitfalls,
        verification=verification,
        required_capability_ids=required_capability_ids,
        origin=origin,
        protected=protected,
        base_builtin_digest=base_builtin_digest,
        digest=contract_sha256(stable_content),
        applicability=AppliesTo(),
        source_refs=source_refs,
        created_by=created_by,
        created_at=NOW,
        updated_at=NOW,
    )


def apply_skill(
    host: ToolHost,
    context: TrustedInvocationContext,
    *,
    operation_id: str,
    operation: SkillOperation,
) -> ToolResult:
    """Submit a skill operation through the public guarded tool boundary."""
    request = SkillApplyInput(
        schema="knowledge.tool.skill-apply.v1",
        operation_id=operation_id,
        operations=(operation,),
    )
    return host.execute("skill_apply", request.model_dump(mode="json", by_alias=True), context)


def private_source_ref(repository: SqliteKnowledgeRepository, actor: ActorContext) -> EvidenceRef:
    """Persist a private event whose evidence must not create shared learning."""
    private_actor = actor.model_copy(
        update={
            "actor_id": "actor.private",
            "member_id": "member.private",
            "session_id": "session.private",
            "conversation_scope": PRIVATE_SCOPE,
            "grants": tuple(
                grant.model_copy(update={"scope": PRIVATE_SCOPE}) for grant in actor.grants
            ),
        }
    )
    repository.register_actor(private_actor, MembershipRole.EDITOR)
    event = ConversationEvent(
        conversation_id="conversation.private",
        message_id="message.private",
        revision=1,
        sequence=1,
        role=ConversationRole.USER,
        speaker_ref=private_actor.actor_id,
        created_at=NOW,
        event_kind=ConversationEventKind.MESSAGE_FINALIZED,
        scope=PRIVATE_SCOPE,
        text="Do not turn this private correction into shared learning.",
    )
    _ = KnowledgeIngestion(repository).ingest(
        private_actor, event, envelope(event, "delivery.private")
    )
    return EvidenceRef(
        evidence_kind=EvidenceKind.CONVERSATION_EVENT,
        evidence_id=event.message_id,
        revision_id=str(event.revision),
        quote_sha256=sha256(event.text.encode()).hexdigest(),
        scope=PRIVATE_SCOPE,
        instruction_authority=InstructionAuthority.AUTHORIZED_USER,
        provenance=Provenance.HUMAN_DIRECT,
    )


def foreground_override_context(
    repository: SqliteKnowledgeRepository, database_path: Path, skill_id: str
) -> tuple[TrustedInvocationContext, EvidenceRef]:
    """Admit a current shared user event for one exact builtin override target."""
    pending = build_slack_ingress(
        SlackIngressRequest(
            conversation_id="conversation.override",
            message_id="message.builtin-override",
            run_id="run.builtin-override",
            action="input",
            text=f"Please revise the procedure for {skill_id} in this request.",
            revision=1,
            external_revision="1",
            created_revision=str(NOW.timestamp()),
            event_kind=ConversationEventKind.MESSAGE_FINALIZED,
            identity=ChannelIdentityBinding(
                schema_version="trace.channel-identity-binding.v1",
                binding_id="binding.member.override",
                installation_id="installation.slack",
                external_user_id="member.override",
                tenant_id="workspace.alpha",
                member_id="member.override",
                created_at=NOW,
            ),
            private=False,
            reply_to=None,
            attachments=(),
            observed_at=NOW,
        )
    )
    repository.register_actor(pending.binding.actor, MembershipRole.EDITOR)
    ingress = CanonicalKnowledgeIngress(database_path)
    assert ingress.admit_standalone(pending)
    _ = KnowledgeIngestion(repository).ingest(
        pending.binding.actor, pending.event, pending.envelope
    )
    context = TrustedInvocationContext(
        invocation_id="invocation.builtin-override",
        actor=pending.binding.actor,
        run_binding_id=pending.binding.binding_id,
        run_id=pending.binding.run_id,
        capability_epoch=pending.binding.actor.policy_epoch,
        source_fetch_event=pending.event,
        invoked_at=NOW,
    )
    source_ref = EvidenceRef(
        evidence_kind=EvidenceKind.CONVERSATION_EVENT,
        evidence_id=pending.event.message_id,
        revision_id=str(pending.event.revision),
        quote_sha256=sha256(pending.event.text.encode()).hexdigest(),
        scope=pending.event.scope,
        instruction_authority=InstructionAuthority.AUTHORIZED_USER,
        provenance=Provenance.HUMAN_DIRECT,
    )
    return context, source_ref
