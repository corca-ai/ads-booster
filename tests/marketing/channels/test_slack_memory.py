from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ads_booster.marketing.channels.contracts import ChannelIdentityBinding
from ads_booster.marketing.channels.slack_conversations import Conversation, Message
from ads_booster.marketing.channels.slack_memory import memory_command

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 9, 7, tzinfo=UTC)


def test_shared_learning_requires_explicit_scope_and_review(tmp_path: Path) -> None:
    conversation = Conversation(
        conversation_id="thread",
        tenant_id="team",
        channel_id="C1",
        thread_ts="1",
        owner_id="member",
        private=False,
        current_run="run-a",
    )
    identity = ChannelIdentityBinding(
        schema_version="trace.channel-identity-binding.v1",
        binding_id="binding",
        installation_id="installation",
        external_user_id="U1",
        tenant_id="team",
        member_id="member",
        can_approve=True,
        created_at=NOW,
    )

    def send(text: str, event: str, *, private: bool = False, run: str = "run-a") -> str:
        return memory_command(
            tmp_path / "memory.sqlite",
            conversation.model_copy(update={"private": private, "current_run": run}),
            Message(message_id=event, conversation_id="thread", user_id="U1", text=text),
            identity,
            now=NOW,
        )

    assert "먼저 이 스레드" in send("기억 제안 이 배경", "before-run", run="")
    local = send("기억 제안 이 배경에는 둥근 폰트", "local")
    assert "기억 검토" in local
    assert "기억이 없습니다" in send("기억 목록", "other", run="run-b")
    shared = send("기억 공용 제안 팀 합성 일정 데이터만 사용", "shared")
    review_command = shared.splitlines()[-1]
    assert review_command.startswith("기억 공용 검토")
    reviewed = send(review_command, "review")
    assert '"work_id":""' in reviewed
    approved = send(reviewed.splitlines()[-1], "approve")
    assert "approved" in approved
    assert "팀 합성" in send("기억 목록", "next-work", run="run-b")
    assert "만들거나 변경할 수 없습니다" in send(
        "기억 공용 제안 개인 일정", "private", private=True
    )
