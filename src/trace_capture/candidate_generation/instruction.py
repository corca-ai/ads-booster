from __future__ import annotations

from typing import TYPE_CHECKING, Final

from trace_capture.workspace import CandidateBackgroundSubject

if TYPE_CHECKING:
    from trace_capture.candidate_generation.models import (
        CandidateContextBundle,
        CandidateDocument,
        CandidateReferenceBody,
    )

SYSTEM_INSTRUCTION: Final = (
    "당신은 Trace 마케팅 게시물의 후보 생성 에이전트입니다. "
    "요청에 포함된 원리·요소·문체·사실·레퍼런스 인덱스와 레퍼런스 본문 문서만 근거로 사용하고, "
    "설명 문장 없이 요청된 JSON 배열만 출력합니다."
)

_ROLE: Final = """당신은 Trace 마케팅 게시물의 후보 생성 에이전트입니다.
아래에 첨부한 context 문서(원리, 요소, 문체, 사실, 레퍼런스 인덱스와 레퍼런스 본문)를
유일한 근거로 삼아 KR(한국) 게시물 후보 {count}개를 서로 다른 주제로 만드세요."""

_RULES: Final = """[반드시 지킬 규칙]
1. FACTS 문서에 없는 검증 가능한 사실을 주장하지 마세요.
   확정되지 않은 내용은 캡션에서 단정하지 마세요.
2. 면책성 괄호 문구(예: "(개인 경험입니다)" 같은 보험용 덧붙임)를 쓰지 마세요.
3. 반말/존댓말과 어조는 VOICE 문서를 그대로 따르세요. 스스로 문체를 새로 정하지 마세요.
4. refs_used에는 레퍼런스 INDEX 문서에 실제로 존재하는 id만 넣으세요. 없으면 빈 배열로 두세요.
5. principles_applied에는 원리 문서에서 실제로 사용한 원리 번호만 넣으세요.
6. {count}개 후보의 주제(topic)는 서로 겹치지 않아야 합니다.
7. image_inputs.background_subject는 그 페르소나가 실제로 잠금화면에 설정해뒀을 법한 배경을
   아래 토큰 중에서 고르세요. 토큰 외의 값이나 새 단어를 만들지 마세요.
   {subjects}
8. image_inputs.background_mood는 "감성적", "예쁜" 같은 모호어 대신 실제로 보이는 것을
   40자 안에서 구체적으로 쓰세요. 예: "늦은 밤 책상 위 스탠드 불빛".
9. image_inputs.trace_items는 잠금화면에 실제로 뜰 일정 문자열이며 5~7개를 권장합니다
   (최소 1개, 최대 8개)."""

_OUTPUT: Final = """[출력 형식]
설명, 머리말, 코드펜스 없이 JSON 배열 하나만 출력하세요.
배열은 정확히 {count}개의 객체를 담고, 각 객체는 아래 키만 가집니다.

[
  {{
    "topic": "한 줄 주제/컨셉 (200자 이내)",
    "country": "KR",
    "caption": "실제 게시할 캡션 전문",
    "hypothesis": "이 후보가 통할 것이라고 보는 이유 한 문장",
    "refs_used": ["INDEX에 존재하는 레퍼런스 id"],
    "principles_applied": [1, 4],
    "appium_prompt": "이미지 생성 지시 텍스트",
    "image_inputs": {{
      "trace_items": ["9:00 통계학 2교시", "13:00 스터디", "19:00 러닝"],
      "device_time": "07:20",
      "background_subject": "scenery",
      "background_mood": "늦은 밤 책상 위 스탠드 불빛",
      "language": "ko"
    }}
  }}
]

appium_prompt는 아래 항목을 사람이 읽을 수 있는 텍스트 블록으로 담으세요.
- 입력_일정: 잠금화면에 보일 일정 문자열 5~7개
- 기기_시각: 화면에 표시할 시각
- 배경화면: 소재와 무드
- 언어: 화면에 쓸 언어
- 정지/영상: 정지 이미지인지 영상인지

image_inputs는 같은 내용을 기계가 읽는 형식으로 담습니다.
- trace_items: 일정 문자열 배열 (5~7개 권장, 각 80자 이내)
- device_time: "HH:MM" 24시간 형식
- background_subject: 위 토큰 목록 중 하나
- background_mood: 배경의 구체적 묘사 (40자 이내)
- language: 화면 언어의 두 글자 코드 (예: ko)"""

_BORROWING: Final = """[레퍼런스 본문 활용 규칙]
- 아래 [레퍼런스 본문] 블록은 요약이 아니라 실제로 게시된 캡션 전문입니다. 이 캡션을 "본보기"로
  직접 차용하세요.
- 히트 캡션의 톤, 문장 구조, 훅 전개 방식(예: 1인칭 감탄, 불완전한 구어체)을 실제로 반영하되
  문장을 그대로 베끼지는 마세요.
- 차용한 구조가 PRINCIPLES 문서의 "성패를 가르지 않는 것"이나 폐기·강등 목록에 올라 있지
  않은지 교차 확인하세요. 올라 있으면 그 구조는 쓰지 마세요.
- refs_used에는 본문을 실제로 읽고 차용한 레퍼런스 id만 넣으세요. 아래에 실려 있어도 쓰지
  않은 레퍼런스는 빼도 됩니다."""

_SELECTION: Final = """당신은 Trace 마케팅 게시물의 후보 생성 에이전트입니다.
지금은 캡션을 쓰기 전 단계입니다. 아래 레퍼런스 인덱스만 보고, KR(한국) 게시물 후보 {count}개를
쓸 때 본문까지 읽어 볼 가치가 가장 큰 레퍼런스를 {minimum}~{maximum}개 고르세요.
성과가 좋았던 것, 그리고 서로 다른 훅과 구조를 보여 주는 것을 우선하세요.

[출력 형식]
설명, 머리말, 코드펜스 없이 레퍼런스 id 문자열만 담은 JSON 배열 하나만 출력하세요.
예: ["kr-001", "kr-014", "kr-032"]"""

_DOCUMENT_HEADER: Final = "[context 문서: {relative_path}]"
_REFERENCE_HEADER: Final = "[레퍼런스 본문: {reference_id}]"

_RETRY: Final = """직전 응답은 형식 검증을 통과하지 못했습니다.
검증 오류: {detail}
같은 요구사항으로 다시 만들되, 이번에는 JSON 배열만 정확한 형식으로 출력하세요."""

_SELECTION_RETRY: Final = """직전 응답은 레퍼런스 선택 형식을 통과하지 못했습니다.
검증 오류: {detail}
인덱스에 실제로 있는 레퍼런스 id 문자열만 담은 JSON 배열 하나를 다시 출력하세요."""


def build_selection_instruction(
    index: CandidateDocument,
    *,
    count: int,
    minimum: int,
    maximum: int,
) -> str:
    """Assemble the first call, which picks the references the run should read in full."""
    return "\n\n".join(
        (
            _SELECTION.format(count=count, minimum=minimum, maximum=maximum),
            f"{_DOCUMENT_HEADER.format(relative_path=index.relative_path)}\n{index.text}",
        )
    )


def build_instruction(
    bundle: CandidateContextBundle,
    *,
    count: int,
    references: tuple[CandidateReferenceBody, ...] = (),
) -> str:
    """Assemble the generation instruction from the context documents and reference bodies.

    The borrowing rules and the reference sections are added only when the selection call
    produced usable bodies, so a run without them keeps exactly its previous instruction.
    """
    subjects = ", ".join(subject.value for subject in CandidateBackgroundSubject)
    sections = [
        _ROLE.format(count=count),
        _RULES.format(count=count, subjects=subjects),
        *(
            f"{_DOCUMENT_HEADER.format(relative_path=document.relative_path)}\n{document.text}"
            for document in bundle.documents
        ),
    ]
    if references:
        sections.append(_BORROWING)
        sections.extend(
            f"{_REFERENCE_HEADER.format(reference_id=reference.reference_id)}\n{reference.text}"
            for reference in references
        )
    sections.append(_OUTPUT.format(count=count))
    return "\n\n".join(sections)


def build_retry_instruction(detail: str) -> str:
    """Assemble the follow-up turn that reports why the first response was rejected."""
    return _RETRY.format(detail=detail)


def build_selection_retry_instruction(detail: str) -> str:
    """Assemble the follow-up turn that reports why the first selection was rejected."""
    return _SELECTION_RETRY.format(detail=detail)
