from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Literal, assert_never

import pytest

from ads_booster.agent.service.knowledge_ingress import TrustedRunBinding
from ads_booster.agent.service.knowledge_ingress_authority import KnowledgeIngressAuthority
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.knowledge_preparation import (
    PreparedContextBlock,
    PreparedContextRole,
    PreparedContextSlot,
    PreparedKnowledgeContext,
)
from ads_booster.knowledge.batch_actor import load_batch_actor, load_job_actor
from ads_booster.knowledge.contracts import ActorContext, GrantCapability, KnowledgeJob
from ads_booster.knowledge.errors import KnowledgePolicyError
from ads_booster.knowledge.operation_enums import JobKind, JobPriority, JobState
from ads_booster.knowledge.repository import MembershipRole, SqliteKnowledgeRepository
from ads_booster.knowledge.scope_contracts import channel_member_scope
from tests.knowledge.test_channel_batch_actor import channel_actor
from tests.knowledge.test_transfer_material import transfer_adapter
from tests.knowledge.transfer_contract_fixtures import transfer_fixture
from tests.marketing.agent_service.test_http_api import NOW

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.knowledge_selection import ContextReceipt


def preference_actor(member: str, session: str = "thread-one") -> ActorContext:
    actor = channel_actor("C1", session).model_copy(
        update={"actor_id": member, "member_id": member}
    )
    personal = channel_member_scope(actor)
    assert personal is not None
    return actor.model_copy(
        update={
            "grants": tuple(
                grant.model_copy(update={"grant_id": f"{member}-{grant.grant_id}"})
                for grant in actor.grants
            )
            + tuple(
                grant.model_copy(
                    update={"grant_id": f"{member}-personal-{grant.grant_id}", "scope": personal}
                )
                for grant in actor.grants
            ),
        }
    )


def test_bridge_uses_registered_member_for_personal_scope(tmp_path: Path) -> None:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    registered = preference_actor("canonical-alice").model_copy(update={"actor_id": "alice"})
    repository.register_actor(registered, MembershipRole.EDITOR)

    bound = KnowledgeIngressAuthority(repository).bind_actor(preference_actor("alice"), fresh=True)

    assert bound.member_id == "canonical-alice"
    personal = channel_member_scope(bound)
    assert personal is not None
    assert {
        grant.scope.member_id
        for grant in bound.grants
        if grant.scope.channel_id and grant.scope.member_id is not None
    } == {"canonical-alice"}
    assert sum(grant.scope == personal for grant in bound.grants) == 2


def test_personal_job_restores_original_channel_actor_after_restart(tmp_path: Path) -> None:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    initial = preference_actor("alice")
    repository.register_actor(initial, MembershipRole.EDITOR)
    bound = KnowledgeIngressAuthority(repository).bind_actor(initial, fresh=True)
    repository.register_actor(preference_actor("bob"), MembershipRole.EDITOR)
    personal = channel_member_scope(bound)
    assert personal is not None
    job = KnowledgeJob(
        schema="knowledge.job.v1",
        job_id="personal-job",
        workspace_id=bound.workspace_id,
        scope=personal,
        kind=JobKind.MEMORY_CONSOLIDATE,
        state=JobState.QUEUED,
        priority=JobPriority.ROUTINE,
        root_event_id="memory-event",
        policy_version="policy",
        due_at=NOW,
        created_at=NOW,
        submitter_actor=bound,
    )

    restored = load_job_actor(SqliteKnowledgeRepository(tmp_path / "knowledge"), job, initial, NOW)

    assert restored.conversation_scope == bound.conversation_scope
    assert {grant.grant_id for grant in restored.grants} == {
        grant.grant_id for grant in bound.grants
    }


def test_bridge_rejects_another_members_personal_grants(tmp_path: Path) -> None:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    alice = preference_actor("alice")
    repository.register_actor(alice, MembershipRole.EDITOR)
    bob_personal = channel_member_scope(preference_actor("bob"))
    forged = alice.model_copy(
        update={
            "grants": tuple(
                grant.model_copy(update={"scope": bob_personal})
                if grant.scope.member_id is not None
                else grant
                for grant in alice.grants
            ),
        }
    )

    with pytest.raises(KnowledgePolicyError):
        _ = KnowledgeIngressAuthority(repository).bind_actor(forged, fresh=True)


@pytest.mark.parametrize("revoked", [False, True])
def test_context_read_rechecks_personal_grant_admission(
    tmp_path: Path,
    revoked: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = transfer_adapter(tmp_path)
    initial = preference_actor("alice")
    adapter.repository.register_actor(initial, MembershipRole.EDITOR)
    bound = KnowledgeIngressAuthority(adapter.repository).bind_actor(initial, fresh=True)
    personal = channel_member_scope(bound)
    personal_read = next(
        grant
        for grant in bound.grants
        if grant.scope == personal and grant.capability is GrantCapability.READ
    )
    binding = TrustedRunBinding(
        binding_id="binding",
        run_id="run",
        request_id="request",
        source="slack",
        action="create",
        source_version="1",
        actor=bound,
        bound_at=NOW,
    )
    with adapter.ingress.connect() as db:
        _ = db.execute(
            "INSERT INTO knowledge_run_bindings VALUES (?,?,?,?,?)",
            (
                binding.binding_id,
                binding.run_id,
                binding.request_id,
                binding.model_dump_json(),
                contract_sha256(binding),
            ),
        )
    fixture = transfer_fixture()
    prepared = PreparedKnowledgeContext(
        schema="knowledge.prepared-context.v1",
        request=fixture.request,
        receipt=fixture.receipt,
        receipt_sha256=contract_sha256(fixture.receipt),
        blocks=tuple(
            PreparedContextBlock(
                block_id=slot.value,
                slot=slot,
                role=PreparedContextRole.SYSTEM,
                text="context",
            )
            for slot in (
                PreparedContextSlot.ROLE,
                PreparedContextSlot.AUTHORITY,
                PreparedContextSlot.TOOL_CATALOG,
                PreparedContextSlot.STORAGE_GUIDE,
                PreparedContextSlot.REQUEST,
            )
        ),
    )

    def receipt_current(
        _repository: SqliteKnowledgeRepository,
        actor: ActorContext,
        _receipt: ContextReceipt,
    ) -> bool:
        return personal_read in actor.grants

    monkeypatch.setattr(
        "ads_booster.agent.service.knowledge.context_receipt_is_current", receipt_current
    )
    if revoked:
        with adapter.repository.connection() as db:
            _ = db.execute(
                "UPDATE channel_grant_admissions SET state='revoked' WHERE grant_id=?",
                (personal_read.grant_id,),
            )
    assert adapter.is_current("run", prepared) is not revoked


@pytest.mark.parametrize("revocation", ["delete", "expire", "revoke"])
def test_personal_grant_revocation_blocks_replay_and_background_restart(
    tmp_path: Path,
    revocation: Literal["delete", "expire", "revoke"],
) -> None:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    initial = preference_actor("alice")
    repository.register_actor(initial, MembershipRole.EDITOR)
    bound = KnowledgeIngressAuthority(repository).bind_actor(initial, fresh=True)
    personal = channel_member_scope(bound)
    grant = next(
        grant
        for grant in bound.grants
        if grant.scope == personal and grant.capability is GrantCapability.WRITE
    )
    with repository.connection() as db:
        match revocation:
            case "delete":
                _ = db.execute("DELETE FROM scope_grants WHERE grant_id=?", (grant.grant_id,))
            case "expire":
                expires_at = NOW - timedelta(seconds=1)
                expired = grant.model_copy(update={"expires_at": expires_at})
                _ = db.execute(
                    "UPDATE scope_grants SET grant_json=?,expires_at=? WHERE grant_id=?",
                    (expired.model_dump_json(), expires_at.isoformat(), grant.grant_id),
                )
            case "revoke":
                _ = db.execute(
                    "UPDATE channel_grant_admissions SET state='revoked' WHERE grant_id=?",
                    (grant.grant_id,),
                )
            case _:
                assert_never(revocation)

    restarted = SqliteKnowledgeRepository(tmp_path / "knowledge")
    with pytest.raises(KnowledgePolicyError):
        _ = KnowledgeIngressAuthority(restarted).bind_actor(bound)
    with pytest.raises(KnowledgePolicyError):
        _ = load_batch_actor(restarted, bound.conversation_scope, NOW, submitter=bound)
