"""Versioned procedures executed by the one canonical Marketing Agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ads_booster.contracts.agent_run import AgentGoal
from ads_booster.creative.creative_procedures import PROCEDURES

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.agent.core.registry import ToolRegistry
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


_BASE_SKILLS = (
    MarketingSkill(
        skill_id="research.daily_slack",
        version="2",
        purpose="오늘의 근거 기반 Trace 마케팅 기회를 조사해 Slack으로 전달한다.",
        required_capabilities=("research.web", "deliver.slack"),
        success_criteria=(
            "출처와 반증 질문을 포함한 오늘의 마케팅 브리프가 완성된다.",
            "Slack 전달 receipt가 확인된다. Notion은 별도 명시 요청이 있을 때만 정리한다.",
        ),
        procedure=(
            "1. input의 immutable research_request를 research.web에 그대로 전달한다.\n"
            "2. 확인한 조사 receipt로 출처·불확실성·반증 질문을 포함한 브리프를 작성한다.\n"
            "3. deliver.slack으로 전달하고 receipt 확인 뒤 완료한다.\n"
            "4. Notion 기록은 이 스킬의 필수 단계나 승인 범위가 아니다."
        ),
    ),
    MarketingSkill(
        skill_id="research.daily_slack_and_notion",
        version="1",
        purpose="명시적으로 요청한 Slack 전달과 Notion 일별 기록을 함께 준비한다.",
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
        skill_id="research.daily_slack_only",
        version="1",
        purpose="오늘의 마케팅 주제를 조사하고 출처가 있는 브리프를 Slack으로 전달한다.",
        required_capabilities=("research.search", "deliver.slack"),
        success_criteria=(
            "검색 출처와 불확실성을 포함한 브리프가 만들어진다.",
            "Slack 전달 receipt가 확인된다.",
        ),
        procedure=(
            "1. input.query를 시작점으로 research.search를 사용해 조사한다.\n"
            "2. 검색 요약은 원문 검증이나 제품 출시 증거가 아니다. "
            "출처 URL과 불확실성을 명시한다.\n"
            "3. 확인한 근거로 브리프를 작성하고 deliver.slack의 text로 전달한다.\n"
            "4. Slack 전달 receipt를 확인한 뒤 완료한다."
        ),
    ),
)

_MARKETING_SKILLS = (
    MarketingSkill(
        skill_id="marketing.context",
        version="1",
        purpose="팀 위키·기억·이전 근거를 조회해 현재 마케팅 요청에 필요한 맥락을 복원한다.",
        required_capabilities=("knowledge_search", "knowledge_get"),
        success_criteria=(
            "조회한 팀 근거와 현재 요청을 연결하고 검색 범위와 공백을 정확히 설명한다.",
        ),
        procedure=(
            "1. 최신 요청에서 필요한 제품 사실, 이전 결정, 브랜드 제약 또는 성과를 식별한다. "
            "현재 도구 목록에서 knowledge_search, knowledge_get, memory_get, source_read의 "
            "실제 제공 여부와 입력 스키마를 확인한다.\n"
            "2. 사용 가능한 지식 검색으로 주제·제품·캠페인을 찾아 반환된 문서 ID와 "
            "revision으로 원문을 읽는다. 기억 조회는 현재 도구의 kind 등 실제 스키마를 따른다. "
            "관련 출처는 반환된 참조로 읽고, 검색어가 좁으면 한 번 다른 표현으로 확인한다.\n"
            "3. 이미 조회한 근거로 요청한 요약·카피·추천을 작성한다. 출처, 사람의 보고, "
            "확인된 사실과 가설을 구분한다. 옛 결정이 최신 지시와 충돌하면 최신 요청을 따른다.\n"
            "4. 검색 결과 없음은 그 검색 범위에 결과가 없다는 뜻이며 전체 위키가 비었다는 "
            "뜻이 아니다. 읽기 도구가 없으면 현재 연결에서 조회할 수 없다고 짧게 설명하고 "
            "제공된 자료로 가능한 부분을 진행한다. 저장·수정했다고 주장하지 않는다. "
            "개인 대화에서는 허용된 공유 자료 읽기만 수행하며 "
            "다른 사람의 개인 기억을 조회하지 않는다."
        ),
    ),
    MarketingSkill(
        skill_id="marketing.opportunity",
        version="1",
        purpose="시장·트렌드·시즈널 신호에서 제품에 맞는 마케팅 기회를 찾고 다음 실험을 추천한다.",
        required_capabilities=("research.search",),
        success_criteria=("출처·시점·타깃 맥락으로 비교한 기회와 추천 실험이 있다.",),
        procedure=(
            "1. 현재 요청과 선택된 팀 지식에서 제품 사실, 대상 국가·채널, 기존 성과를 확인한다. "
            "이미 있는 정보를 다시 묻지 않는다. 미정인 타깃은 가정으로 표시하고 탐색한다.\n"
            "2. research.search로 대상 국가의 언어·시즌·사용 상황을 조사한다. "
            "첫 검색이 약하면 다른 관점으로 좁혀 검색한다. "
            "최신성·제품 적합성·반대 근거를 확인한다. "
            "검색 요약을 원문 검증이나 유행 규모의 증거로 단정하지 않는다.\n"
            "3. 관련 있는 기회 2~3개를 타깃, 관찰한 신호와 출처, 제품 연결, 실행 비용과 "
            "불확실성으로 비교하고 하나를 추천한다. 억지로 제품과 유행을 연결하지 않는다.\n"
            "4. 추천을 검증할 작은 실험과 관찰할 지표를 제안한다. "
            "유용한 부분 결과를 먼저 제공하고, 다음 행동을 막는 정보만 구체적으로 묻는다. "
            "현재 대화 답변은 별도 Slack 발송 도구 없이 반환한다."
        ),
    ),
    MarketingSkill(
        skill_id="marketing.strategy",
        version="1",
        purpose="신기능·검증된 포맷·캠페인의 타깃과 메시지를 비교하고 실행 가능한 추천안을 만든다.",
        required_capabilities=(),
        success_criteria=("근거에 맞는 대안·추천과 검증 가능한 다음 행동이 있다.",),
        procedure=(
            "1. 요청의 산출물과 범위를 정한다. 제품의 확인된 가치, 고객 상황, 채널·국가, "
            "기존 자산·포맷·성과를 선택된 문맥과 현재 도구로 확인한다. "
            "부족한 최신 시장 근거는 사용 가능한 검색 도구로 조사한다.\n"
            "2. 고객의 구체적인 상황 → 해결할 문제 → 확인된 제품 가치 → 보여줄 증거 → "
            "다음 행동으로 메시지를 구성한다. 제품 사실과 마케팅 가설을 분리한다.\n"
            "3. 복수 대안이 유용하면 최대 3개를 비교하고 추천 이유와 반대 근거를 적는다. "
            "나라별 맥락과 채널별 형식을 반영하며 단순 번역으로 끝내지 않는다. "
            "관찰하지 않은 수치·성공 보장은 쓰지 않는다.\n"
            "4. 추천안에 목표·타깃·핵심 메시지·필요 자산·작은 실험·검수 부담을 연결한다. "
            "간단한 문구 요청이면 바로 문구를 작성한다. 정식 제작안 저장이 필요할 때만 "
            "현재 delivery.prepare 스키마에 맞는 초안을 준비한다. 제작·게시 승인은 별개다."
        ),
    ),
    MarketingSkill(
        skill_id="marketing.copy",
        version="1",
        purpose="채널·국가·페르소나에 맞는 홍보 문구를 직접 작성·수정하고 비교 검수한다.",
        required_capabilities=(),
        success_criteria=("요청한 문구 자체와 필요한 검수 메모를 반환한다.",),
        procedure=(
            "1. 현재 요청과 이전 대안·피드백에서 언어·채널·보존할 내용·수정 범위를 확인한다. "
            "글쓰기·수정에 필요한 브랜드 문맥은 proposed_action_kind와 proposed_brand_ref로 "
            "현재 서비스에 요청한다. 확인되지 않은 브랜드를 만들지 않는다.\n"
            "2. 작업 지시서 대신 실제 초안을 쓴다. 훅·구체적인 사용 장면·확인된 제품 가치·"
            "자연스러운 다음 행동을 연결하고 채널 길이와 톤에 맞춘다. "
            "이미 충분한 정보가 있으면 불필요한 검색이나 설정 질문을 하지 않는다.\n"
            "3. 대안 요청에는 비교 가능한 문구와 추천을 준다. 부분 수정은 해당 부분만 바꾼다. "
            "현지화는 대상 언어·문화·날짜 표현을 검토하며 제품 지원 언어를 추측하지 않는다.\n"
            "4. 제품 주장, 언어 혼용, 과장, 번역투와 요청 누락을 검토하고 초안을 답변에 포함한다. "
            "이미지 제작이나 공개 게시는 문구 초안 작성과 별도 도구·승인이다."
        ),
    ),
    MarketingSkill(
        skill_id="marketing.experiment",
        version="1",
        purpose="마케팅 성과를 비교하고 반증 가능한 다음 실험과 학습 후보를 만든다.",
        required_capabilities=(),
        success_criteria=(
            "관찰과 원인 가설을 구분한 분석 및 하나의 검증 가능한 다음 실험이 있다.",
        ),
        procedure=(
            "1. 제공된 성과와 현재 조회 도구로 계정·국가·채널·기간·노출 조건·분모를 확인한다. "
            "사람이 보고한 수치와 직접 수집한 수치를 구분한다. 없는 데이터는 만들지 않는다.\n"
            "2. 비교 가능한 지표를 계산한다. 조회수가 다른 소재의 반응 수만 비교하지 않는다. "
            "기간·배포량·타깃 차이와 표본 한계를 표시한다. "
            "한 사례를 승자나 인과관계로 단정하지 않는다.\n"
            "3. 결과를 설명할 가설과 대안 설명을 제시하고, 한 변수를 바꿀 다음 실험을 추천한다. "
            "타깃·채널·대조안·관찰 지표·판단 시점·중단 조건을 구체화한다. "
            "예산과 표본 기준은 팀의 실제 제약에 맞추며 보편적 성공 수치를 발명하지 않는다.\n"
            "4. 학습은 적용 범위·근거·반증 조건을 가진 후보로 제시한다. "
            "영구 기억이나 검증된 포맷 승격은 사람의 검토를 거친다. "
            "유료 집행과 게시·공개 대응은 각자의 승인 경계를 따른다."
        ),
    ),
)


CREATIVE_SKILLS = tuple(
    MarketingSkill(
        skill_id=f"creative.{procedure.task}",
        version="2",
        purpose=procedure.purpose,
        required_capabilities=("creative.prepare",),
        success_criteria=(
            "작은 요청에 필요한 절차·보존 조건·검수·반환물을 준비한다.",
            "도구가 없으면 구체적인 사람 작업과 같은 업무 재개에 필요한 입력을 안내한다.",
        ),
        procedure=(
            f"1. creative.prepare에 task={procedure.task}와 이미 확인된 input 정보를 전달한다.\n"
            "2. inputs, preserve, change, locales를 전달한다. 캠페인 전체 설정은 불필요하다.\n"
            "3. brief는 제작·검수 완료가 아니다. 도구와 정확한 승인을 확인한다.\n"
            "4. 반환된 route가 automatic이면 현재 스냅샷의 실행 도구·입력 스키마를 확인하고 "
            "그 도구를 호출한다. 준비만 하고 멈추거나 같은 준비를 반복하지 않는다. "
            "실행 승인은 서비스가 집행한다. 결과 receipt를 확인하고 필요한 검수를 이어간다.\n"
            "5. awaiting_input이면 먼저 현재 도구로 자산·메타데이터를 조회해 빈칸을 채운다. "
            "사용자에게 내부 ID·digest를 직접 찾게 하지 않는다. 조회할 수 없는 정보만 묻는다.\n"
            "6. human_assisted이면 직접 할 수 있는 제안·문구·검토 설명을 먼저 제공한다. "
            "도구 실행이 필요한 나머지만 구체적인 사람 작업과 반환물로 안내한다. "
            "이미 충분한 요청이면 별도 준비 없이 실행 도구나 직접 답변으로 처리할 수 있다."
        ),
    )
    for procedure in PROCEDURES
)
SKILLS = (*_BASE_SKILLS, *_MARKETING_SKILLS, *CREATIVE_SKILLS)


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
