"""Versioned procedures executed by the one canonical Marketing Agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ads_booster.contracts.agent_run import AgentGoal

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.marketing.agent_core.registry import ToolRegistry
    from ads_booster.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class MarketingSkill:
    skill_id: str
    version: str
    purpose: str
    required_capabilities: tuple[str, ...]
    success_criteria: tuple[str, ...]
    procedure: str

    def goal(self, context: JsonObject) -> AgentGoal:
        return AgentGoal(
            objective=f"{self.purpose}\n\n필수 절차:\n{self.procedure}",
            success_criteria=self.success_criteria,
            context={
                "skill_id": self.skill_id,
                "skill_version": self.version,
                "input": context,
                "required_capabilities": list(self.required_capabilities),
            },
        )


SKILLS = (
    MarketingSkill(
        skill_id="research.daily_slack",
        version="1",
        purpose="오늘의 근거 기반 Trace 마케팅 기회를 조사해 팀에 전달하고 일별 기록을 남긴다.",
        required_capabilities=("research.web", "deliver.slack", "store.notion.daily"),
        success_criteria=(
            "출처와 반증 질문을 포함한 오늘의 마케팅 브리프가 완성된다.",
            "동일 브리프가 설정된 Slack 채널과 Notion 일별 페이지에 전달된다.",
        ),
        procedure=(
            "1. input에 제공된 immutable research_request를 research.web에 그대로 전달한다.\n"
            "2. receipt로 확인된 조사 결과만 요약한다. 추측을 사실처럼 쓰지 않는다.\n"
            "3. 같은 브리프를 deliver.slack의 text와 store.notion.daily의 "
            "title/content로 전달한다.\n"
            "4. 두 전달 receipt를 확인한 뒤에만 완료한다."
        ),
    ),
    MarketingSkill(
        skill_id="threads.validated_format_replication",
        version="1",
        purpose="검증된 이미지 또는 URL 포맷을 국가별 Trace 콘텐츠 실험으로 복제한다.",
        required_capabilities=("workflow.feature_launch",),
        success_criteria=(
            "입력 이미지 또는 URL과 대상 국가·계정이 immutable 실행 요청에 보존된다.",
            "기존 hosted 연구·기획·Appium·검수·Threads 파이프라인에 한 번만 위임된다.",
            "이미지와 게시 승인을 우회하지 않는다.",
        ),
        procedure=(
            "1. input의 feature_launch_request를 변경하지 않고 "
            "workflow.feature_launch에 전달한다.\n"
            "2. hosted run ID와 상태 receipt를 보존한다.\n"
            "3. Appium 이미지 검수와 Threads 게시 승인은 hosted workflow에서 계속 집행한다.\n"
            "4. 자동 게시나 승인 우회를 제안하지 않는다."
        ),
    ),
)


class MarketingSkillCatalog:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry: ToolRegistry = registry

    def get(self, skill_id: str) -> MarketingSkill:
        skill = next((item for item in SKILLS if item.skill_id == skill_id), None)
        if skill is None:
            raise ValueError("agent_skill_not_found")
        return skill

    def list(self, *, now: datetime) -> list[JsonObject]:
        active = {
            item.capability_id
            for item in self._registry.current_descriptors(now=now)
            if item.enabled and item.readiness.ready
        }
        return cast(
            "list[JsonObject]",
            [
                {
                    "skill_id": skill.skill_id,
                    "version": skill.version,
                    "purpose": skill.purpose,
                    "required_capabilities": list(skill.required_capabilities),
                    "ready": not (
                        blockers := [
                            capability
                            for capability in skill.required_capabilities
                            if capability not in active
                        ]
                    ),
                    "blockers": blockers,
                }
                for skill in SKILLS
            ],
        )

    def require_ready(self, skill_id: str, *, now: datetime) -> MarketingSkill:
        skill = self.get(skill_id)
        view = next(item for item in self.list(now=now) if item["skill_id"] == skill_id)
        if view["ready"] is not True:
            raise ValueError("agent_skill_not_ready")
        return skill


__all__ = ["SKILLS", "MarketingSkill", "MarketingSkillCatalog"]
