from __future__ import annotations

from dataclasses import dataclass
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
    from collections.abc import Sequence

    from ads_booster.candidate_generation.models import CandidateContextBundle

DEFAULT_COUNTRY: Final = "KR"
DEFAULT_LANGUAGE: Final = "ko"

_ROLE: Final = """당신은 Trace 마케팅 게시물의 후보 생성 에이전트입니다.
아래에 첨부한 context 문서(원리, 요소, 문체, 사실, 레퍼런스 본문과 인덱스)를 유일한 근거로 삼아
{country} 게시물 후보 {count}개를 서로 다른 주제로 만드세요."""

_ROLE_ONE: Final = """당신은 Trace 마케팅 게시물의 후보 생성 에이전트입니다.
아래에 첨부한 context 문서(원리, 요소, 문체, 사실, 레퍼런스 본문과 인덱스)를 유일한 근거로 삼아
{country} 게시물 후보 1개를 만드세요."""

_RULES: Final = """[반드시 지킬 규칙]
1. FACTS 문서에 없는 검증 가능한 사실을 주장하지 마세요.
   확정되지 않은 내용은 캡션에서 단정하지 마세요.
2. 면책성 괄호 문구(예: "(개인 경험입니다)" 같은 보험용 덧붙임)를 쓰지 마세요.
3. 반말/존댓말과 어조는 VOICE 문서를 그대로 따르세요. 스스로 문체를 새로 정하지 마세요.
4. refs_used에는 레퍼런스 INDEX 문서에 실제로 존재하는 id만 넣으세요. 없으면 빈 배열로 두세요.
5. principles_applied에는 원리 문서에서 실제로 사용한 원리 번호만 넣으세요.
   최소 1개는 반드시 넣으세요. 아무 원리도 대지 못하는 후보는 근거가 없는 후보입니다.
6. {distinct}
7. 배경화면은 세 필드로 정합니다. 셋은 서로 다른 글이고, 하나를 줄이거나 늘려서
   나머지를 만들면 안 됩니다.
   - background_subject: 기계가 읽는 토큰입니다.
     그 페르소나가 실제로 잠금화면에 설정해뒀을 법한 배경을 아래 목록에서만
     고르세요. 토큰 외의 값이나 새 단어를 만들지 마세요.
     {subjects}
   - background_search_query: 이미지 검색에 그대로 들어갈 **이름**입니다.
   - background_mood: 사람이 읽는 **장면 묘사**입니다.
   query 를 먼저 정하고 mood 를 그다음에 쓰세요. 이름이 정해져야 그 화면이 어떻게
   생겼는지 쓸 수 있습니다. 반대로 하면 방금 쓴 묘사를 검색어로 옮겨 적게 됩니다.
8. image_inputs.background_search_query 는 "그 사람의 직업이나 상황을 묘사하는 사진"이
   아니라 "그 사람이 자기 폰 배경화면으로 저장해뒀을 사진"을 찾는 검색어입니다.
   200자 안에서 쓰고, 이 필드에 한해 실존 인물명·캐릭터명·팀명·아이돌 그룹명을 그대로 써도 됩니다.
   실존 인물명·캐릭터명·팀명을 쓰는 자리는 image_inputs.background_search_query 하나뿐입니다.
   - 직업 소품·작업 공간·물건 나열형 검색어는 금지입니다. 아무도 자기 일터를 찍은 정물
     사진을 배경화면으로 깔지 않습니다. 그런 검색어는 스톡 사진과 블로그 홍보 컷만
     불러옵니다.
     나쁜 예: "병원 사물함 간호사 명찰 볼펜 사진", "개인 카페 에스프레소 머신 새벽 불빛"
   - background_subject와 정합해야 합니다. scenery면 풍경, pet이면 반려동물,
     character_*면 그 캐릭터, sports_team이면 그 팀이나 선수를 찾는 검색어여야 합니다.
   - 좋은 예: 간호사 페르소나 → "고양이 배경화면" 또는 "제주 바다 노을 배경화면",
     기아 팬 → "김도영 직캠", 수험생 → "쿠로미 배경화면".
     위 예시는 형식 참고용입니다. 예시의 팀·인물·캐릭터를 그대로 쓰지 말고 새로 정하세요.
   - 직업과 하루는 trace_items와 캡션이 드러냅니다. 배경화면이 드러내는 것은 그 사람의
     취향입니다. 두 자리에 같은 내용을 겹쳐 쓰지 마세요.
   - "감성 배경", "예쁜 사진" 같은 범용 검색어도 금지입니다. "고화질", "HD" 같은
     해상도 단어도 붙이지 마세요. 그 단어는 데스크톱 월페이퍼 수집 사이트로 검색을
     밀어서 1920x1080 가로 사진만 돌아옵니다.
   - 검색어는 2~4단어입니다. "고유명사 + 배경화면" 골격으로 쓰고, 장면을 묘사하는
     문장을 만들지 마세요. 이미지 검색은 문장을 이해하지 못하고 단어가 늘어날수록
     결과가 뉴스 기사 사진 쪽으로 밀립니다. 그런 사진은 가로로 길고 작아서 잠금화면에
     쓸 수 없고, 해상도 관문에서 한 장도 남지 않습니다.
     나쁜 예: "KIA 타이거즈 야간 경기장 외야 관중석 배경화면"
     좋은 예: "KIA 타이거즈 배경화면"
   - **background_mood 를 바꿔 쓰지 마세요.** mood 가 "해 질 무렵 미끄럼틀로 달려가는
     두 아이의 뒷모습"이라고 해서 검색어가 "노을 진 놀이터에서 뛰는 아이들 뒷모습"이
     되면 안 됩니다. 그것은 같은 문장을 두 번 쓴 것이고, 이미지 검색은 그 문장에서
     워터마크 박힌 클립아트와 스톡 모델 사진을 돌려줍니다.
9. image_inputs.background_mood 는 "감성적", "예쁜" 같은 모호어 대신 실제로 보이는 것을
   40자 안에서 구체적으로 쓰세요. 예: "늦은 밤 책상 위 스탠드 불빛".
   이 필드는 사람이 읽는 설명입니다. 검색에는 쓰이지 않습니다.
10. image_inputs.trace_items는 잠금화면이 보여줄 **한 주**입니다. 18~22개를 만드세요
   (최소 5개, 최대 24개). 각 항목은 문자열이 아니라 객체입니다.
   {{"title": "해외출장", "day": 2, "days": 3, "time": null, "color": "F26419"}}
   - title: 화면에 뜰 이름. 주간 띠 안에 들어가야 하므로 **4~8자로 짧게** 씁니다.
   - day: 화면에 뜨는 날로부터 며칠 뒤인지. 0이 그날이고 6까지 있습니다.
   - days: 며칠에 걸치는지. 1이면 하루, 2 이상이면 여러 날을 가로지르는 띠입니다.
     day + days는 7을 넘을 수 없습니다.
   - time: "HH:MM" 또는 null. null이면 종일 항목입니다.
   - color: 아래 15색 중 하나.
     {colors}
   한 주를 세 축으로 채우세요.
   - **리듬 7~9개**: 매주 같은 요일에 오는 것. 출근·등원·수업·운동·정기 모임
   - **사건 6~8개**: 이번 주에만 있는 것. 면담·발표·병원·직관·약속
   - **기간 3~4개**: days가 2~3인 것. 출장·여행·연수·전시·휴가.
     화면을 채우는 것은 시각이 아니라 이 띠입니다.
   그리고 아래를 지키세요.
   - **시각은 3~5개에만 붙입니다.** 나머지는 time을 null로 두세요. 실제로 도달이 컸던
     화면은 대부분 종일 항목이었고, 시각이 붙은 것은 몇 개뿐이었습니다.
   - **하루에 몰지 마세요.** day가 전부 0인 배치는 거부됩니다. 어떤 날은 4~5개, 어떤
     날은 1개로 불균등하게 두세요. 몰린 날에 "+3" 표시가 뜨고 그것이 화면을 살아 있게
     만듭니다. 고르게 나누면 달력이 아니라 격자로 보입니다.
   - **color는 4~5가지를 섞으세요.** 한 색으로 채우면 밋밋합니다. 성격이 같은 것끼리
     같은 색을 주세요 — 일은 한 색, 가족은 한 색, 운동은 한 색.
   - 직무 작업을 일정으로 늘어놓지 마세요. 잠금화면에 뜨는 것은 그 사람의 생활이지
     업무 티켓이 아닙니다. 일은 "출근", "퇴근", "야근", "당직" 정도의 덩어리로만
     등장하고, 그런 항목은 많아야 하나입니다.
     나쁜 예(title): "PR 리뷰", "staging 배포 확인", "알림 API 리팩터링",
     "월말 결산 마감 처리", "3분기 실적 자료 취합"
     좋은 예(title): "기아전 직관", "첫째 재우기", "한강 러닝", "본공 티켓팅", "해외출장"
10-1. image_inputs.trace_todos는 날짜도 시각도 없는 **할일** 8~12개입니다(최대 20개).
   일정이 아니라 잡무입니다 — 집세 내기, 치과 예약, 보험 갱신, 택배 부치기, 카드값 확인.
   여기서는 제목이 길어도 됩니다. 목록 칸에 세로로 그려지기 때문입니다. 실제로 도달이
   가장 컸던 화면에는 "번개장터 팔린 옷 택배 접수" 같은 항목이 들어 있었습니다.
11. image_inputs 바깥의 persona_domain 필드에는 이 지시문이 이 후보에 정해준 토큰을
   그대로 적으세요. 계정 블록이 있으면 그 계정의 도메인이고, 없으면 아래 "도메인 배정"이
   후보별로 지정한 토큰입니다. 정해지지 않은 토큰이나 새 단어를 쓰면 안 됩니다.
12. 캡션은 앱을 만든 사람의 목소리가 아니라 앱을 쓰는 사람의 목소리입니다.
   - 제품·개발 용어를 캡션에 쓰지 마세요. 배포, PR, 커밋, 리팩터링, staging, API,
     스프린트, 온보딩 같은 말은 등장하지 않습니다.
   - "만들었다", "출시했다", "업데이트했다" 같은 메이커 화법을 쓰지 마세요. 이 계정의 화자는
     만든 사람이 아니라 쓰는 사람이기 때문입니다. 코퍼스에는 메이커 계정의 히트가 오히려
     많지만(KR 26편 중 18편), 그것은 실제로 만든 사람이 자기 이름으로 쓴 글입니다.
     이 계정은 그 자리가 아닙니다.
   - 죽는 것은 메이커라는 신분이 아니라 기능 나열과 여백 없는 마무리입니다. 기능을 전부
     늘어놓고 "한 번 써봐"로 닫은 kr-032는 relative 0.042로 죽었습니다. 같은 계정이 같은
     서사를 반복해도 후속이 35배 급락합니다(kr-020 → kr-029).
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
   - 코퍼스에서 도달이 가장 높았던 kr-001(relative 175.30)에는 일정 나열이 없습니다.
15. 하루가 달라지는 자리에 이 사람이 한 동작 하나가 스치게 하세요.
   - FACTS 문서의 [캡션이 써도 되는 동작]에서 **하나만** 고릅니다. 둘을 넣으면 기능 소개가
     되고, 하나도 없으면 이 계정이 무엇으로 달라졌는지 읽는 사람이 짚을 수 없습니다.
   - 한 제품의 기능을 셋 이상 늘어놓은 게시물 5편은 규모 대비 성과가 전부 미달했고
     반례가 없습니다(0.78·0.45·0.32·0.10·0.08배). 여러 제품을 N개 소개하는 리스티클은
     여기 해당하지 않습니다(2.88배). 죽이는 것은 나열이 아니라 한 제품의 기능 명세입니다.
   - 그 동작을 설명하지 마세요. 그 순간에 그 사람이 한 일로 한 줄 지나가면 됩니다.
   - FACTS의 [절대 쓰면 안 되는 문장]에 걸리는 서술은 쓰지 마세요. 특히 잠금화면 배경과
     잠금화면 위젯은 다른 것이라, 한쪽의 성질을 다른 쪽에 얹으면 거짓이 됩니다.
16. 요금제는 캡션의 소재가 아닙니다.
   - 항목마다 무료·유료를 구분해 나열하지 마세요. 요금제 설명은 캡션이 할 일이 아닙니다.
   - FACTS에서 유료로 표시된 동작을 쓸 때만, 그 문장 안에서 돈을 내고 쓰고 있다는 사실이
     자연스럽게 드러나게 하세요. 예: "결제하고 쓰는 중인데", "유료 켜고부터는".
   - 무료로 되는 일을 굳이 "무료로 된다"고 쓰지 마세요.
17. 앱 이름을 캡션에 쓰지 마세요.
   - 제품은 이미지와 동작으로 드러납니다. 가리켜야 할 때는 "이거"로 충분합니다.
   - kr-026은 앱 이름을 첫 게시물 캡션에 넣지 않고 답글에서만 밝히면서 조회 60만을 냈습니다.
18. 캡션이 붙잡은 장면은 trace_items 안에 실물로 있어야 합니다.
   - 캡션이 "특별활동 날마다 현관에서 되돌아간다"고 썼다면, 일정 어딘가에 특별활동이
     있어야 합니다. 읽는 사람이 화면에서 그것을 찾을 수 있어야 합니다.
   - 다만 캡션이 그 항목을 읽어주면 안 됩니다(규칙 14). 같은 말을 옮겨 적지 말고,
     캡션은 그 장면 주변의 이야기를 쓰세요.
   - 이미지가 증거이고 캡션이 이야기입니다. 둘이 서로를 모르면 증거가 아닙니다.
19. 줄은 짧게 끊고, 방법까지 설명하지 마세요.
   - 한 문장을 늘여 쓰지 말고 줄바꿈으로 끊으세요. 히트는 조사를 흘리고 문장을 자릅니다.
   - 어떻게 하는지를 캡션에서 다 알려주면 물어볼 자리가 없어집니다. 남겨두세요."""

EVENT_COLORS: Final = (
    "6E86F7",
    "3D73DD",
    "8A2BE2",
    "9B5DE5",
    "F9C74F",
    "F26419",
    "D62246",
    "DA4C93",
    "B598F9",
    "00B4D8",
    "5FBDB0",
    "2D936C",
    "FF9E00",
    "FF6B6B",
    "AF3B6E",
)
_COLOR_LINE: Final = " · ".join(EVENT_COLORS)

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
1. 입력_일정(image_inputs.trace_items)은 그 사람의 실제 한 주에서 나올 법한 항목으로
   파생하세요. title에 고유명사·장소가 드러나되 띠 안에 들어가게 짧아야 합니다.
   예: {{"title": "기아전 직관", "day": 3, "days": 1, "time": "18:30", "color": "D62246"}},
   {{"title": "본공 티켓팅", "day": 1, "days": 1, "time": "20:00", "color": "DA4C93"}},
   {{"title": "토익 LC 모의", "day": 2, "days": 1, "time": null, "color": "00B4D8"}},
   {{"title": "한강 러닝", "day": 0, "days": 1, "time": "05:50", "color": "2D936C"}},
   {{"title": "제주 워크샵", "day": 4, "days": 3, "time": null, "color": "F9C74F"}}.
   "회의", "운동", "공부", "약속" 같은 범용 항목은 금지입니다.
   위 예시는 형식 참고용입니다. 예시의 팀·인물·캐릭터를 그대로 쓰지 말고 새로 정하세요.
2. 기기_시각(image_inputs.device_time)은 그 사람의 생활 리듬과 맞아야 합니다.
   새벽 러너와 야근하는 직장인의 잠금화면 시각은 같을 수 없습니다.
3. 배경은 그 사람의 직업을 설명하는 자리가 아니라 취향이 드러나는 자리입니다.
   간호사라고 해서 병원 사진, 카페 사장이라고 해서 카페 사진을 깔지 않습니다.
   고유명사는 background_search_query 에만 쓰고, background_mood와 topic에는 넣지 마세요.
   그 둘은 사람이 읽는 설명이라 일반 명사로 씁니다. 일정 제목과 캡션 본문 안에서는 팬 활동
   맥락의 자연스러운 언급이 허용됩니다.
   (세 배경 필드를 각각 어떻게 쓰는지는 위 [반드시 지킬 규칙] 7~9 에 있습니다.
   같은 필드를 두 곳에서 지시하면 어느 쪽을 따를지가 우연에 맡겨집니다.)
4. 캡션의 화자는 그 사람 본인입니다. 검증 가능한 제품 사실은 FACTS 문서에서만 가져오세요."""

_DISTINCT_MANY: Final = (
    "{count}개 후보의 주제(topic)는 서로 겹치지 않아야 합니다. "
    "아래 [후보별 배정]이 후보마다 다른 출발점을 정해 두었으니 그대로 따르세요."
)
_DISTINCT_ONE: Final = (
    "주제(topic)는 아래 [최근 생성된 후보 목록]의 어느 것과도 겹치지 않아야 합니다."
)
_DISTINCT_SOLO: Final = "주제(topic)는 이 계정이 이미 쓴 소재와 겹치지 않아야 합니다."

_OUTPUT: Final = """[출력 형식]
설명, 머리말, 코드펜스 없이 JSON 객체 하나만 출력하세요.
객체는 candidates 키 하나만 가지고, 그 배열은 정확히 {count}개의 후보 객체를 담습니다.
각 후보 객체는 아래 키만 가집니다. 하나도 빠뜨리지 마세요.

{{
  "candidates": [
    {{
      "topic": "한 줄 주제/컨셉 (200자 이내)",
      "country": "{country}",
      "posting_slot": "morning 또는 evening 또는 manual",
      "persona_domain": "배정받은 도메인 토큰",
      "caption": "실제 게시할 캡션 전문",
      "hypothesis": "이 후보가 통할 것이라고 보는 이유 한 문장",
      "refs_used": ["INDEX에 존재하는 레퍼런스 id"],
      "principles_applied": [1, 4],
      "appium_prompt": "검수자용 한 문장 촬영 메모",
      "image_inputs": {{
        "trace_items": [
          {{"title": "한강 러닝", "day": 0, "days": 1, "time": "05:50", "color": "2D936C"}},
          {{"title": "통계학 2교시", "day": 0, "days": 1, "time": "09:00", "color": "00B4D8"}},
          {{"title": "학과 MT", "day": 4, "days": 3, "time": null, "color": "F9C74F"}},
          {{"title": "스터디", "day": 2, "days": 1, "time": null, "color": "00B4D8"}},
          {{"title": "할머니 생신", "day": 5, "days": 1, "time": null, "color": "DA4C93"}}
        ],
        "trace_todos": ["기숙사비 납부", "동아리 회비 이체", "안경 도수 바꾸기"],
        "device_time": "07:20",
        "background_subject": "scenery",
        "background_search_query": "제주 바다 노을 배경화면",
        "background_mood": "늦은 밤 책상 위 스탠드 불빛",
        "language": "{language}"
      }}
    }}
  ]
}}

이 블록은 형식만 정합니다. 각 필드에 무엇을 쓰는지는 위 규칙들이 정하고,
여기서는 다시 설명하지 않습니다. 두 곳의 설명이 어긋나면 어느 쪽을 따를지가
우연에 맡겨지기 때문입니다.

appium_prompt는 검수자가 읽는 한 문장 촬영 메모입니다. 촬영은 image_inputs만 쓰므로
일정·배경·검색어·시각을 여기에 옮겨 적지 마세요.

posting_slot은 이 캡션이 아침에 올라갈 글인지 저녁에 올라갈 글인지 고르세요.
어느 쪽도 아니면 manual 입니다.

persona_domain은 이 후보에 정해진 토큰 그대로여야 합니다
(계정 블록이 있으면 그 계정의 도메인, 없으면 [도메인 배정]의 배정값).

image_inputs의 각 필드는 위 예시의 형식을 그대로 따릅니다. 내용은 규칙이 정합니다 —
일정과 할일은 규칙 10·10-1, 세 배경 필드는 규칙 7~9.
language 는 이 배치에서는 "{language}" 를 그대로 쓰세요."""


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
    RECOMMEND = "recommend"


_FORM_LABELS: Final = {
    CaptionForm.DAILY: "일상글",
    CaptionForm.HOOK: "훅글",
    CaptionForm.TESTIMONY: "간증글",
    CaptionForm.RECOMMEND: "추천글",
}

_FORM_GUIDANCE: Final = {
    CaptionForm.DAILY: (
        "예전에 어땠는지 먼저 쓰고 요즘은 어떤지로 넘어가세요. 그 사이에 무엇이 달라졌는지가 "
        "이 글의 전부입니다. 제품을 설명하지 말고 하루가 보이게 합니다."
    ),
    CaptionForm.HOOK: (
        "질문이나 한 문장 반전으로 열고 본문은 짧게 끊으세요. 첫 줄이 다음 줄을 읽게 만들어야 "
        "합니다. 근거 레퍼런스: kr-001, kr-003, kr-014."
    ),
    CaptionForm.TESTIMONY: (
        "쓰기 전과 후에 무엇이 달라졌는지 씁니다. 없어진 수고 하나를 구체로 짚으세요. "
        "다만 주장은 FACTS 문서 범위 안에서만 하세요. 근거 레퍼런스: kr-010."
    ),
    CaptionForm.RECOMMEND: (
        "쓰는 사람이 다른 사람에게 권하는 글입니다. 이 배치에서 첫 줄에 사람을 부를 수 있는 "
        "것은 이 후보 하나뿐입니다. 겪는 상황으로 부르세요(예: 아이 키우는 사람이면, 운전 "
        "많이 하는 사람이면). 기기 이름으로 부르지 마세요 — 자사 계정이 이미 쓰는 문형이라 "
        "겹칩니다. 기능을 늘어놓지 말고 달라진 지점 하나만 말한 뒤 어떻게 하는지는 남겨두세요. "
        "읽는 사람이 방법을 물어볼 자리가 남아야 합니다. 근거 레퍼런스: kr-026."
    ),
}

_FORM_EXAMPLES: Final = {
    CaptionForm.DAILY: (
        "예전엔 금요일에 못 끝낸 게 월요일 아침에야 생각났는데 / 요즘은 폰 켜면 남아 있어서 "
        "출근 전에 하나는 지운다"
    ),
    CaptionForm.HOOK: "퇴근하고 침대에서 폰 켜는 사람들 / 그 시간에 앱까지 열긴 싫잖아",
    CaptionForm.TESTIMONY: "두 달 쓰고 제일 달라진 건 / 오늘 뭐 있었나 되짚지 않는 거",
    CaptionForm.RECOMMEND: (
        "아이 키우는 사람이면 / 이거 잠금화면에 깔아둬 / 아침에 준비물 빠뜨릴 일이 없음"
    ),
}

_ASSIGNMENT_HEADER: Final = """[후보별 배정]
이 배치는 한 번에 {count}개를 씁니다. 무엇이 후보를 서로 다르게 만드는지는 코드가
후보마다 미리 정해 두었습니다. 아래 배정을 그대로 따르세요.
{lines}
- 형태는 글을 여는 방식입니다. 같은 사람이 써도 글마다 다르게 열어야 하고,
  한 배치가 전부 같은 형태로 열리면 피드가 한 장짜리 템플릿으로 읽힙니다.
  간증글과 추천글은 각각 한 배치에 많아야 하나입니다. 제품 쪽으로 도는 글이 매 편에 오면
  계정이 광고가 됩니다.
- 도메인은 persona_domain 필드에 그대로 적습니다. 그 후보에 적힌 토큰이어야 하고,
  정해지지 않은 토큰이나 새 단어를 쓰면 안 됩니다.
- 소재 축은 그 후보가 출발할 자리입니다.
  캡션에 옮겨 적을 문구가 아니라, 이 사람의 하루 중 그 축과 닿는 장면 하나를 골라
  거기서 글을 시작하라는 뜻입니다.

각 형태를 어떻게 쓰는지는 아래와 같습니다.
{forms}"""

_ASSIGNMENT_LINE: Final = "- 후보 {index}: 형태 {form} ({form_label}) · 도메인 {domain} ({label})"
_ASSIGNMENT_INTEREST: Final = " · 소재 축 {interest}"
_FORM_GUIDE_LINE: Final = "- {token} ({label}): {guidance}\n  예: {example}"


@dataclass(frozen=True, slots=True)
class CandidateAssignment:
    """What one candidate in the batch was told to be, decided before the call goes out.

    The batch is one provider call now, so nothing separates the candidates except what the
    instruction says about each of them. Keeping the three together — and bound to a
    position in the output array — is what lets the parse step hold the answer to it.
    """

    domain: CandidatePersonaDomain
    form: CaptionForm
    # The account's own interest this candidate starts from. `None` when the account
    # recorded none, which is honest: there is nothing to divide the batch by.
    interest: str | None = None


def assign_interests(interests: Sequence[str], count: int) -> tuple[str | None, ...]:
    """Give each candidate its own subject axis out of the account's interests.

    Cycling rather than failing when there are fewer interests than candidates: two
    candidates on the same axis is a weaker guarantee than two on different ones, not a
    broken batch. An account with no interests gets no axis at all, and the batch falls
    back to the domain and the form to keep its candidates apart.
    """
    if not interests:
        return (None,) * count
    return tuple(interests[index % len(interests)] for index in range(count))


def assign_candidates(
    domains: Sequence[CandidatePersonaDomain],
    interests: Sequence[str] = (),
) -> tuple[CandidateAssignment, ...]:
    """Bind a form and a subject axis to each of the batch's already-chosen domains."""
    forms = assign_caption_forms(len(domains))
    axes = assign_interests(interests, len(domains))
    return tuple(
        CandidateAssignment(domain=domain, form=forms[index], interest=axes[index])
        for index, domain in enumerate(domains)
    )


_RECOMMENDATION_MINIMUM_BATCH: Final = 4


def assign_caption_forms(count: int) -> tuple[CaptionForm, ...]:
    """Give each candidate a form, with at most one testimonial and one recommendation.

    Testimony and the recommendation are the two forms that turn toward the product — one
    says what changed, the other says try it — so both are capped rather than cycled: a
    batch of four of either is an ad break, not an account. The rest alternate between the
    hook and the day, which is enough to keep the captions from opening the same way. The
    assignment is a function of the count alone, so the same batch size always gets the
    same shape and a reviewer can predict what they are reading.
    """
    if count <= 0:
        return ()
    if count == 1:
        return (CaptionForm.HOOK,)
    # The recommendation only earns a slot once the batch is wide enough to carry a
    # testimonial beside it and still open on something that is not about the product.
    capped: tuple[CaptionForm, ...] = (
        (CaptionForm.RECOMMEND, CaptionForm.TESTIMONY)
        if count >= _RECOMMENDATION_MINIMUM_BATCH
        else (CaptionForm.TESTIMONY,)
    )
    alternating = tuple(
        CaptionForm.HOOK if index % 2 == 0 else CaptionForm.DAILY
        for index in range(count - len(capped))
    )
    return (*alternating, *capped)


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
아래는 새로 만들 인물이 아니라 이미 운영 중인 계정입니다. 이 후보는 이 사람의
하루에서 나와야 합니다. 정체성을 새로 지어내지 말고, 소재만 새로 고르세요.

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
    del count
    return _ACCOUNT_HEADER.format(
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


def build_instruction(  # noqa: PLR0913 - each argument is one independent prompt section.
    bundle: CandidateContextBundle,
    *,
    assignments: tuple[CandidateAssignment, ...],
    country: str = DEFAULT_COUNTRY,
    language: str = DEFAULT_LANGUAGE,
    history: tuple[CandidateHistoryEntry, ...] = (),
    account: CandidateAccountBrief | None = None,
    learned_feedback: tuple[str, ...] = (),
) -> str:
    """Assemble the one generation instruction this batch is written from.

    `assignments` is what makes the candidates different from each other, decided by code
    before the call goes out: a caption form, a persona domain and a subject axis, one set
    per candidate, bound positionally to the output array. A batch left to differentiate
    itself writes the same post `count` times and reports variety, which is the failure this
    section exists to prevent — and stating it per candidate is what lets the parse step
    check that the model actually obeyed.

    `history` shows the model what has already been written for this account, so a batch
    does not repeat what last week's batch said. It arrives from the control plane rather
    than from a local store.

    `account` replaces the invent-a-person half of the job: when the batch is written for
    an existing account, the identity-invention block goes away — telling a batch to author
    a fresh person and to stay inside an existing one at the same time is the contradiction
    that put a stranger in every account's captions. What survives both paths is the craft:
    how a schedule, a clock and a wallpaper are derived from whoever is writing.

    `country` and `language` are the request's, not constants, because the drafts are held
    to exactly those two values downstream. Stating them here is what keeps a batch from
    coming back labelled for a country it was never asked to write.
    """
    count = len(assignments)
    subjects = ", ".join(subject.value for subject in CandidateBackgroundSubject)
    one = count == 1
    distinct = _DISTINCT_MANY.format(count=count)
    if one:
        # Naming a section that is not in the prompt would be an instruction to look at
        # nothing, so a lone candidate points at the history only when it has one.
        distinct = _DISTINCT_ONE if history else _DISTINCT_SOLO
    sections = [
        (_ROLE_ONE.format(country=country) if one else _ROLE.format(count=count, country=country)),
        _RULES.format(subjects=subjects, distinct=distinct, colors=_COLOR_LINE),
        *([_INVENT_IDENTITY] if account is None else []),
        _CRAFT,
        *([account_section(account, count=count)] if account is not None else []),
        *([_feedback_section(learned_feedback)] if learned_feedback else []),
        assignment_section(assignments),
        *([_history_section(history)] if history else []),
        *(
            f"{_DOCUMENT_HEADER.format(relative_path=document.relative_path)}\n{document.text}"
            for document in bundle.documents
        ),
        _OUTPUT.format(count=count, country=country, language=language),
    ]
    return "\n\n".join(sections)


def _feedback_section(instructions: tuple[str, ...]) -> str:
    """Render only promoted caption rules; private review notes never enter this section."""
    lines = "\n".join(
        f"{index}. {instruction}" for index, instruction in enumerate(instructions, 1)
    )
    return (
        "[이 계정의 검수에서 누적된 규칙]\n"
        "아래 규칙은 같은 계정·프로필의 반복된 강한 반려에서 승격되었습니다. 모두 지키세요.\n"
        f"{lines}"
    )


def assignment_section(assignments: tuple[CandidateAssignment, ...]) -> str:
    """State what each candidate in this batch was assigned, and how to write each form."""
    lines = "\n".join(
        _ASSIGNMENT_LINE.format(
            index=index,
            form=assignment.form.value,
            form_label=_FORM_LABELS[assignment.form],
            domain=assignment.domain.value,
            label=PERSONA_DOMAIN_LABELS[assignment.domain],
        )
        + (
            ""
            if assignment.interest is None
            else _ASSIGNMENT_INTEREST.format(interest=assignment.interest)
        )
        for index, assignment in enumerate(assignments, start=1)
    )
    # Only the forms this batch actually uses are explained. A guide for a form nobody was
    # assigned is an invitation to write it anyway.
    used = [form for form in CaptionForm if any(item.form is form for item in assignments)]
    forms = "\n".join(
        _FORM_GUIDE_LINE.format(
            token=form.value,
            label=_FORM_LABELS[form],
            guidance=_FORM_GUIDANCE[form],
            example=_FORM_EXAMPLES[form],
        )
        for form in used
    )
    return _ASSIGNMENT_HEADER.format(count=len(assignments), lines=lines, forms=forms)


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
