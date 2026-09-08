"""Composable creative procedures: produce a bounded brief, never claim execution."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from ads_booster.contracts.models import ContractModel, Identifier, Sha256Digest

_MAX_REGIONS = 32
_MAX_LOCALES = 8

Task = Literal[
    "mood",
    "reference",
    "background_review",
    "partial_edit",
    "font_color",
    "localization",
    "mockup",
    "final_qa",
    "partial_feedback",
]
Text = Annotated[str, Field(min_length=1, max_length=2000)]
Locale = Annotated[str, Field(pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")]


class CreativeInputs(ContractModel):
    asset_ids: Annotated[tuple[Identifier, ...], Field(max_length=8)] = ()
    source_sha256: Sha256Digest | None = None
    source_kind: (
        Literal["native_trace_capture", "edited_promotion", "background_asset", "phone_mockup"]
        | None
    ) = None
    request: Text | None = None
    source: Text | None = None
    use_terms: Text | None = None
    data_permission: Literal["synthetic", "explicitly_permitted"] | None = None
    permission_evidence: Text | None = None
    require_product_proof: bool = False


class CreativeProcedure(ContractModel):
    task: Task
    purpose: Text
    capability_id: str
    asset_required: bool = False
    creates_asset: bool = False
    guidance: tuple[Text, ...]
    quality_checks: tuple[Text, ...]
    return_requirements: tuple[Text, ...]


class CreativeBriefRequest(ContractModel):
    task: Task
    inputs: CreativeInputs = Field(default_factory=CreativeInputs)
    preserve: Annotated[tuple[Text, ...], Field(max_length=32)] = ()
    change: Annotated[tuple[Text, ...], Field(max_length=32)] = ()
    locales: Annotated[tuple[Locale, ...], Field(max_length=8)] = ()


class CreativeBrief(ContractModel):
    schema_version: Literal["trace.creative-brief.v1"] = "trace.creative-brief.v1"
    task: Task
    route: Literal["automatic", "human_assisted", "awaiting_input"]
    capability_id: str | None
    status: Literal["prepared_not_executed"] = "prepared_not_executed"
    inputs: CreativeInputs
    preserve: tuple[Text, ...]
    change: tuple[Text, ...]
    locales: tuple[Locale, ...]
    max_candidates: Literal[3] = 3
    missing_inputs: tuple[str, ...]
    guidance: tuple[Text, ...]
    quality_checks: tuple[Text, ...]
    return_requirements: tuple[Text, ...]
    boundaries: tuple[Text, ...]


_VISUAL = (
    "텍스트·달력 영역의 여백, 배경 복잡도, 글자 대비를 위치별로 확인한다.",
    "배경·캐릭터의 무드와 폰트, 일정 항목 색상의 조화를 비교한다.",
    "실제 폰 크기에서 글씨 잘림·겹침·정렬·가독성과 매력을 사람이 확인한다.",
    "울퉁불퉁한 질감·왜곡·불필요한 요소와 제품 사실의 불일치를 기록한다.",
)
_LOCALIZATION = (
    "의미와 톤, 폰트의 대상 문자 지원, 날짜·요일·시간 표현을 언어별로 확인한다.",
    "줄바꿈·넘침·정렬, 달력·일정 정보와 원본 보존 영역의 일관성을 비교한다.",
    "각 언어의 검수 상태를 따로 기록하고 요청된 언어 파생물만 수정한다.",
)
_RETURNS = (
    "원본 asset ID·revision·digest, 결과 파일·digest, 출처·사용 조건을 반환한다.",
    "변경 영역·보존 영역과 언어를 기록하고 사람 완료 보고와 직접 검증을 구분한다.",
)

PROCEDURES = (
    CreativeProcedure(
        task="mood",
        purpose="홍보 소재와 무드 제안",
        capability_id="creative.mood",
        guidance=("요청과 기존 제품 맥락으로 소재·무드 2~3개, 추천과 이유를 제시한다.",),
        quality_checks=("선호를 해당 작업 범위로 한정하고 실험 가설과 제품 사실을 구분한다.",),
        return_requirements=("비교 가능한 후보·추천·필요한 입력을 반환한다.",),
    ),
    CreativeProcedure(
        task="reference",
        purpose="레퍼런스 검색과 비교",
        capability_id="research.search",
        guidance=("소재·무드에 맞는 출처 링크를 찾고 여백·복잡도·대비로 최대 3개를 비교한다.",),
        quality_checks=("검색 요약과 실제 열어본 이미지의 시각 관찰을 구분한다.",),
        return_requirements=("출처 링크·선정 이유·사용 조건의 확인 여부를 반환한다.",),
    ),
    CreativeProcedure(
        task="background_review",
        purpose="배경 적합성 검토",
        capability_id="creative.image.review",
        asset_required=True,
        guidance=("이미지를 실제 열고 달력·텍스트 예정 위치의 문제와 부분 수정안을 제시한다.",),
        quality_checks=_VISUAL,
        return_requirements=("문제 위치·수정 대안·추천·미확인 항목을 반환한다.",),
    ),
    CreativeProcedure(
        task="partial_edit",
        purpose="배경 여백과 구도 부분 편집",
        capability_id="creative.image.edit",
        asset_required=True,
        creates_asset=True,
        guidance=(
            "보존 영역은 고정한다. 여백만 확장하며 캐릭터·달력·글씨는 재생성하지 않는다.",
            "벡터 스타일은 질감 개선 실험 후보이며 편집 가능한 벡터 파일과 구분한다.",
        ),
        quality_checks=(
            *_VISUAL,
            "원본과 보존 영역을 비교하고 변경 범위 밖 차이가 있으면 검수에 표시한다.",
        ),
        return_requirements=_RETURNS,
    ),
    CreativeProcedure(
        task="font_color",
        purpose="폰트와 테마 색상 제안",
        capability_id="creative.font_color",
        asset_required=True,
        guidance=("무드에 맞는 폰트·일정 색상 조합 3개 이내와 문자 지원·가독성·대비를 설명한다.",),
        quality_checks=(
            "실제 앱에서 선택 가능한 폰트인지 별도로 확인하고 지원 미확인을 명시한다.",
        ),
        return_requirements=(
            "폰트 이름·문자/사용 조건 확인 상태·테마 색상·적용 위치·추천을 반환한다.",
        ),
    ),
    CreativeProcedure(
        task="localization",
        purpose="기존 캡처의 다국어 확장",
        capability_id="creative.image.localize",
        asset_required=True,
        creates_asset=True,
        guidance=(
            "제품 증거는 실제 언어별 앱 캡처로 만든다. 홍보 가공은 원본 레이아웃을 유지한다.",
            "언어별 문자열·폰트·날짜를 원본과 비교한다. 한 언어 수정은 그 파생물만 만든다.",
        ),
        quality_checks=_LOCALIZATION + _VISUAL,
        return_requirements=(
            *_RETURNS,
            "언어별 원문/번역문·폰트·검수 결과와 실제 캡처/가공 구분을 반환한다.",
        ),
    ),
    CreativeProcedure(
        task="mockup",
        purpose="홍보용 실물 폰 목업",
        capability_id="creative.image.mockup",
        asset_required=True,
        creates_asset=True,
        guidance=("승인된 화면을 폰 목업에 배치하고 장면·손 자세·화면 크기를 제한한다.",),
        quality_checks=(
            *_VISUAL,
            "화면의 글씨·달력 내용을 유지하고 목업을 실제 사용 장면 증거로 취급하지 않는다.",
        ),
        return_requirements=_RETURNS,
    ),
    CreativeProcedure(
        task="final_qa",
        purpose="최종 이미지 검수",
        capability_id="creative.image.review",
        asset_required=True,
        guidance=("결과를 열어 원본과 비교한다. 정량 검사·모델 시각 평가·사람 취향을 구분한다.",),
        quality_checks=_VISUAL + _LOCALIZATION,
        return_requirements=("언어별 결함 위치·심각도·수정안·사람 검수 대기 항목을 반환한다.",),
    ),
    CreativeProcedure(
        task="partial_feedback",
        purpose="피드백에 따른 부분 수정",
        capability_id="creative.image.edit",
        asset_required=True,
        creates_asset=True,
        guidance=(
            "현재 피드백의 적용 범위와 보존 조건을 고정하고 관련 결과만 수정한다.",
            "이 작업의 취향을 모든 캠페인의 영구 규칙으로 승격하지 않는다.",
        ),
        quality_checks=(
            *_VISUAL,
            "원본 변경이면 파생물 영향을 확인하고 관련없는 언어 결과는 유지한다.",
        ),
        return_requirements=_RETURNS,
    ),
)


def build_creative_brief(  # noqa: PLR0913 - explicit preservation and locale inputs form the brief.
    task: Task,
    inputs: CreativeInputs,
    *,
    preserve: tuple[str, ...] = (),
    change: tuple[str, ...] = (),
    locales: tuple[str, ...] = (),
    ready_capabilities: frozenset[str] = frozenset(),
) -> CreativeBrief:
    """Use only a host-filtered ready catalog; this packet grants no execution authority."""
    procedure = next((item for item in PROCEDURES if item.task == task), None)
    if procedure is None:
        raise ValueError("creative_procedure_unknown")
    if len(preserve) > _MAX_REGIONS or len(change) > _MAX_REGIONS or len(locales) > _MAX_LOCALES:
        raise ValueError("creative_brief_limit_exceeded")
    if len(set(locales)) != len(locales):
        raise ValueError("creative_locale_duplicate")
    missing = _missing_inputs(procedure, inputs, preserve, change, locales)
    capability: str | None = procedure.capability_id
    guidance = procedure.guidance
    if (
        task in {"background_review", "final_qa"}
        and inputs.asset_ids
        and "creative.asset.review" in ready_capabilities
    ):
        capability = "creative.asset.review"
        guidance += ("같은 업무의 asset ID·revision·digest를 확인해 등록된 원본을 직접 검토한다.",)
    if task == "localization" and inputs.require_product_proof:
        capability = None
        guidance += ("실제 앱의 언어별 캡처가 필요하다. 이미지 텍스트 가공으로 대체하지 않는다.",)
    route: Literal["automatic", "human_assisted", "awaiting_input"] = (
        "awaiting_input"
        if missing
        else "automatic"
        if capability in ready_capabilities
        else "human_assisted"
    )
    if route == "human_assisted":
        guidance += (
            "도구가 없다. 위 절차를 사람에게 전달하고 같은 업무에서 결과를 받아 검수·재개한다.",
        )
    return CreativeBrief(
        task=task,
        route=route,
        capability_id=capability if route == "automatic" else None,
        inputs=inputs,
        preserve=preserve,
        change=change,
        locales=locales,
        missing_inputs=tuple(missing),
        guidance=guidance,
        quality_checks=procedure.quality_checks,
        return_requirements=procedure.return_requirements,
        boundaries=(
            "이 문서는 준비한 작업 지시이며 실행·품질 검증·승인 완료 증거가 아니다.",
            "실행 전 readiness·권한·비용·제작 승인을 재확인한다. 제작 승인은 게시 승인이 아니다.",
            "사람 완료 보고, 파일 무결성 검사, 모델 시각 평가, 사람 최종 검수를 구분한다.",
            "Pinterest 발견은 사용 허가가 아니다. 합성 또는 명시적으로 허용된 일정만 사용한다.",
            "가공물은 제품 기능·폰트·언어 지원 증거가 아니다. 최종 품질은 사람이 검수한다.",
        ),
    )


def _missing_inputs(
    procedure: CreativeProcedure,
    inputs: CreativeInputs,
    preserve: tuple[str, ...],
    change: tuple[str, ...],
    locales: tuple[str, ...],
) -> list[str]:
    missing: list[str] = []
    if procedure.asset_required and not inputs.asset_ids:
        missing.append("source_asset")
    if procedure.creates_asset:
        if not inputs.source_sha256:
            missing.append("source_digest")
        missing.extend(
            key
            for key in ("source", "use_terms", "data_permission", "permission_evidence")
            if getattr(inputs, key) is None
        )
    if procedure.task in {"partial_edit", "partial_feedback"}:
        if not preserve:
            missing.append("preserve_regions")
        if not change:
            missing.append("change_regions")
    if procedure.task == "localization" and not locales:
        missing.append("target_locales")
    return missing
