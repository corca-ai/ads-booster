"""A complete approval target stays readable within bounded Slack messages."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ads_booster.contracts.creative_work import CreativeScope
from ads_booster.contracts.marketing_delivery import DeliveryProposal, ProductionTarget
from ads_booster.marketing.agent_service.delivery_review import DeliveryReviewStore
from ads_booster.marketing.channels.contracts import ChannelIdentityBinding
from ads_booster.marketing.channels.slack_conversations import Conversation, Message
from ads_booster.marketing.channels.slack_delivery import delivery_command

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 9, 7, tzinfo=UTC)
IDENTITY = ChannelIdentityBinding(
    schema_version="trace.channel-identity-binding.v1",
    binding_id="b",
    installation_id="i",
    external_user_id="U1",
    tenant_id="trace",
    member_id="reviewer",
    can_approve=True,
    created_at=NOW,
)
CONVERSATION = Conversation(
    conversation_id="c",
    tenant_id="trace",
    channel_id="C1",
    thread_ts="1.0",
    owner_id="",
    private=False,
    current_run="r",
)


def _command(
    database: Path,
    text: str,
    *,
    identity: ChannelIdentityBinding = IDENTITY,
    conversation: Conversation = CONVERSATION,
) -> str:
    return delivery_command(
        database,
        conversation,
        Message(message_id="m", conversation_id="c", user_id="U1", text=text),
        identity,
        now=NOW,
    )


def _prepare(database: Path) -> str:
    proposal = DeliveryProposal(
        proposal_id="p",
        scope=CreativeScope(workspace_id="trace", product_id="trace"),
        run_id="r",
        rationale="Large but schema-valid review",
        target=ProductionTarget(
            input_sha256="a" * 64,
            instructions="Keep character",
            preserve=("x" * 4000,) * 32,
            change=("y" * 4000,) * 32,
            max_cost_units=1,
        ),
    )
    return (
        DeliveryReviewStore(database)
        .prepare(proposal, actor_scope=proposal.scope)
        .model_dump_json()
    )


def test_review_never_exceeds_slack_message_bound(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    _ = _prepare(database)
    text = _command(database, "실행안 검토 p")
    assert len(text) <= 6000


def test_review_leads_with_decision_and_keeps_exact_target(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    proposal = DeliveryProposal(
        proposal_id="p",
        scope=CreativeScope(workspace_id="trace", product_id="trace"),
        run_id="r",
        rationale="달력 위쪽 글자가 잘 보이도록 여백 확보",
        target=ProductionTarget(
            input_sha256="a" * 64,
            instructions="위쪽 배경만 확장",
            preserve=("캐릭터와 달력",),
            change=("위쪽 여백",),
            max_cost_units=4,
        ),
    )
    packet = DeliveryReviewStore(database).prepare(proposal, actor_scope=proposal.scope)
    text = _command(database, "실행안 검토 p")
    brief, body = text.split("본문:\n", 1)
    assert "제작 준비안" in brief
    assert "이유: 달력 위쪽 글자가 잘 보이도록 여백 확보" in brief
    assert "유지: 캐릭터와 달력" in brief
    assert "변경: 위쪽 여백" in brief
    assert "비용 한도(도구 단위): 4" in brief
    assert packet.model_dump_json() in body
    assert packet.proposal.target_sha256 in body
    assert "본문 전체" in brief


def test_pages_preserve_complete_target_and_approval_only_on_last(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    original = _prepare(database)
    first = _command(database, "실행안 검토 p")
    matched = re.search(r"페이지 1/(\d+)", first)
    assert matched is not None
    count = int(matched.group(1))
    restored: list[str] = []
    for page in range(1, count + 1):
        text = _command(database, f"실행안 검토 p {page}")
        assert len(text) <= 6000
        restored.append(text.split("본문:\n", 1)[1].split("\n\n", 1)[0])
        if page < count:
            assert "실행안 승인" not in text
            assert f"실행안 검토 p {page + 1}" in text
        else:
            assert "같은 버전의 모든 페이지" in text
            assert "실행안 승인 p 1 " in text
    assert "".join(restored) == original
    packet = DeliveryReviewStore(database).get(
        CreativeScope(workspace_id="trace", product_id="trace"), "p"
    )
    assert packet is not None
    assert packet.state == "draft"


def test_review_preserves_run_identity_and_reviewer_boundaries(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    _ = _prepare(database)
    assert "찾지 못" in _command(
        database,
        "실행안 검토 p",
        conversation=CONVERSATION.model_copy(update={"current_run": "other"}),
    )
    assert "찾지 못" in _command(
        database, "실행안 검토 p", identity=IDENTITY.model_copy(update={"tenant_id": "other"})
    )
    assert "팀 스레드" in _command(
        database, "실행안 검토 p", conversation=CONVERSATION.model_copy(update={"private": True})
    )
    assert "권한" in _command(
        database,
        "실행안 승인 p 1 " + "a" * 64,
        identity=IDENTITY.model_copy(update={"can_approve": False}),
    )
    assert "페이지" in _command(database, "실행안 검토 p 0")
