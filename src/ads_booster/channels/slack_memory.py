"""Explicit human memory review through authenticated Slack membership."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from ads_booster.contracts.agent_memory import MemoryAccess, MemoryNote, MemoryScope
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.learning.memory import SQLiteMemoryStore

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from ads_booster.channels.contracts import ChannelIdentityBinding
    from ads_booster.channels.slack_conversations import Conversation, Message


def memory_command(  # noqa: C901,PLR0911 - explicit scoped reviewer command responses.
    database: Path,
    conversation: Conversation,
    message: Message,
    identity: ChannelIdentityBinding,
    *,
    now: datetime,
) -> str:
    store = SQLiteMemoryStore(database)
    _, _, remainder = message.text.partition(" ")
    shared = remainder.startswith("공용 ")
    if shared:
        if conversation.private:
            return "개인 대화에서 공용 기억을 만들거나 변경할 수 없습니다. 팀 채널에서 제안하세요."
        remainder = remainder.removeprefix("공용 ")
    prefix = "기억 공용" if shared else "기억"
    access = MemoryAccess(
        scope=MemoryScope(
            workspace_id=identity.tenant_id,
            channel_id=conversation.channel_id,
            product_id="trace",
            work_id="" if shared else conversation.current_run or conversation.conversation_id,
            member_id=identity.member_id if conversation.private else "",
            session_id=conversation.conversation_id if conversation.private else "",
        ),
        actor_id=identity.member_id,
        private=conversation.private,
        can_review=identity.can_approve,
    )
    action, _, argument = remainder.partition(" ")
    if action == "제안" and argument.strip():
        if not shared and not conversation.current_run:
            return "먼저 이 스레드에서 맡길 작업을 알려주세요. 기억을 그 업무에 연결하겠습니다."
        note_id = "slack-memory-" + contract_sha256({"message": message.message_id})[:32]
        existing = store.get(note_id, access)
        if existing is None:
            store.put(
                MemoryNote(
                    note_id=note_id,
                    scope=access.scope,
                    category="observation",
                    domain="work",
                    text=argument,
                    source_ref=f"slack:{conversation.channel_id}:{message.message_id}",
                    source_sha256=contract_sha256({"message": message.text}),
                    author_id=identity.member_id,
                    created_at=now,
                    expires_at=now + timedelta(days=30),
                ),
                access,
                now=now,
            )
        return f"기억 후보로 기록했습니다. 적용 전 검토가 필요합니다.\n{prefix} 검토 {note_id}"
    if action == "목록":
        return (
            "\n".join(
                f"{n.note_id} ({n.stage}) {n.text}" for n in store.list_notes(access, limit=6)
            )
            or "기억이 없습니다."
        )
    note_id, _, digest = argument.partition(" ")
    note = store.get(note_id, access)
    if note is None:
        return "기억 제안 내용 / 기억 목록 / 기억 검토 ID / 기억 채택 ID 해시 / 기억 폐기 ID 해시"
    if action == "검토":
        if note.stage == "candidate" and access.can_review:
            note = store.review(
                note_id, access, expected_sha256=contract_sha256(note), stage="review", now=now
            )
        return (
            f"{note.text}\n출처: {note.source_ref}\n범위: {note.scope.model_dump_json()}\n"
            f"만료: {note.expires_at.isoformat()}\n상태: {note.stage}\n"
            f"{prefix} 채택 {note_id} {contract_sha256(note)}"
        )
    if action in {"채택", "폐기"}:
        note = store.review(
            note_id,
            access,
            expected_sha256=digest,
            stage="approved" if action == "채택" else "rejected",
            now=now,
        )
        return f"기억 상태: {note.stage}. 실행·제작·게시 승인을 부여한 것은 아닙니다."
    return "기억 목록 또는 기억 검토 ID로 내용을 확인하세요."
