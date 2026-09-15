from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ads_booster.channels.contracts import ChannelIdentityBinding
from ads_booster.channels.slack_conversations import (
    Conversation,
    Message,
    MessagePlan,
    SlackConversationStore,
)
from ads_booster.channels.slack_integration_actors import SlackIntegrationActors

if TYPE_CHECKING:
    from pathlib import Path


def test_threads_actor_accepts_canonical_slack_conversation_id(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "agent.sqlite3"
    store = SlackConversationStore(database)
    conversation = Conversation(
        conversation_id="slack-conversation-" + "a" * 64,
        tenant_id="team",
        channel_id="C1",
        thread_ts="1.1",
        owner_id="member-1",
        private=False,
    )
    message = Message(
        message_id="slack-message-" + "b" * 64,
        conversation_id=conversation.conversation_id,
        user_id="U1",
        text="내 Threads 계정 연결해줘",
    )
    store.admit(conversation, message)
    store.save_plan(message, MessagePlan(action="create", run_id="run-1"))
    identity = ChannelIdentityBinding(
        schema_version="trace.channel-identity-binding.v1",
        binding_id="binding-1",
        installation_id="installation-1",
        external_user_id="U1",
        tenant_id="team",
        member_id="member-1",
        created_at=datetime(2026, 9, 15, tzinfo=UTC),
    )
    actors = SlackIntegrationActors(str(database), lambda _user_id: identity)

    # When
    actor = actors.threads_actor("team", "run-1")

    # Then
    assert actor.conversation_id == conversation.conversation_id
