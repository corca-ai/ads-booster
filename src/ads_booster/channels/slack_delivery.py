"""Member-authorized review of prepared plans; never channel execution."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.contracts.creative_work import CreativeScope
from ads_booster.creative.creative_asset_verifier import CreativeAssetVerifier
from ads_booster.creative.creative_assets import SqliteCreativeAssetRepository
from ads_booster.delivery.delivery_review import DeliveryReviewStore
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from ads_booster.contracts.marketing_delivery import DeliveryReviewPacket
    from ads_booster.channels.contracts import ChannelIdentityBinding
    from ads_booster.channels.slack_conversations import Conversation, Message


_REVIEW_FIELDS = 3
_APPROVE_FIELDS = 5
_STATE_FIELDS = 4
_PAGE_CHARS = 3500
_MAX_PAGE_DIGITS = 6
_BRIEF_CHARS = 1200
_FIELD_CHARS = 180
_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_KINDS = {
    "production": "제작 준비안",
    "publication": "최종 게시 준비안",
    "paid_budget": "광고 예산 준비안",
    "paid_execution": "광고 집행 준비안",
    "format_promotion": "포맷 채택 준비안",
    "format_deactivation": "포맷 비활성화 준비안",
    "code_improvement": "코드 개선 제안",
    "post_publication_change": "게시 후 변경 준비안",
}
_DECISION_FIELDS = {
    "instructions": "작업",
    "preserve": "유지",
    "change": "변경",
    "max_cost_units": "비용 한도(도구 단위)",
    "account_id": "계정",
    "channel": "채널",
    "text": "문구",
    "schedule_at": "게시 시각(UTC)",
    "conditions": "게시 조건",
    "qa_summary": "제안자가 제공한 검수 요약",
    "currency": "통화",
    "max_minor_units": "예산 한도(통화 최소 단위)",
    "spend_minor_units": "집행액(통화 최소 단위)",
    "purpose": "목적",
    "audience": "대상",
    "execution_conditions": "집행 조건",
    "format_id": "포맷",
    "applicability": "적용 범위",
    "recommendation": "추천",
    "counterexamples": "반례",
    "problem": "문제",
    "reproduction": "재현",
    "impact": "영향",
    "human_review_path": "사람 리뷰 경로",
    "action": "변경 행동",
    "draft": "변경 초안",
    "alternatives": "대안",
}


def _decision_brief(packet: DeliveryReviewPacket) -> str:
    proposal = packet.proposal
    lines = [
        _KINDS[proposal.target.kind],
        f"이유: {proposal.rationale[:_FIELD_CHARS]}",
    ]
    target = _OBJECT.validate_python(proposal.target.model_dump(mode="json"))
    for key, label in _DECISION_FIELDS.items():
        if key not in target:
            continue
        value = target[key]
        rendered = (
            " / ".join(str(item) for item in value) if isinstance(value, list) else str(value)
        )
        if value is None:
            rendered = "미지정"
        if len(rendered) > _FIELD_CHARS:
            rendered = rendered[:_FIELD_CHARS] + "…"
        lines.append(f"{label}: {rendered}")
    summary = "\n".join(lines)
    if len(summary) > _BRIEF_CHARS:
        summary = summary[:_BRIEF_CHARS] + "…"
    return summary + "\n검토 안내용 요약입니다. 승인 전 아래 본문 전체의 대상·조건을 확인하세요.\n"


def delivery_command(  # noqa: PLR0911 - explicit reviewer commands.
    database: Path,
    conversation: Conversation,
    message: Message,
    identity: ChannelIdentityBinding,
    *,
    now: datetime,
) -> str:
    if conversation.private:
        return "공유 실행안 검토와 승인은 허용된 팀 스레드에서 진행하세요."
    fields = message.text.split()
    if len(fields) < _REVIEW_FIELDS:
        return (
            "실행안 검토 ID / 실행안 승인 ID 버전 해시 / 실행안 예약 ID 버전 / 실행안 취소 ID 버전"
        )
    action, proposal_id = fields[1:3]
    store = DeliveryReviewStore(
        database,
        asset_verifier=CreativeAssetVerifier(
            SqliteCreativeAssetRepository(database, database.parent / "artifacts")
        ),
    )
    scope = CreativeScope(workspace_id=identity.tenant_id, product_id="trace")
    packet = store.get(scope, proposal_id)
    if packet is None or packet.proposal.run_id != conversation.current_run:
        return "이 업무의 실행안을 찾지 못했습니다."
    if action == "검토":
        if len(fields) not in {_REVIEW_FIELDS, _STATE_FIELDS}:
            return "실행안 검토 ID 페이지 번호로 요청하세요."
        page_text = fields[3] if len(fields) == _STATE_FIELDS else "1"
        return _review_page(packet, page_text)
    if not identity.can_approve:
        return "이 실행안을 승인할 권한이 없습니다."
    if action in {"승인", "거절"} and len(fields) == _APPROVE_FIELDS:
        packet = store.review(
            scope,
            proposal_id,
            expected_revision=int(fields[3]),
            expected_target_sha256=fields[4],
            reviewer_id=identity.member_id,
            reviewer_authorized=identity.can_approve,
            approved=action == "승인",
            expires_at=now + timedelta(minutes=5),
            now=now,
        )
    elif action == "예약" and len(fields) == _STATE_FIELDS:
        packet = store.schedule(scope, proposal_id, expected_revision=int(fields[3]), now=now)
    elif action == "취소" and len(fields) == _STATE_FIELDS:
        packet = store.cancel(scope, proposal_id, expected_revision=int(fields[3]))
    else:
        return "실행안 검토 ID로 현재 버전과 해시를 확인하세요."
    return "".join(
        (
            f"준비안 상태: {packet.state}, 버전: {packet.revision}. ",
            "실제 게시·광고 집행은 하지 않았습니다.",
        )
    )


def _review_page(packet: DeliveryReviewPacket, page_text: str) -> str:
    if not page_text.isdecimal() or len(page_text) > _MAX_PAGE_DIGITS:
        return "페이지 번호는 양의 정수로 입력하세요."
    page = int(page_text)
    content = packet.model_dump_json()
    count = max(1, (len(content) + _PAGE_CHARS - 1) // _PAGE_CHARS)
    if not 1 <= page <= count:
        return f"페이지 번호는 1~{count}로 입력하세요."
    proposal_id = packet.proposal.proposal_id
    chunk = content[(page - 1) * _PAGE_CHARS : page * _PAGE_CHARS]
    header = f"실행안 {proposal_id} · 버전 {packet.revision} · 페이지 {page}/{count}"
    if page < count:
        footer = f"다음 페이지: 실행안 검토 {proposal_id} {page + 1}"
    else:
        footer = "\n".join(
            (
                "같은 버전의 모든 페이지 확인 후 승인하세요. 버전이 바뀌면 처음부터 검토하세요.",
                "준비안만 검토합니다. 실제 게시·광고 집행은 꺼져 있습니다.",
                f"실행안 승인 {proposal_id} {packet.revision} {packet.proposal.target_sha256}",
            )
        )
    brief = _decision_brief(packet) if page == 1 else ""
    return f"{header}\n{brief}본문:\n{chunk}\n\n{footer}"
