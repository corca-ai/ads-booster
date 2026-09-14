"""Readable Slack state projection; callers hold the service Run lock."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import AgentRecordKind, AgentRunState, AgentStepKind

if TYPE_CHECKING:
    from ads_booster.contracts.agent_run import AgentRecord, AgentRun, AgentStep


def run_status(run: AgentRun, steps: tuple[AgentStep, ...]) -> str:
    if run.state is AgentRunState.RUNNING:
        # Under the Run lock, RUNNING without a worker wait is an interrupted
        # foreground turn, not evidence that a provider is still doing work.
        stage = steps[-1].kind if steps else None
        if stage is AgentStepKind.OBSERVE:
            return (
                "요청을 해석하던 중 처리가 중단됐습니다. 이번 요청의 다음 실행은 시작하지 "
                "않았습니다. 새 요청을 보내면 이어서 처리합니다."
            )
        if stage in {AgentStepKind.PLAN, AgentStepKind.APPROVE}:
            return "실행 준비 중 처리가 중단됐습니다. 준비하던 도구는 아직 실행하지 않았습니다."
        return "실행 결과를 확정하기 전에 처리가 중단됐습니다. 중복 실행하지 않았습니다."
    return {
        AgentRunState.CREATED: "요청을 접수했습니다. 아직 실행을 시작하지 않았습니다.",
        AgentRunState.AWAITING_APPROVAL: "실행할 내용을 제안했습니다. 아직 실행하지 않았습니다.",
        AgentRunState.AWAITING_INPUT: "추가 내용을 기다리고 있습니다.",
        AgentRunState.AWAITING_TOOL: "요청한 도구가 처리 중이며 결과를 기다리고 있습니다.",
        AgentRunState.AWAITING_RECONCILIATION: (
            "실행을 요청했지만 결과가 확인되지 않았습니다. 중복 실행하지 않았습니다."
        ),
        AgentRunState.BLOCKED: "작업을 진행할 수 없어 완료하지 못했습니다.",
        AgentRunState.COMPLETED: "완료",
        AgentRunState.STOPPED: "작업을 멈췄습니다. 이미 실행된 결과는 유지됩니다.",
        AgentRunState.FAILED: "오류로 작업을 완료하지 못했습니다.",
    }[run.state]


def conversational_answer(
    run: AgentRun, steps: tuple[AgentStep, ...], records: tuple[AgentRecord, ...]
) -> str:
    """A knowledge/input wait may have no reasoning answer, or only an older one."""
    if run.state is AgentRunState.AWAITING_INPUT:
        output = steps[-1].output_sha256 if steps else None
        current = next((r for r in records if r.payload_sha256 == output), None)
        if (
            current is not None
            and current.payload_schema_version == "trace.knowledge-preparation-blocked.v1"
        ):
            preparation = current.payload.get("preparation")
            if isinstance(preparation, dict):
                if preparation.get("status") == "brand_unresolved":
                    return (
                        "여러 브랜드가 있어 사용할 브랜드를 정하지 못했습니다. "
                        "어느 브랜드의 게시물인지 알려주세요."
                    )
                reason = preparation.get("error_code")
                return {
                    "required_voice_unavailable": (
                        "선택한 브랜드의 표현 기준을 확인하지 못해 제작을 시작하지 않았습니다. "
                        "사용할 브랜드나 표현 기준을 알려주세요."
                    ),
                    "scope_unresolved": (
                        "요청한 브랜드나 자료를 이 대화에서 찾지 못했습니다. "
                        "사용할 브랜드나 자료를 알려주세요."
                    ),
                    "correction_pending": (
                        "앞서 수정한 내용을 반영 중이라 작업을 시작하지 않았습니다."
                    ),
                    "constraint_conflict": (
                        "적용할 작업 기준이 서로 충돌합니다. "
                        "이번 요청에서 우선할 기준을 알려주세요."
                    ),
                }.get(
                    str(reason),
                    (
                        "작업에 필요한 자료를 확인하지 못해 시작하지 않았습니다. "
                        "다른 요청이나 추가 설명을 보내면 이어서 처리합니다."
                    ),
                )
        if current is None or current.kind is not AgentRecordKind.INTENT:
            return run_status(run, steps)
        return str(current.payload.get("reasoning_summary", "")).strip() or run_status(run, steps)
    latest = next((r for r in reversed(records) if r.kind is AgentRecordKind.REASONING), None)
    decision = None if latest is None else latest.payload.get("decision")
    answer = (
        str(decision.get("reasoning_summary", "")).strip() if isinstance(decision, dict) else ""
    )
    return answer or run_status(run, steps)
