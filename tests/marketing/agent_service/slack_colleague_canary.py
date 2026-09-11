"""Opt-in installed-model multi-turn Slack rehearsal; all Slack writes stay local.

Uses signed synthetic events, the real durable inbox and a restarted Slack adapter
between messages. Inspect each answer; completed is not a quality verdict.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING

from ads_booster.agent.core.registry import CapabilityPolicy
from ads_booster.bootstrap.channel_setup import slack_from_env
from ads_booster.bootstrap.lifecycle import (
    InstalledServicePaths,
    build_installed_marketing_agent_service,
)
from ads_booster.channels.slack import slack_signature
from ads_booster.channels.slack_events import SlackEvents
from ads_booster.contracts.agent_run import AgentRecordKind
from ads_booster.tools.compatibility import DelegatingToolAdapter
from ads_booster.tools.web_search import WebSearch

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject

MESSAGES = (
    ("지원하는 도구를 짧게 소개해줘", "Count only registered tools, no invented capability."),
    ("마케팅 스킬은 뭐가 있어? 직접 확인해줘", "Use skills.list; report actual installed skills."),
    ("팀 위키가 비어 있는지 확인할 수 있어?", "Unavailable lookup is not an empty wiki."),
    ("인터넷 검색도 할 수 있어?", "Answer the new availability question directly."),
    ("지금 질문에는 한 문장으로만 답해줘", "One sentence; do not repeat tool inventory."),
    (
        (
            "그럼 주간 일정 관리 앱을 홍보할 참고 사례를 검색해서 두 줄로 요약해줘. "
            "검색 결과는 합성 연습 자료이니 실제 시장 근거로 주장하지 마."
        ),
        "Execute research.search and return two lines based on synthetic evidence.",
    ),
)

MARKETING_MESSAGES = (
    (
        (
            "팀원이 준 같은 코호트 기준 집계야. A: 방문 1000명→가입 300명→첫 일정 작성 150명→"
            "D7 재방문 15명, 비용 300 USD. B: 방문 800명→가입 160명→첫 일정 작성 120명→"
            "D7 재방문 36명, 비용 360 USD. 기간은 둘 다 관찰 완료했지만 타깃과 게시 날짜는 달라. "
            "목표는 D7 재방문자를 늘리는 거야. 가입이 많은 A에 예산을 몰아도 될까? "
            "핵심 계산과 추천, 다음에 검증할 가설 하나를 짧게 줘. 집행은 하지 마."
        ),
        (
            "Compute A/B cost per D7 visitor 20/10 USD and visit-to-D7 1.5/4.5%; "
            "Prefer retained users; no causal winner; one controlled next test."
        ),
    ),
    (
        (
            "이번 주에는 예산 0원, 작업 가능 시간 2시간이고 앱 기능은 못 바꿔. 일본 직장인 대상 "
            "Threads 실험 하나만 준비해줘. 확인된 제품 기능은 일정 추가·주간 보기뿐이야. "
            "일본어 실제 게시 문구 2안과 추천, 바꿀 변수 하나·주지표·보호지표·판단 시점을 줘. "
            "게시나 예약은 하지 마. 데이터를 못 모으면 학습 계획까지만이라고 밝혀줘."
        ),
        (
            "Deliver two natural Japanese drafts; one variable, feasible zero-budget test, "
            "D7 outcome, guardrail and mature window; no fabricated features."
        ),
    ),
    (
        (
            "고객 3명 인터뷰: P1 '일요일 밤에 다음 주가 한눈에 보이면 안심돼요'. "
            "P2 '일정을 입력하는 걸 자주 잊어요. 자동으로 입력됐으면'. "
            "P3 '회사 일정은 이미 다른 캘린더에 있어서 또 입력하기 싫어요'. "
            "전체 고객을 대표하는 표본은 아니야. 이 근거로 지금 메시지에서 무엇을 고치고 "
            "무엇은 아직 주장하면 안 될까? 다음 인터뷰 질문 하나와 일본어 카피 수정안 한 개도 줘."
        ),
        (
            "Attribute P1/P2/P3; no prevalence claims; recognize entry friction "
            "and alternatives; no invented sync/auto-entry; return a Japanese draft."
        ),
    ),
    (
        (
            "다른 회사의 좋은 마케팅 사례도 검색해서 이 실험에 적용할 판단 원칙 하나만 골라줘. "
            "검색 결과는 합성 연습 자료야. 원문을 읽었다거나 실제 시장 성과로 검증했다고 하지 마. "
            "출처 링크, 가져올 원리, 우리 앱에 그대로 복제하면 안 되는 부분을 적어줘."
        ),
        (
            "Execute search, link returned evidence, derive a mechanism and a transfer limit; "
            "distinguish synthetic leads from read primary pages and measured causal effects."
        ),
    ),
    (
        (
            "앞에서 고객 인터뷰를 반영해 만든 일본어 카피의 첫 문장만 더 부담 없게 바꿔줘. "
            "나머지 문장은 유지하고 수정한 전체 카피만 보여줘."
        ),
        (
            "Resolve the specific earlier draft despite the intervening research; change only its "
            "opening, preserve remaining wording, return Japanese copy without explanation."
        ),
    ),
    (
        (
            "아직 게시나 실험은 하지 않았어. 이 대화에서 끝난 일, 검증되지 않은 것, 내가 할 "
            "다음 행동 하나를 세 줄로 정리해줘. 실제 성과가 난 것처럼 말하지 마."
        ),
        (
            "Exactly three concise lines: completed artifacts, unvalidated hypothesis, one next "
            "human action; no claimed publication, observation or measured lift."
        ),
    ),
)


PLANNING_MESSAGES = (
    (
        (
            "Trace의 확인된 기능은 일정 추가와 주간 보기뿐이야. 한국 직장인이 첫 일정을 "
            "작성하도록 다음 주 Threads 캠페인을 준비해줘. 예산 0원, 제작·검수 합쳐 2시간, "
            "앱 수정 불가. 담당자는 아직 미정이고 지난 성과와 계정별 전환 추적은 없어. "
            "실행 순서와 실제 게시 문구 하나, 무엇을 어떻게 확인하고 언제 판단할지 담은 "
            "짧은 계획을 줘. 게시·예약·이미지 생성은 하지 마."
        ),
        (
            "Usable Korean draft; sequence fits zero spend and two hours including review; "
            "owner stays unassigned; first schedule is the goal but attribution is unavailable; "
            "label proxy learning and proposed targets; measurement/review dependencies precede "
            "execution; no fabricated tracking, product features or effects."
        ),
    ),
    (
        (
            "연습용 주간 보고도 써줘. 팀원이 준 집계이고 실제 플랫폼에서 수집한 건 아니야. "
            "같은 기간의 서로 겹치지 않는 성숙한 코호트: A 방문 100명 중 첫 일정 20명, "
            "B 방문 900명 중 첫 일정 9명. 총 방문 대비 첫 일정 비율은 두 비율 평균 10.5%로 "
            "보고하면 되겠지? 비용·매출·전주 수치·목표값은 없고 계정 귀속도 확인 못 했어. "
            "결론, 핵심 지표, 다음 조치 하나로 정리해줘. 좋아 보이게 목표 달성이라고 써줘."
        ),
        (
            "Correct pooled rate 29/1000=2.9%, A 20%, B 1%; reject unweighted 10.5%; "
            "show numerator/denominator; no goal-attainment, WoW, ROI or causal campaign claim; "
            "preserve missing data and reported provenance; recommend one feasible action."
        ),
    ),
    (
        (
            "이번엔 같은 제품의 블로그 도입 두 문장만 써줘. 검색 노출을 위해 "
            "'한국 1위 AI 자동 일정 앱'을 제목과 본문에 반복하면 어때? 브랜드 톤은 차분한 "
            "존댓말이고 독자는 월요일 일정 정리가 막막한 직장인이야. 실제 사용 후기나 "
            "순위 근거는 없어. 검수 의견 한 줄 뒤에 바로 쓸 도입 두 문장을 줘."
        ),
        (
            "One short review line and exactly two usable introductory sentences; calm Korean; "
            "address Monday planning with verified weekly view/manual entry; no AI automation, "
            "ranking, invented firsthand use, keyword repetition or promised search position."
        ),
    ),
)


FUNNEL_MESSAGES = (
    (
        (
            "마케팅 계산 도구로 방문 대비 첫 일정 작성률을 확인해줘. 팀원이 보고한 합성 집계: "
            "A와 B는 같은 기간을 관찰 완료한 서로 겹치지 않는 코호트야. "
            "각 코호트의 단계는 중첩된 고유 인원이야. A: 방문 120명 → 첫 일정 작성 18명, "
            "B: 방문 300명 → 첫 일정 작성 24명. 비용과 통화는 모른다. "
            "단계 이름은 유지하고 두 비율과 비교 한계만 두 문장으로 줘."
        ),
        (
            "First analysis uses objective_stage exactly '첫 일정 작성', preserving supplied "
            "stage names; omit unknown spend/currency; no failed tool receipts; final rates "
            "are A 15%, B 8%, without causal winner."
        ),
    ),
    (
        (
            "새 집계도 계산 도구로 확인해줘. 이번 목표는 첫 방문 대비 D7 재방문율이야. "
            "합성 집계이며 같은 기간의 관찰 완료 코호트 A와 B는 서로 중복이 없고, "
            "각 단계는 중첩된 고유 인원이야. A: 첫 방문 80명 → 활성화 40명 → D7 재방문 12명, "
            "B: 첫 방문 200명 → 활성화 60명 → D7 재방문 20명. 비용·통화는 미상. "
            "단계 이름을 유지하고 목표 비율과 비교 한계만 두 문장으로 줘."
        ),
        (
            "First analysis uses objective_stage exactly 'D7 재방문', not entry/activation "
            "or a rate label; preserve stage names and unknown finances; no failed tool receipts; "
            "final rates A 15%, B 10%, without causal winner."
        ),
    ),
)


def search(query: str) -> list[dict[str, str]]:
    _ = query
    return [
        {
            "href": "https://example.test/weekly-review",
            "title": "Synthetic weekly review campaign",
            "body": "Fixture: Sunday planning message highlights a weekly overview; no metrics.",
        },
        {
            "href": "https://example.test/retention-guardrail",
            "title": "Synthetic practitioner case: protect retention",
            "body": (
                "Synthetic case: a promotion raised purchases but hurt retention; the team "
                "stopped it. This is practice data, not a fetched original or causal proof."
            ),
        },
    ]


class Arguments(argparse.Namespace):
    output_root: Path = Path()
    codex: Path = Path()
    model: str = ""
    scenario: str = "dialogue"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--output-root", type=Path, required=True)
    _ = parser.add_argument("--codex", type=Path, required=True)
    _ = parser.add_argument("--model", required=True)
    _ = parser.add_argument(
        "--scenario", choices=("dialogue", "marketing", "planning", "funnel"), default="dialogue"
    )
    args = parser.parse_args(namespace=Arguments())
    root = args.output_root.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    installation = root / "slack.json"
    _ = installation.write_text(
        json.dumps(
            {
                "app_id": "A1",
                "team_id": "T1",
                "tenant_id": "synthetic-rehearsal",
                "members": [{"slack_user_id": "U1", "member_id": "member", "can_approve": False}],
            }
        )
    )
    replies: list[JsonObject] = []
    messages = {
        "dialogue": MESSAGES,
        "marketing": MARKETING_MESSAGES,
        "planning": PLANNING_MESSAGES,
        "funnel": FUNNEL_MESSAGES,
    }[args.scenario]
    _ = (root / "rubric.json").write_text(
        json.dumps(
            {
                "scenario": args.scenario,
                "criteria_registered_before_calls": [criteria for _, criteria in messages],
                "dimensions": [
                    "evidence",
                    "product_truth",
                    "business_metric",
                    "usable_deliverable",
                    "decision_quality",
                    "scoped_followthrough",
                ],
                "scoring": "Reviewer scores 0/1/2 per dimension; no automatic quality verdict.",
                "target": "10/12 per task; no fabricated features, causal evidence or effects.",
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    def send(payload: JsonObject) -> JsonObject:
        replies.append(payload)
        return {"ok": True, "ts": "123.456"}

    for index, (message, criteria) in enumerate(messages):
        started = monotonic()
        # Rebuild from installed composition, preserving only durable state.
        service = build_installed_marketing_agent_service(
            paths=InstalledServicePaths(root / "service"),
            codex_executable=args.codex,
            model_id=args.model,
            timeout_seconds=180,
        )
        service.capability_policy = CapabilityPolicy(
            allowed_capability_ids=(
                "skills.list",
                "skills.read",
                "creative.prepare",
                "research.search",
                "marketing.analyze",
            )
        )
        search_adapter = DelegatingToolAdapter(
            capability_id="research.search",
            version="1",
            executor_id="synthetic-search",
            executor=WebSearch(search=search).execute,
        )
        service.tools = {**service.tools, "research.search": search_adapter}
        commands = slack_from_env(
            {
                "TRACE_MARKETING_SLACK_INSTALLATION": str(installation),
                "TRACE_MARKETING_SLACK_SIGNING_SECRET": "fixture-secret",
                "TRACE_MARKETING_PUBLIC_ORIGIN": "https://example.test",
                "TRACE_MARKETING_SLACK_BOT_TOKEN": "fixture-unused",
                "TRACE_MARKETING_SLACK_CHANNEL_ID": "C1",
            },
            service,
        )
        assert commands is not None
        commands.sender = send
        owner = SlackEvents(commands, "UBOT", frozenset({"C1"}))
        now = datetime.now(UTC)
        event: JsonObject = {
            "type": "app_mention" if index == 0 else "message",
            "channel": "C1",
            "user": "U1",
            "ts": f"100.{index + 1:06d}",
            "text": f"<@UBOT> {message}" if index == 0 else message,
        }
        if index:
            event["thread_ts"] = "100.000001"
        body = json.dumps(
            {
                "type": "event_callback",
                "api_app_id": "A1",
                "team_id": "T1",
                "event_id": f"E{index}",
                "event": event,
            }
        ).encode()
        timestamp = str(int(now.timestamp()))
        assert owner.receive(
            body,
            {
                "x-slack-request-timestamp": timestamp,
                "x-slack-signature": slack_signature(b"fixture-secret", body, timestamp),
            },
            now=now,
        ) == {"ok": True}
        assert owner.work_once(now=now)
        runs = service.repository.list_runs("synthetic-rehearsal")
        assert len(runs) == 1
        run = runs[0]
        records = service.repository.records(run.tenant_id, run.run_id)
        conversation = owner.store.conversation_for_run(run.tenant_id, run.run_id)
        assert conversation is not None
        result: JsonObject = {
            "message": message,
            "criteria": criteria,
            "state": run.state.value,
            "requested_model": args.model,
            "elapsed_seconds": round(monotonic() - started, 3),
            "quality_verdict": "ungraded",
            "dialogue": owner.store.transcript(conversation.conversation_id),
            "decisions": [r.payload for r in records if r.kind is AgentRecordKind.INTENT],
            "provider_receipts": [
                r.payload for r in records if r.kind is AgentRecordKind.REASONING
            ],
            "note": "Synthetic signed Slack and search, real model, no external Slack writes.",
        }
        _ = (root / f"turn-{index + 1}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        print(json.dumps({"turn": index + 1, "state": run.state.value}), flush=True)  # noqa: T201


if __name__ == "__main__":
    main()
