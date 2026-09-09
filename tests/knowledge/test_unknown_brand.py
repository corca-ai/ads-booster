from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from ads_booster.agent.service.knowledge import KnowledgeServiceAdapter
from ads_booster.agent.service.knowledge_ingress import CanonicalKnowledgeIngress, TrustedRunBinding
from ads_booster.agent.service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentGoal,
    AgentRun,
    AgentRunState,
    CapabilitySnapshot,
)
from ads_booster.contracts.knowledge_preparation import (
    PreparedKnowledgeContext,
    RequiredContextPreparationError,
)
from ads_booster.contracts.knowledge_selection import KnowledgeActionKind
from ads_booster.knowledge.context_selection import KnowledgeContextAssembler
from ads_booster.knowledge.contracts import (
    BrandState,
    ConversationEvent,
    ConversationEventKind,
    ConversationRole,
    IngestEnvelope,
    MessageEventRef,
    TaskBinding,
    TaskBindingState,
)
from ads_booster.knowledge.repository import (
    MembershipRole,
    SqliteKnowledgeRepository,
)
from ads_booster.knowledge.repository_context import active_task_binding
from ads_booster.knowledge.retrieval import KnowledgeRetriever
from ads_booster.knowledge.tools import ToolHost
from tests.knowledge.brand_test_fixtures import brand_registration
from tests.knowledge.change_test_fixtures import NOW, actor

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path


@pytest.mark.parametrize(
    "brand_id", ["brand.missing", "brand.foreign", "brand.retired", "brand.active"]
)
def test_prepare_checks_selected_brand_before_changing_current_task(
    tmp_path: Path, brand_id: str
) -> None:
    # Given: real canonical run identity and an existing active task.
    repo = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = actor()
    foreign_scope = editor.conversation_scope.model_copy(
        update={"workspace_id": "workspace.foreign"}
    )
    foreign = editor.model_copy(
        update={
            "workspace_id": "workspace.foreign",
            "conversation_scope": foreign_scope,
            "grants": tuple(
                grant.model_copy(
                    update={"workspace_id": "workspace.foreign", "scope": foreign_scope}
                )
                for grant in editor.grants
            ),
        }
    )
    repo.register_actor(editor, MembershipRole.ADMIN)
    repo.register_actor(foreign, MembershipRole.ADMIN)
    _ = repo.register_brand(brand_registration(repo, editor, "brand.active", "active"))
    _ = repo.register_brand(brand_registration(repo, foreign, "brand.foreign", "foreign"))
    retired = brand_registration(repo, editor, "brand.retired", "retired")
    _ = repo.register_brand(
        replace(retired, brand=retired.brand.model_copy(update={"state": BrandState.RETIRED}))
    )
    previous = TaskBinding(
        task_id="task.previous",
        workspace_id=editor.workspace_id,
        actor_ref=editor.actor_id,
        member_id=editor.member_id,
        session_id=editor.session_id,
        action_kind=KnowledgeActionKind.TEAM_CHAT,
        capability_epoch=editor.policy_epoch,
        state=TaskBindingState.ACTIVE,
        opened_at=NOW,
    )
    host = ToolHost(repo)
    _ = host.open_task(editor, previous)
    database = tmp_path / "service.sqlite"
    run_repo = SqliteAgentRunRepository(database)
    ingress = CanonicalKnowledgeIngress(database)
    run = AgentRun(
        schema_version="trace.agent-run.v1",
        run_id="run.brand",
        tenant_id=editor.workspace_id,
        goal=AgentGoal(
            objective="Write brand content", success_criteria=("Use the selected brand",)
        ),
        budget=AgentBudget(max_tool_calls=1, max_cost_units=1),
        state=AgentRunState.CREATED,
        created_at=NOW,
        updated_at=NOW,
    )
    binding = TrustedRunBinding(
        binding_id="binding.brand",
        run_id=run.run_id,
        request_id="request.brand",
        source="api",
        action="create",
        source_version="1",
        actor=editor,
        bound_at=NOW,
    )
    event = ConversationEvent(
        conversation_id="conversation.brand",
        message_id="message.brand",
        revision=1,
        sequence=1,
        role=ConversationRole.USER,
        speaker_ref=editor.actor_id,
        created_at=NOW,
        text=run.goal.objective,
        event_kind=ConversationEventKind.MESSAGE_FINALIZED,
        scope=editor.conversation_scope,
    )
    envelope = IngestEnvelope(
        schema="knowledge.ingest-envelope.v1",
        delivery_id="delivery.brand",
        event_kind=event.event_kind,
        request_text=event.text,
        message_event=MessageEventRef(
            conversation_ref=event.conversation_id, message_ref=event.message_id, revision=1
        ),
        timestamp=NOW,
    )

    def admit(connection: sqlite3.Connection) -> None:
        _ = ingress.admit(connection, binding, event, envelope)

    _ = run_repo.create(run, admission=admit)
    adapter = KnowledgeServiceAdapter(
        ingress, repo, host, KnowledgeContextAssembler(repo, KnowledgeRetriever(repo))
    )
    capabilities = CapabilitySnapshot(
        schema_version="trace.capability-snapshot.v1",
        snapshot_id="cap.brand",
        run_id=run.run_id,
        descriptors=(),
        created_at=NOW,
    )

    # When: the trusted model intent proposes a selected brand for content.
    result = adapter.prepare(
        run, capabilities, now=NOW, action_kind=KnowledgeActionKind.CONTENT_WRITE, brand_id=brand_id
    )

    # Then: unresolved brands preserve prior task authority, and an active local brand prepares.
    if brand_id == "brand.active":
        assert isinstance(result, PreparedKnowledgeContext)
        assert result.receipt.resolved_brand_ref == brand_id
    else:
        assert isinstance(result, RequiredContextPreparationError)
        assert result.brand_ref == brand_id
        assert active_task_binding(repo, editor) == previous
