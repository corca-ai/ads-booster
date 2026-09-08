"""Scoped human-reported marketing outcomes without external collection or publication."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from pydantic import TypeAdapter, ValidationError

from ads_booster.contracts.agent_memory import MemoryAccess, MemoryScope
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.performance_observation import PerformanceObservation
from ads_booster.learning.performance_observations import PerformanceObservationStore
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.agent.service.application import MarketingAgentService
    from ads_booster.channels.contracts import ChannelIdentityBinding
    from ads_booster.channels.slack_conversations import Conversation, Message

_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_FIELDS = frozenset(
    {
        "channel",
        "account_id",
        "country",
        "publication_ref",
        "window_start",
        "window_end",
        "views",
        "likes",
        "comments",
        "clicks",
        "installs",
        "note",
    }
)
_MAX_INPUT = 6000
_MAX_ROWS = 6
_COMPARE_COUNT = 2
_LEARNING_FIELDS = 3
_MISMATCH_LABELS = {
    "channel_mismatch": "채널",
    "account_id_mismatch": "계정",
    "country_mismatch": "국가",
    "window_start_mismatch": "관측 시작",
    "window_end_mismatch": "관측 종료",
}
_HELP = (
    "성과 기록 {JSON} / 성과 정정 ID {JSON}\n"
    "필수: channel, account_id, country, publication_ref,\n"
    "window_start, window_end, views, likes, comments.\n"
    "시각은 UTC 시간대를 포함하세요. clicks·installs는 확인한 경우만 입력하세요.\n"
    '예: 성과 기록 {"channel":"threads","account_id":"trace-jp","country":"JP",'
    '"publication_ref":"게시물 주소 또는 ID","window_start":"2026-09-06T00:00:00Z",'
    '"window_end":"2026-09-07T00:00:00Z","views":100,"likes":10,"comments":2}\n'
    "성과 목록 / 성과 비교 ID ID\n성과 학습 ID[,ID] 관찰 | 반례 | 적용범위\n"
    "사람 보고이며 자동 조회·검증·게시하지 않습니다. 관찰은 인과 효과의 증명이 아닙니다."
)


def is_performance_command(text: str) -> bool:
    prefix, _, remainder = text.partition(" ")
    action, _, _ = remainder.partition(" ")
    return prefix == "성과" and action in {"기록", "정정", "목록", "비교", "학습", "도움말"}


def performance_command(
    service: MarketingAgentService,
    conversation: Conversation,
    message: Message,
    identity: ChannelIdentityBinding,
    *,
    now: datetime,
) -> str:
    if (
        message.conversation_id != conversation.conversation_id
        or message.user_id != identity.external_user_id
        or (not conversation.private and conversation.tenant_id != identity.tenant_id)
        or (conversation.private and conversation.owner_id != identity.member_id)
    ):
        return "이 대화의 성과 기록 범위를 확인할 수 없습니다."
    _, _, remainder = message.text.partition(" ")
    action, _, argument = remainder.partition(" ")
    if action == "도움말":
        return _HELP
    run = service.repository.get(conversation.tenant_id, conversation.current_run)
    if run is None:
        return "먼저 이 스레드에서 맡길 작업을 알려주세요. 성과를 같은 업무에 연결하겠습니다."
    access = MemoryAccess(
        scope=MemoryScope(
            workspace_id=identity.tenant_id,
            product_id="trace",
            work_id=run.run_id,
            member_id=identity.member_id if conversation.private else "",
            session_id=conversation.conversation_id if conversation.private else "",
        ),
        actor_id=identity.member_id,
        private=conversation.private,
        can_review=identity.can_approve,
    )
    store = PerformanceObservationStore(service.repository.database_path)
    try:
        return _action(store, access, message, action, argument, now)
    except ValidationError:
        return _HELP
    except ValueError:
        return "성과 범위·정정 권한·관측 기간·원본을 확인해주세요. 필수 항목: 성과 도움말"


def _action(  # noqa: PLR0913,PLR0911,PLR0917 - bounded explicit command dispatch.
    store: PerformanceObservationStore,
    access: MemoryAccess,
    message: Message,
    action: str,
    argument: str,
    now: datetime,
) -> str:
    if len(argument) > _MAX_INPUT:
        return _HELP
    if action == "목록":
        observations = store.list(access, current_only=True, limit=_MAX_ROWS)
        lines = "\n".join(_row(item) for item in observations)
        return (
            f"사람 보고(최근 최대 {_MAX_ROWS}개):\n{lines or '기록 없음'}\n미보고는 0이 아닙니다."
        )
    if action == "비교":
        ids = tuple(argument.split())
        if len(ids) != _COMPARE_COUNT or len(set(ids)) != _COMPARE_COUNT:
            return _HELP
        comparison = store.compare(ids, access)
        items = comparison.observations
        caution = (
            "조건이 다릅니다: "
            + ", ".join(
                _MISMATCH_LABELS.get(reason, "비교 조건") for reason in comparison.mismatch_reasons
            )
            if not comparison.comparable
            else "채널·계정·국가·관측 기간이 같습니다."
        )
        return "\n".join(
            (
                "사람 보고 비교",
                *(_row(item) for item in items),
                caution,
                "관측 차이는 인과 효과의 증명이 아닙니다. 미보고 수치는 비교하지 않습니다.",
            )
        )
    if action == "학습":
        raw_ids, _, details = argument.partition(" ")
        ids = tuple(raw_ids.split(","))
        parts = tuple(item.strip() for item in details.split("|"))
        if not 1 <= len(ids) <= _COMPARE_COUNT or len(parts) != _LEARNING_FIELDS or not all(parts):
            return _HELP
        note = store.learning_candidate(
            access,
            note_id="performance-learning-" + contract_sha256({"message": message.message_id})[:32],
            observation_ids=ids,
            observation=parts[0],
            counterexample=parts[1],
            applicability=parts[2],
            now=now,
            expires_at=now + timedelta(days=30),
        )
        return f"사람 보고 기반 학습 후보입니다. 아직 적용하지 않습니다.\n기억 검토 {note.note_id}"
    supersedes = None
    if action == "정정":
        supersedes, _, argument = argument.partition(" ")
    elif action != "기록":
        return _HELP
    payload = _JSON.validate_json(argument)
    if set(payload) - _FIELDS:
        return _HELP
    observation_id = (
        "performance-observation-" + contract_sha256({"message": message.message_id})[:32]
    )
    existing = store.get(observation_id, access)
    stamp = existing.recorded_at if existing is not None else now
    observation = PerformanceObservation.model_validate(
        {
            **payload,
            "observation_id": observation_id,
            "scope": access.scope.model_dump(mode="json"),
            "author_id": access.actor_id,
            "source_ref": f"slack:{message.conversation_id}:{message.message_id}",
            "source_sha256": contract_sha256({"message": message.text}),
            "recorded_at": stamp,
            "supersedes": supersedes,
        }
    )
    _ = store.record(observation, access)
    return f"사람 보고 성과: {observation_id}\n성과 목록으로 확인하세요. 외부 검증 수치가 아닙니다."


def _row(item: PerformanceObservation) -> str:
    metrics = ", ".join(
        f"{label} {value if value is not None else '미보고'}"
        for label, value in (
            ("조회", item.views),
            ("좋아요", item.likes),
            ("댓글", item.comments),
            ("클릭", item.clicks),
            ("설치", item.installs),
        )
    )
    return (
        f"{item.observation_id}\n{item.channel} / {item.account_id} / {item.country}\n"
        f"게시물: {item.publication_ref[:160]}\n"
        f"{item.window_start.isoformat()} ~ {item.window_end.isoformat()}\n{metrics}"
    )
