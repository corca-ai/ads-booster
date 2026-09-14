"""Readable Slack state projection; callers hold the service execution lock."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import AgentRunState, AgentStepKind

if TYPE_CHECKING:
    from ads_booster.contracts.agent_run import AgentRun, AgentStep


def run_status(run: AgentRun, steps: tuple[AgentStep, ...]) -> str:
    if run.state is AgentRunState.RUNNING:
        # Under the execution lock, RUNNING without a worker wait is an interrupted
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
