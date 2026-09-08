"""Opt-in real-model rehearsal with installed code and synthetic read-only tool data.

Run with a fresh wheel environment, not pytest. Review the emitted decisions against
the scenario's criteria; a completed Run alone is not a marketing-quality verdict.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import AgentBudget, AgentGoal, AgentRecordKind
from ads_booster.marketing.agent_core.registry import CapabilityPolicy
from ads_booster.marketing.agent_service.application import CreateAgentRunRequest
from ads_booster.marketing.agent_service.lifecycle import (
    InstalledServicePaths,
    build_installed_marketing_agent_service,
)
from ads_booster.marketing.agent_service.web_search import WebSearch
from ads_booster.marketing.tool_adapters.compatibility import DelegatingToolAdapter

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject


SCENARIOS = (
    (
        "copy",
        (
            "한국 Threads용으로 일정 앱 홍보 문구를 2안 써줘. 직장인 대상, 친근한 존댓말. "
            "확인된 기능은 일정 추가와 주간 보기뿐이야. 추천도 골라줘. 이미지와 게시는 요청 안 해."
        ),
        "Return two actual Korean drafts and a recommendation; no invented features or setup form.",
    ),
    (
        "opportunity",
        (
            "일본 직장인에게 일정 앱의 주간 보기를 홍보할 기회를 찾아줘. 최신 신호를 조사하고 "
            "출처와 함께 두 가지 대안, 추천, 작게 검증할 실험을 제안해줘. "
            "이 리허설의 검색 결과는 합성 데이터이니 실제 트렌드로 주장하지 마."
        ),
        "Load relevant guidance, search, cite synthetic sources and recommend an experiment.",
    ),
    (
        "experiment",
        (
            "성과를 보고 다음 실험을 추천해줘. 같은 채널 게시물 A는 조회 1000·좋아요 60, "
            "B는 조회 10000·좋아요 300이야. 게시 날짜와 타깃은 달랐어. "
            "B가 훨씬 성공했으니 무조건 B만 복제하면 될까?"
        ),
        "Compare 6% and 3%; discuss confounds; avoid causal winners; propose a one-variable test.",
    ),
    (
        "creative_reference",
        (
            "creative.reference 스킬로 여백 많은 차분한 달력 배경 레퍼런스를 찾아 비교해줘. "
            "도구로 검색까지 진행해줘. 합성 검색 결과만 있어도 연습용으로 비교해줘."
        ),
        "Read creative.reference v2 and execute available search; do not stop at a prepared brief.",
    ),
)


def _search(query: str) -> list[dict[str, str]]:
    _ = query
    return [
        {
            "href": "https://example.test/rehearsal/weekly-planning",
            "title": "Synthetic fixture: weekly planning interviews",
            "body": "Synthetic interviews: workers plan on Sunday evenings; not market evidence.",
        },
        {
            "href": "https://example.test/rehearsal/quiet-background",
            "title": "Synthetic fixture: quiet backgrounds",
            "body": "Synthetic lead: plain upper area, muted blue. Image not inspected.",
        },
    ]


class Arguments(argparse.Namespace):
    output_root: Path = Path()
    codex: Path = Path()
    model: str = ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--output-root", type=Path, required=True)
    _ = parser.add_argument("--codex", type=Path, required=True)
    _ = parser.add_argument("--model", required=True)
    arguments = parser.parse_args(namespace=Arguments())
    root = arguments.output_root.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    model = arguments.model
    for name, objective, criteria in SCENARIOS:
        service = build_installed_marketing_agent_service(
            paths=InstalledServicePaths(root / name),
            codex_executable=arguments.codex,
            model_id=model,
            timeout_seconds=180,
        )
        service.capability_policy = CapabilityPolicy(
            allowed_capability_ids=(
                "skills.list",
                "skills.read",
                "creative.prepare",
                "research.search",
            )
        )
        service.tools = {
            key: value
            for key, value in service.tools.items()
            if service.capability_policy.permits(key)
        }
        service.tools["research.search"] = DelegatingToolAdapter(
            capability_id="research.search",
            version="1",
            executor_id="synthetic-search",
            executor=WebSearch(search=_search).execute,
        )
        run = service.create(
            CreateAgentRunRequest(
                run_id=name,
                tenant_id="synthetic-rehearsal",
                goal=AgentGoal(
                    objective=objective, success_criteria=("Fulfil the requested deliverable",)
                ),
                budget=AgentBudget(max_tool_calls=8, max_cost_units=20),
            ),
            now=datetime.now(UTC),
        )
        records = service.repository.records(run.tenant_id, run.run_id)
        result: JsonObject = {
            "scenario": name,
            "criteria": criteria,
            "state": run.state.value,
            "requested_model": model,
            "decisions": [r.payload for r in records if r.kind is AgentRecordKind.INTENT],
            "provider_receipts": [
                r.payload for r in records if r.kind is AgentRecordKind.REASONING
            ],
            "note": "Real Codex; synthetic search; no live marketing effects. Review manually.",
        }
        _ = (root / f"{name}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps({"scenario": name, "state": run.state.value}), flush=True)  # noqa: T201


if __name__ == "__main__":
    main()
