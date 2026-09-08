from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.knowledge_context import EditorialContextRole
from ads_booster.contracts.knowledge_preparation import (
    PreparedContextBlock,
    PreparedContextRole,
    PreparedContextSlot,
    PreparedKnowledgeContext,
)
from ads_booster.contracts.knowledge_selection import SelectedConstraint, VoiceStatus
from ads_booster.knowledge.context_selection import KnowledgeContextAssembler
from ads_booster.knowledge.repository import SqliteKnowledgeRepository
from ads_booster.knowledge.retrieval import KnowledgeRetriever
from ads_booster.knowledge.tools import ToolHost
from ads_booster.marketing.agent_service.knowledge import KnowledgeServiceAdapter
from ads_booster.marketing.agent_service.knowledge_ingress import CanonicalKnowledgeIngress
from tests.knowledge.transfer_contract_fixtures import transfer_fixture


def transfer_adapter(root: Path) -> KnowledgeServiceAdapter:
    repository = SqliteKnowledgeRepository(root / "knowledge")
    return KnowledgeServiceAdapter(
        CanonicalKnowledgeIngress(root / "agent.db"),
        repository,
        ToolHost(repository),
        KnowledgeContextAssembler(repository, KnowledgeRetriever(repository)),
    )


def _prepared(*, configured: bool) -> PreparedKnowledgeContext:
    transfer = transfer_fixture()
    receipt = transfer.receipt.model_copy(
        update={
            "required_constraints": (
                SelectedConstraint(
                    constraint_id="constraint.team",
                    authority_ref="event.team",
                    revision_id="team.r1",
                ),
            ),
            "voice_status": VoiceStatus.CONFIGURED
            if configured
            else VoiceStatus.VOICE_UNCONFIGURED,
            "soul_revision_id": transfer.receipt.soul_revision_id if configured else None,
        }
    )
    internal = tuple(
        PreparedContextBlock(
            block_id=f"required.{slot.value}",
            slot=slot,
            role=PreparedContextRole.SYSTEM,
            text=f"Internal {slot.value}",
        )
        for slot in (
            PreparedContextSlot.ROLE,
            PreparedContextSlot.AUTHORITY,
            PreparedContextSlot.TOOL_CATALOG,
            PreparedContextSlot.STORAGE_GUIDE,
            PreparedContextSlot.REQUEST,
        )
    )
    constraint = PreparedContextBlock(
        block_id="constraint.team",
        slot=PreparedContextSlot.CONSTRAINT,
        role=PreparedContextRole.SYSTEM,
        text="No price claims.",
        revision_refs=("team.r1",),
    )
    voice = (
        (
            PreparedContextBlock(
                block_id="brand.voice",
                slot=PreparedContextSlot.BRAND_VOICE,
                role=PreparedContextRole.EDITORIAL,
                text="Calm brand tone.",
                revision_refs=("memory.soul.rev3",),
            ),
        )
        if configured
        else ()
    )
    return PreparedKnowledgeContext(
        schema="knowledge.prepared-context.v1",
        request=transfer.request,
        receipt=receipt,
        receipt_sha256=contract_sha256(receipt),
        blocks=(*internal, constraint, *voice),
    )


@pytest.mark.parametrize("configured", [True, False])
def test_transfer_preserves_selected_team_constraints(tmp_path: Path, configured: bool) -> None:
    # Given: selected mandatory TEAM constraint, with either configured or empty SOUL.
    prepared = _prepared(configured=configured)
    # When: the service projects selected context into editorial transfer material.
    material = transfer_adapter(tmp_path).transfer_material(prepared)
    # Then: the constraint retains authority and revision, without system internals.
    constraint = next(
        block for block in material.editorial_context if block.block_id == "constraint.team"
    )
    assert constraint.role is EditorialContextRole.CONSTRAINT
    assert constraint.revision_refs == ("team.r1",)
    assert {block.block_id for block in material.editorial_context} == (
        {"constraint.team", "brand.voice"} if configured else {"constraint.team"}
    )
    assert material.receipt.voice_status is prepared.receipt.voice_status


@pytest.mark.parametrize(
    "mutation", ["unselected", "wrong_revision", "missing_revision", "missing", "wrong_role"]
)
def test_transfer_rejects_invalid_mandatory_constraint(tmp_path: Path, mutation: str) -> None:
    # Given: a block that no longer agrees with the selected mandatory constraint receipt.
    prepared = _prepared(configured=False)
    constraint = prepared.blocks[-1]
    changes = {
        "unselected": {"block_id": "constraint.forged"},
        "wrong_revision": {"revision_refs": ("unselected.r9",)},
        "missing_revision": {"revision_refs": ()},
        "missing": {},
        "wrong_role": {"role": PreparedContextRole.EDITORIAL},
    }
    blocks = prepared.blocks[:-1]
    if mutation != "missing":
        blocks = (*blocks, constraint.model_copy(update=changes[mutation]))
    invalid = prepared.model_copy(update={"blocks": blocks})
    # When / Then: materialization fails before a worker can receive incomplete authority.
    with pytest.raises(ValueError, match="knowledge_transfer_constraint_invalid"):
        _ = transfer_adapter(tmp_path).transfer_material(invalid)


def test_transfer_excludes_tool_catalog_even_with_editorial_role(tmp_path: Path) -> None:
    # Given: an internal tool catalog carries a misleading editorial role and selected revision.
    prepared = _prepared(configured=True)
    blocks = tuple(
        block.model_copy(
            update={
                "role": PreparedContextRole.EDITORIAL,
                "revision_refs": ("team.r1",),
            }
        )
        if block.slot is PreparedContextSlot.TOOL_CATALOG
        else block
        for block in prepared.blocks
    )
    # When: projecting the required internal catalog alongside selected context.
    material = transfer_adapter(tmp_path).transfer_material(
        prepared.model_copy(update={"blocks": blocks})
    )
    # Then: only the selected constraint and brand voice are portable.
    assert {block.block_id for block in material.editorial_context} == {
        "constraint.team",
        "brand.voice",
    }


def test_transfer_rejects_changed_receipt_digest(tmp_path: Path) -> None:
    # Given: the context receipt was changed without its stored digest.
    prepared = _prepared(configured=False).model_copy(update={"receipt_sha256": "f" * 64})
    # When / Then: transfer creation rejects the inconsistent provenance.
    with pytest.raises(ValueError, match="knowledge_transfer_receipt_invalid"):
        _ = transfer_adapter(tmp_path).transfer_material(prepared)
