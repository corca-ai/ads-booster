from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from trace_capture.candidate_generation.models import CandidateContextBundle

SYSTEM_INSTRUCTION: Final = (
    "당신은 Trace 마케팅 게시물의 후보 생성 에이전트입니다. "
    "요청에 포함된 원리·요소·문체·사실·레퍼런스 인덱스 문서만 근거로 사용하고, "
    "설명 문장 없이 요청된 JSON 배열만 출력합니다."
)

_ROLE: Final = """당신은 Trace 마케팅 게시물의 후보 생성 에이전트입니다.
아래에 첨부한 context 문서(원리, 요소, 문체, 사실, 레퍼런스 인덱스)를 유일한 근거로 삼아
KR(한국) 게시물 후보 {count}개를 서로 다른 주제로 만드세요."""

_RULES: Final = """[반드시 지킬 규칙]
1. FACTS 문서에 없는 검증 가능한 사실을 주장하지 마세요.
   확정되지 않은 내용은 캡션에서 단정하지 마세요.
2. 면책성 괄호 문구(예: "(개인 경험입니다)" 같은 보험용 덧붙임)를 쓰지 마세요.
3. 반말/존댓말과 어조는 VOICE 문서를 그대로 따르세요. 스스로 문체를 새로 정하지 마세요.
4. refs_used에는 레퍼런스 INDEX 문서에 실제로 존재하는 id만 넣으세요. 없으면 빈 배열로 두세요.
5. principles_applied에는 원리 문서에서 실제로 사용한 원리 번호만 넣으세요.
6. {count}개 후보의 주제(topic)는 서로 겹치지 않아야 합니다."""

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
    "appium_prompt": "이미지 생성 지시 텍스트"
  }}
]

appium_prompt는 아래 항목을 사람이 읽을 수 있는 텍스트 블록으로 담으세요.
- 입력_일정: 잠금화면에 보일 일정 문자열 5~7개
- 기기_시각: 화면에 표시할 시각
- 배경화면: 소재와 무드
- 언어: 화면에 쓸 언어
- 정지/영상: 정지 이미지인지 영상인지"""

_DOCUMENT_HEADER: Final = "[context 문서: {relative_path}]"

_RETRY: Final = """직전 응답은 형식 검증을 통과하지 못했습니다.
검증 오류: {detail}
같은 요구사항으로 다시 만들되, 이번에는 JSON 배열만 정확한 형식으로 출력하세요."""


def build_instruction(bundle: CandidateContextBundle, *, count: int) -> str:
    """Assemble the single generation instruction from the loaded context documents."""
    sections = [
        _ROLE.format(count=count),
        _RULES.format(count=count),
        *(
            f"{_DOCUMENT_HEADER.format(relative_path=document.relative_path)}\n{document.text}"
            for document in bundle.documents
        ),
        _OUTPUT.format(count=count),
    ]
    return "\n\n".join(sections)


def build_retry_instruction(detail: str) -> str:
    """Assemble the follow-up turn that reports why the first response was rejected."""
    return _RETRY.format(detail=detail)
