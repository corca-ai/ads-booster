from __future__ import annotations

from enum import StrEnum, unique
from typing import TYPE_CHECKING, Final

from ads_booster.workspace import (
    PERSONA_DOMAIN_LABELS,
    CandidateAccountBrief,
    CandidateBackgroundSubject,
    CandidateHistoryEntry,
    CandidatePersonaDomain,
)

if TYPE_CHECKING:
    from ads_booster.candidate_generation.models import CandidateContextBundle

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
6. {count}개 후보의 주제(topic)는 서로 겹치지 않아야 합니다.
7. image_inputs.background_subject는 그 페르소나가 실제로 잠금화면에 설정해뒀을 법한 배경을
   아래 토큰 중에서 고르세요. 토큰 외의 값이나 새 단어를 만들지 마세요.
   {subjects}
8. image_inputs.background_mood는 "감성적", "예쁜" 같은 모호어 대신 실제로 보이는 것을
   40자 안에서 구체적으로 쓰세요. 예: "늦은 밤 책상 위 스탠드 불빛".
9. image_inputs.trace_items는 잠금화면에 실제로 뜰 일정 문자열이며 5~7개를 권장합니다
   (최소 1개, 최대 8개).
   - 직무 작업을 일정으로 늘어놓지 마세요. 잠금화면에 뜨는 것은 그 사람의 생활이지
     업무 티켓이 아닙니다. 일은 "출근", "퇴근", "야근", "당직" 정도의 덩어리로만
     등장하고, 그런 항목은 많아야 하나입니다.
     나쁜 예: "22:00 PR 리뷰", "staging 배포 확인", "알림 API 리팩터링",
     "월말 결산 마감 처리", "3분기 실적 자료 취합"
     좋은 예: "19:00 기아 vs LG 직관", "21:30 첫째 재우기", "5:50 한강 러닝",
     "20:00 본공 티켓팅"
10. image_inputs.background_search_query는 "그 사람의 직업이나 상황을 묘사하는 사진"이 아니라
   "그 사람이 자기 폰 배경화면으로 저장해뒀을 사진"을 찾는 검색어입니다. 200자 안에서 쓰고,
   이 필드에 한해 실존 인물명·캐릭터명·팀명·아이돌 그룹명을 그대로 써도 됩니다.
   - 직업 소품·작업 공간·물건 나열형 검색어는 금지입니다. 아무도 자기 일터를 찍은 정물
     사진을 배경화면으로 깔지 않습니다. 그런 검색어는 스톡 사진과 블로그 홍보 컷만
     불러옵니다.
     나쁜 예: "병원 사물함 간호사 명찰 볼펜 사진", "개인 카페 에스프레소 머신 새벽 불빛"
   - background_subject와 정합해야 합니다. scenery면 풍경, pet이면 반려동물,
     character_*면 그 캐릭터, sports_team이면 그 팀이나 선수를 찾는 검색어여야 합니다.
   - 좋은 예: 간호사 페르소나 → "고양이 배경화면 고화질" 또는 "제주 바다 노을 배경화면",
     기아 팬 → "김도영 직캠", 수험생 → "쿠로미 배경화면".
     위 예시는 형식 참고용입니다. 예시의 팀·인물·캐릭터를 그대로 쓰지 말고 새로 정하세요.
   - 직업과 하루는 trace_items와 캡션이 드러냅니다. 배경화면이 드러내는 것은 그 사람의
     취향입니다. 두 자리에 같은 내용을 겹쳐 쓰지 마세요.
   - "감성 배경", "예쁜 사진" 같은 범용 검색어도 금지입니다.
11. image_inputs 바깥의 persona_domain 필드에는 이 지시문이 이 후보에 정해준 토큰을
   그대로 적으세요. 계정 블록이 있으면 그 계정의 도메인이고, 없으면 아래 "도메인 배정"이
   후보별로 지정한 토큰입니다. 정해지지 않은 토큰이나 새 단어를 쓰면 안 됩니다.
12. 캡션은 앱을 만든 사람의 목소리가 아니라 앱을 쓰는 사람의 목소리입니다.
   - 제품·개발 용어를 캡션에 쓰지 마세요. 배포, PR, 커밋, 리팩터링, staging, API,
     스프린트, 온보딩 같은 말은 등장하지 않습니다.
   - "만들었다", "출시했다", "업데이트했다" 같은 메이커 화법을 쓰지 마세요. 레퍼런스에서
     같은 잠금화면 소재라도 메이커 화법을 얹은 쪽이 도달 76배 열세였고(kr-032 대 kr-026),
     만든 사람 서사는 계정당 한 번 쓰면 후속이 35배 급락합니다(kr-020 → kr-029).
   - 직업은 이 사람의 배경이지 글의 소재가 아닙니다. 무슨 일을 하는지는 일정에서 스칠
     뿐이고, 글이 그 직업에 대한 이야기가 되면 안 됩니다.
13. 캡션은 이 사람이 자기 계정에 쓰는 생활글입니다. 연출임을 글에서 선언하지 마세요.
   - "만들어봤어", "올려봤어", "담아봤어", "세팅해봤어" 같은 시연 동사를 쓰지 마세요.
     그 동사는 하루를 사는 사람이 아니라 화면을 만든 사람의 자리에서 나옵니다. 한 번
     나오는 순간 글 전체가 제품 소개가 됩니다.
   - 계정은 컨셉으로 운영합니다. 겪지 않은 삶이라는 사실을 글 안에서 밝히거나 암시하지
     마세요. 캡션은 그 인물의 하루로 읽혀야 합니다.
14. 일정은 이미지가 보여줍니다. 캡션이 image_inputs.trace_items를 낭독하지 마세요.
   - 시각이 붙은 일정 나열을 캡션에 넣지 마세요. "07:20 …" 처럼 줄바꿈으로 늘어놓은
     일정표는 캡션이 아니라 잠금화면이 할 일입니다.
   - 일정 항목은 많아야 하나가 이야기 속에서 스칠 뿐입니다.
   - 이미지의 일은 증명이고 캡션의 일은 스크롤을 멈추는 것입니다. 둘이 같은 말을 하면
     캡션이 캡처 설명문이 되고, 사람은 방금 본 것을 다시 읽지 않습니다.
   - 코퍼스에서 도달이 가장 높았던 kr-001(relative 175.30)에는 일정 나열이 없습니다."""

_INVENT_IDENTITY: Final = """[정체성 창작 규칙]
이 배치에는 정해진 계정이 없습니다. 후보마다 서로 다른 "구체 정체성"을 먼저 창작하고,
그 한 사람에게서 모든 표면 디테일을 파생시키세요.
도메인을 스포츠에 몰지 말고 넓게 흩으세요. 예: 특정 프로팀 팬, 아이돌·밴드 팬덤,
특정 시험 수험생, 어린 아이를 키우는 부모, 특정 직군 직장인(간호사·개발자·미용사 등),
러닝·등산 크루, 반려동물 보호자, 자격증 준비생, 자영업자.
"야구를 좋아함", "운동을 좋아함" 수준은 금지입니다.
어느 팀의 팬인지까지 정해진 고유명사급 구체성이어야 합니다.
위 예시는 형식 참고용입니다. 예시의 팀·인물·캐릭터를 그대로 쓰지 말고 새로 정하세요."""

_CRAFT: Final = """[구체성 규칙]
아래는 그 사람이 누구든 표면 디테일을 뽑아내는 방법입니다.
1. 입력_일정(image_inputs.trace_items) 5~7개는 그 사람의 실제 일주일에서 나올 법한
   문자열로 파생하세요. 고유명사·시각·장소가 드러나야 합니다.
   예: "기아 vs LG 18:30 직관", "본공 티켓팅 20:00", "토익 LC 모의 3회", "새벽 러닝 5:50 한강".
   "회의", "운동", "공부", "약속" 같은 범용 일정은 금지입니다.
   위 예시는 형식 참고용입니다. 예시의 팀·인물·캐릭터를 그대로 쓰지 말고 새로 정하세요.
2. 기기_시각(image_inputs.device_time)은 그 사람의 생활 리듬과 맞아야 합니다.
   새벽 러너와 야근하는 직장인의 잠금화면 시각은 같을 수 없습니다.
3. image_inputs.background_subject는 위 토큰 목록 안에서 그 사람에게 맞는 것을 고르고,
   background_mood는 그 사람이 실제로 깔아뒀을 법한 화면을 구체 문구로 쓰세요.
   배경은 그 사람의 직업을 설명하는 자리가 아니라 취향이 드러나는 자리입니다.
   간호사라고 해서 병원 사진, 카페 사장이라고 해서 카페 사진을 깔지 않습니다.
   실존 인물명·캐릭터명·팀명을 쓰는 자리는 image_inputs.background_search_query 하나뿐입니다.
   그 필드에서는 고유명사를 그대로 쓰고, background_mood와 topic에는 넣지 마세요.
   (background_mood와 topic은 사람이 읽는 설명 필드이므로 일반 명사로 씁니다.)
   일정 문자열과 캡션 본문 안에서는 팬 활동 맥락의 자연스러운 언급이 허용됩니다.
4. 캡션의 화자는 그 사람 본인입니다. 검증 가능한 제품 사실은 FACTS 문서에서만 가져오세요."""

_OUTPUT: Final = """[출력 형식]
설명, 머리말, 코드펜스 없이 JSON 배열 하나만 출력하세요.
배열은 정확히 {count}개의 객체를 담고, 각 객체는 아래 키만 가집니다.

[
  {{
    "topic": "한 줄 주제/컨셉 (200자 이내)",
    "country": "KR",
    "persona_domain": "배정받은 도메인 토큰",
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
      "background_search_query": "제주 바다 노을 배경화면 고화질",
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

persona_domain은 이 후보에 정해진 토큰 그대로여야 합니다
(계정 블록이 있으면 그 계정의 도메인, 없으면 [도메인 배정]의 배정값).

image_inputs는 같은 내용을 기계가 읽는 형식으로 담습니다.
- trace_items: 일정 문자열 배열 (5~7개 권장, 각 80자 이내)
- device_time: "HH:MM" 24시간 형식
- background_subject: 위 토큰 목록 중 하나
- background_mood: 배경의 구체적 묘사 (40자 이내)
- background_search_query: 그 사람이 배경화면으로 저장했을 사진을 찾을 검색어
  (200자 이내, background_subject와 정합, 직업 정물 금지,
  이 필드에만 실존 인물명·캐릭터명·팀명 허용)
- language: 화면 언어의 두 글자 코드 (예: ko)"""


@unique
class CaptionForm(StrEnum):
    """How one caption opens, assigned per candidate the way its domain is.

    A batch left to choose for itself writes the same shape three times, and a feed of one
    shape reads as a template. The vocabulary is closed for the same reason the domain one
    is: a model free to invent forms reports variety it did not write.
    """

    DAILY = "daily"
    HOOK = "hook"
    TESTIMONY = "testimony"


_FORM_LABELS: Final = {
    CaptionForm.DAILY: "일상글",
    CaptionForm.HOOK: "훅글",
    CaptionForm.TESTIMONY: "간증글",
}

_FORM_GUIDANCE: Final = {
    CaptionForm.DAILY: (
        "이 사람의 생활 장면 하나를 그대로 쓰세요. 제품을 설명하지 말고 하루가 보이게 합니다."
    ),
    CaptionForm.HOOK: (
        "질문이나 한 문장 헤드라인으로 열고 본문은 짧게 끊으세요. "
        "근거 레퍼런스: kr-001, kr-003, kr-014."
    ),
    CaptionForm.TESTIMONY: (
        "쓰기 전과 후에 무엇이 달라졌는지 씁니다. 다만 주장은 FACTS 문서 범위 안에서만 "
        "하세요. 근거 레퍼런스: kr-010."
    ),
}

_FORM_EXAMPLES: Final = {
    CaptionForm.DAILY: "오늘도 알람 세 번 끄고 나왔는데 폰 켜니까 첫 줄이 벌써 지나 있었다",
    CaptionForm.HOOK: "다들 시험기간엔 폰 어떻게 해요?",
    CaptionForm.TESTIMONY: "두 달째 쓰는데 이제 내일 뭐부터 하는지 확인하려고 앱을 안 연다",
}

_FORM_HEADER: Final = """[캡션 형태 배정]
같은 사람이 써도 글마다 여는 방식이 달라야 합니다. 한 배치가 전부 같은 형태로 열리면
피드가 한 장짜리 템플릿으로 읽힙니다. 후보별 형태는 코드가 배정했습니다.
{lines}
간증글은 한 배치에 많아야 하나입니다. 제품 이야기가 매 글에 오면 계정이 광고가 됩니다.
형태는 무엇을 쓰느냐가 아니라 어떻게 여느냐입니다. 배정받은 도메인과 계정은 그대로 두고
형태만 이 배정을 따르세요."""

_FORM_LINE: Final = "- 후보 {index}: {token} ({label}) — {guidance}\n  예: {example}"


def assign_caption_forms(count: int) -> tuple[CaptionForm, ...]:
    """Give each candidate in the batch a form, with at most one testimonial in it.

    Testimony is the only form that makes a claim about the product, so it is capped rather
    than cycled: a batch of three testimonials is an ad break, not an account. The rest
    alternate between the hook and the day, which is enough to keep three captions from
    opening the same way. The assignment is a function of the count alone, so the same
    batch size always gets the same shape and a reviewer can predict what they are reading.
    """
    if count <= 0:
        return ()
    if count == 1:
        return (CaptionForm.HOOK,)
    alternating = tuple(
        CaptionForm.HOOK if index % 2 == 0 else CaptionForm.DAILY for index in range(count - 1)
    )
    return (*alternating, CaptionForm.TESTIMONY)


_ASSIGNMENT_HEADER: Final = """[도메인 배정]
이번 배치의 도메인은 누적 커버리지가 가장 적은 순서로 코드가 배정했습니다.
후보와 도메인은 1:1입니다. 한 도메인에 후보를 몰아넣지 마세요.
{lines}
각 후보의 persona_domain 필드에는 위에서 그 후보에 배정된 토큰을 그대로 적으세요."""

_ASSIGNMENT_LINE: Final = "- 후보 {index}: {token} ({label}) 도메인의 구체 정체성으로"

_HISTORY_HEADER: Final = """[최근 생성된 후보 목록]
아래는 이 워크스페이스가 최근에 만든 후보입니다.
이들과 소재·정체성이 겹치지 않게 새로 만드세요. 같은 팀·같은 시험·같은 캐릭터를 반복하지 마세요.
{lines}"""

_HISTORY_LINE: Final = "- [{domain}] {topic}"
_HISTORY_UNKNOWN: Final = "도메인 미기록"
_MAX_HISTORY_TOPIC_CHARS: Final = 60

_DOCUMENT_HEADER: Final = "[context 문서: {relative_path}]"

_RETRY: Final = """직전 응답은 형식 검증을 통과하지 못했습니다.
검증 오류: {detail}
같은 요구사항으로 다시 만들되, 이번에는 JSON 배열만 정확한 형식으로 출력하세요."""


_ACCOUNT_HEADER: Final = """[이 계정으로 씁니다]
아래는 새로 만들 인물이 아니라 이미 운영 중인 계정입니다. 후보 {count}개 모두 이
사람의 하루에서 나와야 합니다. 정체성을 새로 지어내지 말고, 소재만 서로 다르게 하세요.

- 이름: {display_name} ({age}세, {region})
- 직업: {occupation}
- 컨셉: {concept}
- 관심사: {interests}
- 생활 리듬: {life_rhythm}
- 배경 취향: {background_subject} / {background_mood}

위 항목은 무엇을 쓸지 고를 때 쓰는 재료입니다. 캡션에 옮겨 적을 문장이 아닙니다.

규칙:
1. persona_domain 은 {domain} 으로 고정합니다.
2. 일정(trace_items)과 기기 시각은 이 사람의 생활 리듬에서 나와야 합니다.
3. 배경 검색어는 이 사람의 배경 취향과 맞아야 합니다.
4. 문체·어미·길이는 이 블록이 정하지 않습니다. 아래 문체 문서와 레퍼런스가 정합니다.
   계정이 바뀌었다는 이유로 말투를 바꾸지 마세요.
5. 자기소개나 직업 소개로 캡션을 시작하지 마세요. 이 계정을 이미 팔로우한 사람에게
   쓰는 글이라, 매번 자기가 누구인지 밝히면 광고로 읽힙니다.
6. 컨셉 문장을 캡션에 그대로 옮겨 쓰지 마세요. 컨셉은 소재를 고르는 기준이지
   본문의 첫 줄이 아닙니다.
7. 직업은 이 사람이 어떤 시간을 사는지 알려줄 뿐, 글의 소재가 아닙니다. 직업이
   개발·기획처럼 제품을 만드는 일이라면 더욱 그렇습니다. 만드는 이야기를 쓰면
   계정이 광고로 읽히고, 레퍼런스상 그 서사는 한 번 쓰면 후속이 급락합니다.
   이 계정이 무엇을 좋아하고 하루를 어떻게 보내는지로 쓰세요."""


def account_section(account: CandidateAccountBrief, *, count: int) -> str:
    """Describe the account the batch is written as, in the terms generation must obey."""
    return _ACCOUNT_HEADER.format(
        count=count,
        display_name=account.display_name,
        age=account.age,
        region=account.region,
        occupation=account.occupation,
        concept=account.concept,
        interests=", ".join(account.interests),
        life_rhythm=account.life_rhythm,
        background_subject=account.background_subject,
        background_mood=account.background_mood,
        domain=account.domain,
    )


def build_instruction(
    bundle: CandidateContextBundle,
    *,
    count: int,
    domains: tuple[CandidatePersonaDomain, ...] = (),
    history: tuple[CandidateHistoryEntry, ...] = (),
    account: CandidateAccountBrief | None = None,
) -> str:
    """Assemble the single generation instruction from the loaded context documents.

    `domains` binds one candidate to one domain by position, and `history` shows the model
    what this workspace has already produced. Both are optional so a caller with no store
    behind it still gets a usable instruction.

    `account` replaces the invent-a-person half of the job: when the batch is written for
    an existing account, spreading it across domains would be the bug rather than the
    feature, so the per-candidate domain assignment is dropped and the account's own domain
    stands for the whole batch. The identity-invention block goes with it — telling a batch
    to author a fresh person and to stay inside an existing one at the same time is the
    contradiction that put a stranger in every account's captions. What survives both paths
    is the craft: how a schedule, a clock and a wallpaper are derived from whoever is
    writing.
    """
    subjects = ", ".join(subject.value for subject in CandidateBackgroundSubject)
    sections = [
        _ROLE.format(count=count),
        _RULES.format(count=count, subjects=subjects),
        *([_INVENT_IDENTITY] if account is None else []),
        _CRAFT,
        *([account_section(account, count=count)] if account is not None else []),
        *([_assignment_section(domains)] if domains and account is None else []),
        _form_section(assign_caption_forms(count)),
        *([_history_section(history)] if history else []),
        *(
            f"{_DOCUMENT_HEADER.format(relative_path=document.relative_path)}\n{document.text}"
            for document in bundle.documents
        ),
        _OUTPUT.format(count=count),
    ]
    return "\n\n".join(sections)


def _form_section(forms: tuple[CaptionForm, ...]) -> str:
    lines = "\n".join(
        _FORM_LINE.format(
            index=index,
            token=form.value,
            label=_FORM_LABELS[form],
            guidance=_FORM_GUIDANCE[form],
            example=_FORM_EXAMPLES[form],
        )
        for index, form in enumerate(forms, start=1)
    )
    return _FORM_HEADER.format(lines=lines)


def _assignment_section(domains: tuple[CandidatePersonaDomain, ...]) -> str:
    lines = "\n".join(
        _ASSIGNMENT_LINE.format(
            index=index,
            token=domain.value,
            label=PERSONA_DOMAIN_LABELS[domain],
        )
        for index, domain in enumerate(domains, start=1)
    )
    return _ASSIGNMENT_HEADER.format(lines=lines)


def _history_section(history: tuple[CandidateHistoryEntry, ...]) -> str:
    lines = "\n".join(
        _HISTORY_LINE.format(
            domain=(
                _HISTORY_UNKNOWN
                if entry.persona_domain is None
                else PERSONA_DOMAIN_LABELS[entry.persona_domain]
            ),
            topic=entry.topic[:_MAX_HISTORY_TOPIC_CHARS],
        )
        for entry in history
    )
    return _HISTORY_HEADER.format(lines=lines)


def build_retry_instruction(detail: str) -> str:
    """Assemble the follow-up turn that reports why the first response was rejected."""
    return _RETRY.format(detail=detail)
