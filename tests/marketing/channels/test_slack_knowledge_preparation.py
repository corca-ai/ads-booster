"""Signed requests with the installed knowledge runtime, including an empty brand store."""

from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, override

import pytest

from ads_booster.bootstrap.lifecycle import build_installed_knowledge_runtime
from ads_booster.channels.slack_events import SlackEvents
from ads_booster.contracts.agent_run import AgentRunState
from ads_booster.contracts.knowledge_preparation import (
    RequiredContextErrorCode,
    RequiredContextPreparationError,
)
from ads_booster.contracts.knowledge_selection import KnowledgeActionKind, VoiceStatus
from ads_booster.knowledge.configuration import (
    KnowledgeSettings,
    initialize_knowledge_store,
    initialize_local_configuration,
)
from ads_booster.knowledge.context_selection import KnowledgeContextAssembler
from ads_booster.providers.codex_cli import CodexCli
from tests.marketing.agent_service.test_application import (
    _reasoning_result,  # pyright: ignore[reportPrivateUsage]
)
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import RecordingReasoning, receive
from tests.marketing.channels.test_slack_images import ImageReasoning, configured

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.contracts.knowledge_preparation import (
        BrandUnresolvedPreparation,
        PreparedContextBlock,
    )
    from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult
    from ads_booster.knowledge.contracts import ActorContext, TaskBinding


class ContentImageReasoning(ImageReasoning):
    def __init__(self) -> None:
        super().__init__(authorize=True)
        self.requests: list[ReasoningRequest] = []

    @override
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        self.requests.append(request)
        decision = super().plan(request).decision
        assert request.prepared_context is not None
        if request.prepared_context.request.action_kind is not KnowledgeActionKind.CONTENT_WRITE:
            decision = decision.model_copy(
                update={"proposed_action_kind": KnowledgeActionKind.CONTENT_WRITE}
            )
        return _reasoning_result(request, decision)


def test_first_image_request_with_empty_brand_store_executes_without_blank_reply(
    tmp_path: Path,
) -> None:
    original, messages, uploads, calls = configured(tmp_path)
    service = original.commands.application.service
    settings = KnowledgeSettings(
        root=tmp_path / "knowledge",
        control_root=tmp_path / "control",
        policy_path=tmp_path / "control/policy.json",
    )
    _ = initialize_local_configuration(*settings.require_enabled(), workspace_id="team")
    _ = initialize_knowledge_store(settings)
    installed = build_installed_knowledge_runtime(
        settings=settings,
        service_database=service.repository.database_path,
        codex=CodexCli(executable=Path("/unused/codex"), model="test"),
        model_id="test",
    )
    with closing(installed.runtime):
        service.knowledge = installed.adapter
        reasoning = ContentImageReasoning()
        service.reasoning = reasoning
        owner = SlackEvents(
            original.commands,
            "UBOT",
            frozenset({"C1"}),
            workspace_mentions=True,
            image_delivery=original.image_delivery,
        )
        receive(owner, user="UNEW", text="<@UBOT> 이미지 만들어줘")
        for _ in range(8):
            if not owner.work_once(now=NOW):
                break
        assert len(calls) == 1
        assert len(uploads) == 3
        assert service.repository.list_runs("team")[0].state is AgentRunState.COMPLETED
        assert all(str(message["text"]).strip() for message in messages)
        assert "다운로드" in str(messages[-1]["text"])
        assert any(
            request.prepared_context is not None
            and request.prepared_context.receipt.voice_status is VoiceStatus.VOICE_UNCONFIGURED
            for request in reasoning.requests
        )


@pytest.mark.parametrize("followup", ["지금 어떤 상황이야?", "이 문제에 대한 이슈 올려줘"])
def test_brand_wait_has_current_reply_and_new_question_reaches_reasoning(
    tmp_path: Path, followup: str
) -> None:

    original, messages, _, _ = configured(tmp_path)
    service = original.commands.application.service
    settings = KnowledgeSettings(
        root=tmp_path / "knowledge",
        control_root=tmp_path / "control",
        policy_path=tmp_path / "control/policy.json",
    )
    _ = initialize_local_configuration(*settings.require_enabled(), workspace_id="team")
    _ = initialize_knowledge_store(settings)
    installed = build_installed_knowledge_runtime(
        settings=settings,
        service_database=service.repository.database_path,
        codex=CodexCli(executable=Path("/unused/codex"), model="test"),
        model_id="test",
    )

    class MissingVoice(KnowledgeContextAssembler):
        @override
        def _voice_blocks(
            self, actor: ActorContext, task: TaskBinding, now: datetime
        ) -> (
            tuple[VoiceStatus, str | None, tuple[str, ...], tuple[PreparedContextBlock, ...]]
            | RequiredContextPreparationError
            | BrandUnresolvedPreparation
        ):
            if task.action_kind is KnowledgeActionKind.CONTENT_WRITE:
                return RequiredContextPreparationError(
                    schema="knowledge.preparation.v1",
                    status="required_context_error",
                    task_ref=task.task_id,
                    action_kind=task.action_kind,
                    brand_ref=task.brand_id,
                    error_code=RequiredContextErrorCode.REQUIRED_VOICE_UNAVAILABLE,
                )
            return super()._voice_blocks(actor, task, now)

    with closing(installed.runtime):
        service.knowledge = replace(
            installed.adapter,
            assembler=MissingVoice(
                installed.adapter.repository, installed.adapter.assembler.retriever
            ),
        )
        owner = SlackEvents(original.commands, "UBOT", frozenset({"C1"}), workspace_mentions=True)
        service.reasoning = ContentImageReasoning()
        receive(owner, text="<@UBOT> 이미지 만들어줘")
        for _ in range(8):
            if not owner.work_once(now=NOW):
                break
        run = service.repository.list_runs("team")[0]
        assert run.state is AgentRunState.AWAITING_INPUT
        assert "브랜드의 표현 기준" in str(messages[-1]["text"])
        assert "브랜드의 표현 기준" in owner.commands.summary("team", run.run_id)
        assert all(str(message["text"]).strip() for message in messages)
        answering = RecordingReasoning()
        service.reasoning = answering
        receive(owner, type="message", text=followup, ts="100.002", thread_ts="100.001")
        for _ in range(8):
            if not owner.work_once(now=NOW):
                break
        assert len(answering.requests) == 1
        assert followup in answering.requests[0].model_dump_json()
        finished = service.repository.get("team", run.run_id)
        assert finished is not None
        assert finished.state is AgentRunState.COMPLETED
