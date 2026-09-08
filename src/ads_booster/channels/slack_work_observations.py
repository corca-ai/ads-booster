"""Record attributed human effort without converting it into verified tool costs."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import TYPE_CHECKING

from ads_booster.contracts.agent_memory import MemoryAccess, MemoryScope
from ads_booster.contracts.agent_run import AgentRecordKind, ToolReceiptRecord, contract_sha256
from ads_booster.contracts.work_observation import WorkObservation, WorkPhase
from ads_booster.learning.work_observations import WorkObservationStore

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.agent.service.application import MarketingAgentService
    from ads_booster.channels.contracts import ChannelIdentityBinding
    from ads_booster.channels.slack_conversations import Conversation, Message

_HELP = (
    "작업 기록 제작|수정|검수|현지화 12분 설명\n"
    "언어별 기록: 작업 기록 현지화 12분 언어=ja 설명\n"
    "작업 정정 ID 제작|수정|검수|현지화 10분 설명\n"
    "작업 요약 / 작업 학습 ID 관찰 | 반례 | 적용범위\n"
    "시간은 사람이 보고한 값으로 기록하며 실제 작업 시작 시각을 추정하지 않습니다."
)
_PHASES: dict[str, WorkPhase] = {
    "제작": "production",
    "수정": "revision",
    "검수": "review",
    "현지화": "localization",
}
_ENTRY = re.compile(
    "".join(  # noqa: FLY002 - split raw regex without f-string brace escaping.
        (
            r"^(제작|수정|검수|현지화) ([0-9]{1,5})분",
            r"(?: 언어=([a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*))? (.{1,1000})$",
        )
    )
)
_MAX_MINUTES = 1440
_LEARNING_FIELDS = 3


def is_work_observation_command(text: str) -> bool:
    prefix, _, remainder = text.partition(" ")
    action, _, _ = remainder.partition(" ")
    return prefix == "작업" and action in {"기록", "정정", "요약", "학습"}


def work_observation_command(  # noqa: PLR0911 - scoped command responses.
    service: MarketingAgentService,
    conversation: Conversation,
    message: Message,
    identity: ChannelIdentityBinding,
    *,
    now: datetime,
) -> str:
    run = service.repository.get(conversation.tenant_id, conversation.current_run)
    if run is None:
        return "먼저 이 스레드에서 맡길 작업을 알려주세요. 기록을 그 업무에 연결하겠습니다."
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
    store = WorkObservationStore(service.repository.database_path)
    _, _, remainder = message.text.partition(" ")
    action, _, argument = remainder.partition(" ")
    if action == "요약":
        summary = store.summarize(access)
        receipts = tuple(
            ToolReceiptRecord.model_validate(record.payload)
            for record in service.repository.records(conversation.tenant_id, run.run_id)
            if record.kind is AgentRecordKind.RECEIPT
        )
        phase_totals: dict[str, float] = {}
        for item in summary.observations:
            phase = next(
                (label for label, value in _PHASES.items() if value == item.phase), item.phase
            )
            label = f"{phase} / {item.locale or '언어 미지정'}"
            phase_totals[label] = phase_totals.get(label, 0) + item.elapsed_minutes
        breakdown = "\n".join(
            f"{label}: {minutes:g}분" for label, minutes in sorted(phase_totals.items())[:12]
        )
        recent = "\n".join(
            f"{item.observation_id}: {item.note[:100]}"
            for item in sorted(summary.observations, key=lambda item: item.recorded_at)[-6:]
        )
        return (
            f"사람 보고 합계: {summary.elapsed_minutes:g}분, "
            f"수정 보고 {summary.revision_count}회.\n"
            f"유효 기록 {len(summary.observations)}개. "
            "중복 시간대는 실제 경과 시간과 다를 수 있습니다.\n"
            f"단계·언어별(최대 12개):\n{breakdown}\n최근 기록(최대 6개):\n{recent}\n"
            f"확인된 도구 receipt: {len(receipts)}개, 비용 단위 합계 "
            f"{sum(item.actual_cost_units for item in receipts)} (통화 금액 아님).\n"
            "사람 시간과 도구 비용은 별도 값입니다. 기록되지 않은 시간·비용은 포함하지 않았습니다."
        )
    if action == "학습":
        observation_id, _, details = argument.partition(" ")
        parts = [part.strip() for part in details.split("|")]
        if len(parts) != _LEARNING_FIELDS or not all(parts):
            return _HELP
        note = store.learning_candidate(
            access,
            note_id="work-learning-" + contract_sha256({"message": message.message_id})[:32],
            observation_ids=(observation_id,),
            observation=parts[0],
            counterexample=parts[1],
            applicability=parts[2],
            now=now,
            expires_at=now + timedelta(days=30),
        )
        return (
            f"이 업무의 학습 후보를 만들었습니다. 아직 적용하지 않습니다.\n기억 검토 {note.note_id}"
        )
    supersedes = None
    if action == "정정":
        supersedes, _, argument = argument.partition(" ")
    elif action != "기록":
        return _HELP
    match = _ENTRY.fullmatch(argument)
    if match is None or int(match[2]) > _MAX_MINUTES:
        return _HELP
    observation_id = "work-observation-" + contract_sha256({"message": message.message_id})[:32]
    existing = store.get(observation_id, access)
    if existing is None:
        observation = WorkObservation(
            observation_id=observation_id,
            scope=access.scope,
            author_id=identity.member_id,
            source_ref=f"slack:{conversation.channel_id}:{message.message_id}",
            source_sha256=contract_sha256({"message": message.text}),
            window_start=now,
            window_end=now,
            window_kind="report_time",
            recorded_at=now,
            phase=_PHASES[match[1]],
            elapsed_minutes=int(match[2]),
            revision_count=1 if match[1] == "수정" else 0,
            locale=match[3] or "",
            note=match[4],
            supersedes=supersedes,
        )
        _ = store.record(observation, access)
    return (
        f"사람이 보고한 작업 시간을 기록했습니다: {observation_id}\n"
        "실제 시작 시각·제품 성능을 검증한 기록은 아닙니다. 작업 요약으로 확인하세요."
    )
