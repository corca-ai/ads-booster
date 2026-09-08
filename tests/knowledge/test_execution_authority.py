"""Current execution authority against real persisted identity and grant changes."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from ads_booster.knowledge.repository import MembershipRole, SqliteKnowledgeRepository
from ads_booster.agent.service.knowledge import KnowledgeServiceAdapter
from ads_booster.agent.service.knowledge_ingress_authority import (
    KnowledgeIngressAuthority,
)
from tests.knowledge.change_test_fixtures import NOW, actor

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.knowledge_preparation import PreparedKnowledgeContext
    from ads_booster.knowledge.context_selection import KnowledgeContextAssembler
    from ads_booster.knowledge.tools import ToolHost
    from ads_booster.agent.service.knowledge_ingress import CanonicalKnowledgeIngress


@pytest.mark.parametrize(
    "change", ["fence", "member", "expired", "deleted_grant", "unchanged", "rebound"]
)
def test_execution_checks_pending_correction_and_current_grant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    original = actor()
    repository.register_actor(original, MembershipRole.EDITOR)

    def binding_for_run(_run: str) -> SimpleNamespace:
        return SimpleNamespace(actor=original)

    def pending_fence_for_run(_run: str) -> bool:
        return change == "fence"

    def current_receipt(*_args: object) -> bool:
        return True

    if change == "rebound":
        original = KnowledgeIngressAuthority(repository).bind_actor(original)

    ingress = SimpleNamespace(
        binding_for_run=binding_for_run,
        pending_fence_for_run=pending_fence_for_run,
    )
    owner = KnowledgeServiceAdapter(
        cast("CanonicalKnowledgeIngress", cast("object", ingress)),
        repository,
        cast("ToolHost", object()),
        cast("KnowledgeContextAssembler", object()),
    )
    prepared = cast("PreparedKnowledgeContext", cast("object", SimpleNamespace(receipt=object())))
    monkeypatch.setattr(
        "ads_booster.agent.service.knowledge.context_receipt_is_current",
        current_receipt,
    )
    with repository.connection() as db:
        if change == "member":
            _ = db.execute("UPDATE memberships SET state='disabled'")
        elif change == "expired":
            for grant in original.grants:
                expired = grant.model_copy(update={"expires_at": NOW + timedelta(seconds=1)})
                _ = db.execute(
                    "UPDATE scope_grants SET grant_json=? WHERE grant_id=?",
                    (expired.model_dump_json(), grant.grant_id),
                )
        elif change == "deleted_grant":
            _ = db.execute("DELETE FROM scope_grants")
    assert owner.is_current("run", prepared) is (change in {"unchanged", "rebound"})
