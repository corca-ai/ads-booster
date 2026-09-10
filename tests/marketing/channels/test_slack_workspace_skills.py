"""Explicit Slack requests publish procedures without publishing channel history."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import AgentRecordKind, AgentRunState
from ads_booster.knowledge.contracts import ConversationEventKind
from ads_booster.knowledge.errors import KnowledgePolicyError
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.skills import KnowledgeSkills
from tests.knowledge.test_curation_inputs import envelope
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import RecordingReasoning, receive
from tests.marketing.channels.test_slack_learning import (
    ApplyLearningThenStop,
    _prepared_learning_receipt,  # pyright: ignore[reportPrivateUsage]
    _skill_apply_input,  # pyright: ignore[reportPrivateUsage]
)
from tests.marketing.channels.test_slack_learning import (
    installed_events as _installed_events,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_explicit_channel_request_creates_workspace_skill(tmp_path: Path) -> None:
    owner, installed, _ = _installed_events(tmp_path)
    try:
        receive(owner, user="U1", text="<@UBOT> 스킬 만들기: 완료 receipt를 확인하는 공용 절차")
        assert owner.work_once(now=NOW)
        admitted = owner.store.latest_knowledge_event(owner._message_id("C1", "100.001"))  # pyright: ignore[reportPrivateUsage]
        assert admitted is not None
        owner.commands.application.service.reasoning = ApplyLearningThenStop(
            _skill_apply_input(admitted[0], created_by=admitted[1].actor.actor_id)
        )
        while owner.work_once(now=NOW):
            pass
        created_run = owner.commands.application.service.repository.get("team", admitted[1].run_id)
        assert created_run is not None
        assert created_run.state is AgentRunState.COMPLETED
        records = owner.commands.application.service.repository.records("team", admitted[1].run_id)
        assert any(
            item.kind is AgentRecordKind.RECEIPT and item.payload["disposition"] == "succeeded"
            for item in records
        )
        repository = installed.adapter.repository
        catalog = KnowledgeSkills(repository)
        stored = catalog.get(admitted[1].actor, "learned.u1.receipt-rules")
        assert stored is not None

        owner.commands.application.service.reasoning = RecordingReasoning()
        receive(
            owner,
            user="U2",
            channel="C2",
            ts="100.002",
            text="<@UBOT> learned.u1.receipt-rules 스킬을 확인해줘",
        )
        while owner.work_once(now=NOW):
            pass
        other = owner.store.latest_knowledge_event(owner._message_id("C2", "100.002"))  # pyright: ignore[reportPrivateUsage]
        assert other is not None
        reused = catalog.get(other[1].actor, "learned.u1.receipt-rules")
        assert reused is not None
        assert reused.record.digest == stored.record.digest
        assert any(
            item.reference.skill_id == stored.record.skill_id
            for item in catalog.list(other[1].actor)
        )
        with pytest.raises(KnowledgePolicyError):
            _ = repository.resolve_evidence(other[1].actor, stored.record.source_refs[0])
        run_id = other[1].run_id
        selected = _prepared_learning_receipt(owner, run_id)["selected_skill_revisions"]
        assert isinstance(selected, list)
        assert any(
            isinstance(item, dict) and item["skill_id"] == stored.record.skill_id
            for item in selected
        )

        receive(
            owner,
            user="U2",
            channel="C2",
            ts="100.003",
            text="<@UBOT> 스킬 수정: learned.u1.receipt-rules 검증 단계 추가",
        )
        assert owner.work_once(now=NOW)
        update = owner.store.latest_knowledge_event(owner._message_id("C2", "100.003"))  # pyright: ignore[reportPrivateUsage]
        assert update is not None
        owner.commands.application.service.reasoning = ApplyLearningThenStop(
            {
                "schema": "knowledge.tool.skill-apply.v1",
                "operation_id": "operation.requested-update",
                "operations": [
                    {
                        "kind": "update",
                        "skill_id": stored.record.skill_id,
                        "expected_revision_id": stored.record.version,
                        "draft": {
                            "description": "공용 receipt 검증",
                            "procedure": "Read and verify the receipt.",
                        },
                        "reason": "The current user requested a verification step.",
                    }
                ],
            }
        )
        while owner.work_once(now=NOW):
            pass
        updated = catalog.get(admitted[1].actor, stored.record.skill_id)
        assert updated is not None
        assert updated.record.version != stored.record.version
        assert updated.record.procedure == "Read and verify the receipt."
        deleted = update[0].model_copy(
            update={
                "revision": 2,
                "event_kind": ConversationEventKind.MESSAGE_DELETED,
                "edited_at": NOW + timedelta(seconds=1),
                "text": "",
            }
        )
        _ = KnowledgeIngestion(repository).ingest(
            update[1].actor, deleted, envelope(deleted, "delivery.deleted")
        )
        assert catalog.get(admitted[1].actor, stored.record.skill_id) is None
    finally:
        installed.runtime.close()
