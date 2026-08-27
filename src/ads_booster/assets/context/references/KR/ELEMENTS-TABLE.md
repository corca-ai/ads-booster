---
country: KR
axes_version: AXES.md v1
records: 41 (kr-001 ~ kr-041)
created: 2026-08-24
updated: 2026-08-25
source: 각 레코드의 캡션 원문 + visual 블록/이미지 묘사에서 확인되는 것만 태깅
---

# KR 요소 성분표 — 레코드별 AXES 태깅

> 레코드 원본은 수정하지 않았다. 이 표는 재채굴 결과만 담는다.
> 판정("이 요소가 유효한가")은 여기 쓰지 않는다 → `context/core/ELEMENTS-KR.md`.

## 판정 규칙 (전 국가 공통)

| 기호 | 뜻 |
|---|---|
| `?` | **판정불가** — 레코드에 해당 정보가 없거나 묘사가 얕아 확인 불가. 추정하지 않음 |
| `n/a` | 해당 축이 성립하지 않음 (이미지 없는 텍스트 게시물의 이미지 5축, 기기 화면이 아닌 실물 사진의 device_realism) |

- **background_subject**: 기기 스크린샷은 *월페이퍼/배경화면의 소재*, 실물 사진은 *피사체*. `minimal`=단색·무늬 배경. `none`=배경 자체가 없거나(순수 UI/인포그래픽) AXES 값 어디에도 없는 사물·공간 소재 → 실제 소재는 열린 칸에 기술.
- **face**: 사람 얼굴만 true. 캐릭터·이모지 얼굴은 false(열린 칸에 `char:` 표기).
- **has_numbers**: 구체 수치·기간·개수가 캡션 텍스트에 명시되면 true. 리스트 번호 매김(1.2.3.)만 있으면 false.
- **app_name_shown**: 앱 이름이 **캡션 텍스트**(연결 스레드 포함)에 문자로 등장하면 true. 링크카드 제목·URL 슬러그로만 노출되면 false(열린 칸에 표기).
- **cta_type**: 캡션 기준. `comment_gate`는 댓글 요청 일반을 포함하되, 보상 조건이 붙은 진짜 게이트는 열린 칸에 표기.
- **freshness**: `first`=이 소재의 최초 게시 / `repost`=같은 이미지·문구 재탕 / `series`=같은 템플릿의 회차 갱신.

## 표

| id | out | rel | bg_subject | mood | txt_img | face | dev_real | hook | num | emoji | tone | cta | app_nm | len | fresh | seed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| kr-001 | hit | 175.30 | n/a | n/a | n/a | n/a | n/a | empathy | F | none | banmal | none | F | short | first | F |
| kr-002 | hit | 0.66 | n/a | n/a | n/a | n/a | n/a | question | F | none | banmal | comment_gate | F | short | first | F |
| kr-003 | hit | 0.49 | none | plain | none | F | n/a | question | F | none | formal | comment_gate | F | short | first | F |
| kr-004 | flop | 0.022 | n/a | n/a | n/a | n/a | n/a | declaration | F | none | mixed | none | F | long | repost | F |
| kr-005 | hit | 0.06 | none | clean | heavy | F | ? | question | T | none | mixed | direct_link | F | medium | first | F |
| kr-006 | flop | 0.02 | none | busy | heavy | F | high | demo | T | none | banmal | direct_link | T | medium | first | F |
| kr-007 | flop | 0.0013 | n/a | n/a | n/a | n/a | n/a | empathy | T | light | formal | profile | T | long | first | F |
| kr-008 | hit | 2.27 | n/a | n/a | n/a | n/a | n/a | declaration | T | none | formal | none | T | medium | first | F |
| kr-009 | hit | 2.05 | n/a | n/a | n/a | n/a | n/a | number | T | none | formal | none | T | long | first | F |
| kr-010 | hit | 2.50 | person | warm | none | T | n/a | declaration | ? | ? | banmal | ? | F | ? | first | F |
| kr-011 | hit | 0.08 | n/a | n/a | n/a | n/a | n/a | number | T | none | banmal | none | F | short | series | F |
| kr-012 | flop | 0.0015 | character_kitty | cute | heavy | F | high | question | F | none | banmal | direct_link | T | short | repost | T |
| kr-013 | hit | 1.16 | none | cute | light | F | staged | declaration | F | light | banmal | choice | F | medium | series | F |
| kr-014 | hit | 5.36 | n/a | n/a | n/a | n/a | n/a | question | T | light | banmal | comment_gate | F | medium | first | F |
| kr-015 | hit | 0.58 | none | warm | heavy | F | n/a | declaration | T | none | banmal | comment_gate | F | medium | first | F |
| kr-016 | hit | 0.26 | none | plain | light | F | ? | empathy | F | light | banmal | comment_gate | F | medium | first | F |
| kr-017 | flop | 0.056 | n/a | n/a | n/a | n/a | n/a | empathy | T | none | mixed | comment_gate | F | medium | first | F |
| kr-018 | hit | 0.325 | n/a | n/a | n/a | n/a | n/a | question | T | none | banmal | comment_gate | F | short | first | F |
| kr-019 | hit | 0.087 | n/a | n/a | n/a | n/a | n/a | declaration | T | none | banmal | none | F | long | first | F |
| kr-020 | hit | 3.57 | n/a | n/a | n/a | n/a | n/a | declaration | T | heavy | banmal | comment_gate | F | long | first | F |
| kr-021 | hit | 0.093 | ? | ? | ? | ? | ? | number | T | none | banmal | none | F | short | first | F |
| kr-022 | flop | 0.0055 | none | busy | heavy | F | staged | number | T | none | banmal | none | F | short | first | F |
| kr-023 | hit | 0.23 | ? | ? | ? | ? | ? | number | T | none | banmal | none | F | short | first | F |
| kr-024 | hit | 1.05 | n/a | n/a | n/a | n/a | n/a | number | T | light | banmal | none | F | medium | first | F |
| kr-025 | hit | 0.23 | none | clean | heavy | F | staged | declaration | T | none | banmal | none | F | short | series | F |
| kr-026 | hit | 0.82 | scenery | warm | heavy | F | high | declaration | T | none | banmal | direct_link | F | short | first | F |
| kr-027 | hit | 2.16 | minimal | dark | heavy | F | high | declaration | F | none | banmal | direct_link | F | short | first | F |
| kr-028 | hit | 0.28 | none | cute | heavy | F | ? | declaration | F | none | banmal | none | F | long | first | F |
| kr-029 | flop | 0.032 | none | plain | heavy | F | high | demo | F | light | banmal | direct_link | T | short | series | F |
| kr-030 | hit | 1.12 | scenery | warm | light | T | high | declaration | F | light | banmal | direct_link | T | short | first | F |
| kr-031 | hit | 2.85 | minimal | cute | light | F | staged | declaration | F | light | banmal | profile | T | short | first | F |
| kr-032 | flop | 0.042 | pet | busy | heavy | F | high | declaration | F | light | banmal | direct_link | T | medium | first | F |
| kr-033 | hit | 0.252 | scenery | dark | heavy | F | staged | declaration | F | none | banmal | profile | F | short | first | F |
| kr-034 | flop | 0.0036 | scenery | dark | heavy | F | staged | declaration | F | none | banmal | profile | F | short | series | F |
| kr-035 | flop | 0.0044 | character_other | cute | heavy | F | high | declaration | F | none | banmal | profile | F | short | first | F |
| kr-036 | hit | 0.168 | none | busy | heavy | F | high | declaration | F | none | banmal | profile | F | short | series | F |
| kr-037 | mid | 0.077 | none | clean | light | F | staged | declaration | F | light | formal | direct_link | T | long | first | F |
| kr-038 | hit | 2.108 | minimal | clean | light | F | high | declaration | F | none | formal | none | T | short | first | F |
| kr-039 | flop | 0.189 | none | clean | heavy | F | staged | declaration | F | none | formal | none | T | short | series | F |
| kr-040 | flop | 0.0048 | none | busy | heavy | F | n/a | declaration | F | none | banmal | profile | F | short | first | F |
| kr-041 | hit | 4.441 | minimal | cute | light | F | high | declaration | F | none | banmal | none | F | medium | first | F |

### 재수집 패치로 `?`가 해소된 행 (2026-08-25)

| id | out | rel | bg_subject | mood | txt_img | face | dev_real | hook | num | emoji | tone | cta | app_nm | len | fresh | seed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| kr-010 | hit | 2.50 | person | warm | none | T | n/a | declaration | **F** | **heavy** | banmal | **none** | F | **short** | first | F |
| kr-021 | hit | 0.093 | **none** | **busy** | **heavy** | **F** | **n/a** | number | T | none | banmal | none | F | short | first | F |
| kr-023 | hit | 0.23 | **none** | **busy** | **heavy** | **F** | **n/a** | number | T | none | banmal | none | F | short | first | F |

굵게 표시한 칸이 이번에 `?`에서 확정된 값이다. **위 본표의 해당 행도 이 값으로 갱신된 것으로 읽을 것**(본표 행은 원본 보존을 위해 그대로 두고 여기에 정정표를 둔다).
- kr-010의 `emoji_density: heavy`는 그림 이모지가 아니라 **감성 특수문자 장식**(`* ੈ✩‧₊˚* ੈ✩‧₊ෆ⸒⸒ෆ⸒⸒`)이다 — AXES 정의를 문자 그대로 적용한 결과이며 통상적인 이모지 사용과 구분해서 읽어야 한다.
- kr-021·kr-023은 `format: text` → `image+caption` 정정에 따라 이미지 5축이 신설됐다. 둘 다 기기 화면이 아닌 아이콘 콜라주이므로 `dev_real: n/a`.

## 열린 칸 (`notable`)

| id | notable |
|---|---|
| kr-001 | 팔로워 67명 rel 175.30 — 전 국가 최대. 비속어 포함 1문장. 앱·제품과 완전 무관한 범용 감정 소재 |
| kr-002 | 브랜드(국수집) 계정이 업종 무관 콘텐츠로 히트. 리포스트 844 > 댓글 369 — "내 답을 남기려 공유"하는 루프 |
| kr-003 | AXES에 없는 배경 소재 = **사물 클로즈업(자동차 컵홀더)**. 댓글 671 ≈ 좋아요 737. 본업(주식) 무관 호기심 훅 |
| kr-004 | "펌" 출처 표기 = 타인 콘텐츠 재게시. kr-002와 동일 소재를 답변 나열형으로 바꾸자 댓글 369→2 |
| kr-005 | 미스터리 훅→반전 2단. 앱명은 캡션에 없고 3/4·4/4 링크카드로만 노출. device_realism 판정불가(상태바 미기술) |
| kr-006 | 첫인상이 자사 제품이 아니라 **남의 트위터 피드 캡처**. first_cut_role=product_showcase |
| kr-007 | 브랜드 화자의 위장 공감 광고. 2/2에서 "어그로성 광고 죄송합니다"로 셀프 태클 |
| kr-008 | 앱명은 있으나 **타사 도구(챗GPT)**. 팔로워 167명 계정의 예외적 단발 히트 |
| kr-009 | 리포스트 63 > 댓글 59 = 저장형. 같은 계정의 자기 제품 홍보 게시물은 1~4 반응 |
| kr-010 | **캡션 원문 절이 파일에 없음** → 캡션 4개 축 판정불가. 이미지=증거·캡션=여백 구조. 후속글 연쇄 히트 |
| kr-011 | 순위 갱신마다 재사용(23위→10위→18위). 앱명 '나만의네컷'은 캡션에 없고 카테고리명만 |
| kr-012 | **KR 유일 character_kitty이자 flop.** 단 freshness=repost + 질문 훅에 답 제시 + 어필리에이트 정황이 동반 교란. url·outcome 보강 패스에서 정정된 레코드 |
| kr-013 | `char:` 픽셀 캐릭터('디도'). 성취 자랑 + choice CTA 조합 → 댓글 98/좋아요 256 |
| kr-014 | rel 5.36. 리포스트 9,200 > 댓글 3,900. 도메인(뷰티) 무관 질문 훅의 화력 상한선 |
| kr-015 | 이미지에 제3자 타임스탬프 앱 워터마크가 찍혀 "인증"의 신뢰를 만듦. 텍스트 이모티콘(`:)`)만 사용 |
| kr-016 | **캡션과 이미지가 서로 다른 게시물에서 온 것으로 기록됨(원본이 명시한 한계)** — 요소 판정 근거로 쓸 때 격리 필요. "주소는 첫 댓글에" 병용 |
| kr-017 | 팔로워 180명 소형인데 flop. 숫자는 나이(30살)뿐 — 성과·방법 수치 아님 |
| kr-018 | 댓글 179 > 좋아요 76 역전. "채널에 소개될 수도"라는 **보상 조건부 댓글 게이트** |
| kr-019 | 비개발자(40대 주부) 메이커 서사. 댓글 45 ≈ 좋아요 59 |
| kr-020 | **보상 조건부 진짜 comment_gate**("팔로우+댓글=다운로드 링크"). 댓글 1,000 > 좋아요 588. 잠금화면 원클릭을 기능 1번으로 명시 |
| kr-021 | **리스티클인데 visual 블록 미기록** — format:text 표기와 "어플 9개" 소재가 상충, 이미지 5축 판정불가. views 57 기록도 다른 지표와 자릿수 불일치(기록 오류 의심) |
| kr-022 | 리스티클 이미지가 **아이콘 인포그래픽**(실제 UI 없음). 팔로워 5.4만 대형 계정에서 rel 최저 |
| kr-023 | kr-021과 같은 사유로 이미지 5축 판정불가. 3계정 동일 템플릿 세트 중 rel 최고 |
| kr-024 | 인스타에서 검증된 정체성의 Threads 교차 유입. 같은 계정 다른 게시물은 1~3 반응 |
| kr-025 | **캡션 원문이 사실상 1줄**("Day 63. 오늘의 앱") — 캡션 축 대부분 근거 얕음. 1컷 목업 표지 + 2·3컷 실제 UI. 회차별 편차 큼(15~50 vs 163) |
| kr-026 | `char:` 정장 고양이 일러스트가 scenery 배경 위에 동반. 조회 60만 실측, 반응 전환 0.21%. 앱명은 링크카드에만 |
| kr-027 | 컷3만 배경이 고양이 사진(pet). 조회 94만, 전환 0.20%. 위젯 레이아웃 카탈로그 구성. 개발자 본인이 댓글 응대 |
| kr-028 | `char:` 구단 의인화 동물 캐릭터(표정이 스코어에 반응). 배경 제거 카드만 노출 → device_realism 판정불가. 도메인=야구 팬덤 |
| kr-029 | 새 app_surface(카플레이)를 써도 못 살림. kr-030(1.12)→kr-029(0.032) 하루 만에 35배 급락의 두 번째 점 |
| kr-030 | 데이터를 **실사진(아기 얼굴 4장)**으로 표현 — KR 잠금화면 위젯 중 유일한 face:true. 같은 날 변주 게시물은 8/4/3으로 급감 |
| kr-031 | `char:` 감정 이모지 캐릭터. **첫 줄부터 앱명 공개했는데 hit** — 미스터리 구조 불필요 근거. 온보딩 목업인데도 hit |
| kr-032 | 배경이 **실제 반려견 사진**인데 앱 아이콘도 강아지 일러스트 — 마스코트와 실제 반려동물이 겹침. 일정 내용이 TOEIC·유산균·롯데vs키움·맨유vs리즈로 뒤섞여 **생활감이 이번 수집 최고**. 조회 7.9천인데 전환율 0.75% |
| kr-033 | 두 컷 모두 **iOS 상태바·시계가 아예 없다** — 잠금화면 캡처가 아니라 '달력이 구워진 배경화면' 렌더. 일정이 필라테스·SNS콘텐츠·일본여행 등 매끈한 가상 페르소나. 댓글에 "기본 캘린더랑 연동되냐"는 설치 직전 질문 |
| kr-034 | 시각이 **9:41·배터리 만충** = 애플 공식 목업 표준값(staged 근거). 일정 데이터가 kr-033의 5월판을 그대로 8월로 옮긴 것(17~19일 '일본 여행' 위치까지 동일). 사흘 연속 같은 템플릿 → 8/2/2좋아요 |
| kr-035 | **KR 두 번째 캐릭터 배경 표본이자 비격리 첫 사례**(kr-012는 시딩 격리). 배경=흰 털복숭이 캐릭터(이름 미상) + 위젯 옆에 **정장 고양이 앱 마스코트** 별도 등장 — 배경 캐릭터와 제품 캐릭터가 공존. 첫 줄이 감상('하…')이고 조건 호명은 3번째 줄로 밀림 |
| kr-036 | 배경이 **게시자 본인 사진**(침구 위 펼친 책)이고 2/2에서 "내사진 설정했어"라고 직접 밝힘 — AXES의 background_subject 보강 정의를 당사자가 말로 확인해준 사례. 링크카드 리뷰 수가 3일 만에 104→123개 |
| kr-037 | 이모지 머리표(✨🔁🐶) 기능 3단 나열 + **완결 존댓말 3연속**(VOICE-KR 금지선 2 위반). 댓글이 "누가 왜 꼭 썼으면 좋겠는지 바운드님만의 이야기가 쌓이길"로 원리 1을 자연어로 재발견. **개인 화자 존댓말인데 hit가 아닌 첫 사례**(단 flop이 아니라 mid) |
| kr-038 | **팔로워 37명**. 위젯 카드 그라데이션이 두산(남색)↔한화(주황) 구단 색과 일치 — '위젯 테마'를 배경화면에 맞추는 발상 자체가 소재. 댓글 25개 중 다수가 "어떻게 하는 거냐" 사용법 질문이라 **의도 없이 댓글 게이트 운영비용 발생** |
| kr-039 | kr-038과 **같은 기능**의 재알림인데 카드뉴스+존댓말. 큰따옴표 슬로건·'많은 사랑 부탁드려요'·✔불릿 나열. 2컷은 사용자 댓글을 오려 붙인 "유저의 목소리" 카드 — **소재는 날것인데 브랜드 포장으로 힘이 빠짐**. relative(0.189)로는 flop으로 안 보이나 절대 도달 285회 |
| kr-040 | **배경화면도 기기 크롬도 없는 위젯 미리보기**(app_surface=none). 디자인 완성도는 높은데 맥락이 없다. 같은 계정이 조건 호명 문법을 4번 반복하며 전부 한 자릿수 — 조건 호명 경향의 대량 반례 |
| kr-041 | 귀여움이 **배경화면이 아니라 위젯 그래픽 자체**(픽셀아트 전동차)에 있다. 첫 줄이 '귀엽게 **만들어봄**'으로 감상이 아닌 제작 선언. 다이나믹 아일랜드 라이브 액티비티 활용. 댓글 하나("갤럭시도 같이 나와야지")가 **좋아요 51** — 안드로이드 결핍 정서의 크기 |

## 집계 (2026-08-25 갱신)

- 총 **41건** / 이미지 있는 레코드 **26건** · 텍스트 전용(n/a) 15건 · **이미지 판정불가(`?`) 0건**
  - 이전 판정불가 2건(kr-021, kr-023)은 재수집으로 해소 — 둘 다 `format: text`가 오기였고 실제로는 인포그래픽 이미지를 포함한다.
- 캡션 축 판정불가: **0건** (kr-010의 4개 축이 캡션 원문 회수로 해소)
- device_realism 판정불가 3건(kr-005, kr-016, kr-028) — 변동 없음
- seeding_suspect true: 1건(kr-012) — **신규 10건 중 true 없음.** 단 kr-026·032·035·036의 계정 관계(개발자 기준 2개 주체)는 kr-035 비고에 기록
- freshness: first **31** / repost 2(kr-004, kr-012) / series **8**(kr-011, kr-013, kr-025, kr-029, kr-034, kr-036, kr-039 + 기존)
- **background_subject 분포(이미지 26건 기준)**: none 11 / minimal 4 / scenery 4 / character_kitty 1 / **character_other 1** / person 1 / pet 1 / 기타 3
  - **캐릭터 배경은 여전히 2건뿐**(kr-012 격리 대상, kr-035 비격리)이고 **둘 다 flop**이다. 이번 라운드 목표였던 '캐릭터 배경 3건 이상'은 **미달**이다 — 남은 공백으로 유지할 것.

### 이번 라운드가 만든 축별 관찰

- **tone**: formal 3건 추가(kr-037 mid, kr-038 **hit**, kr-039 flop). **kr-038이 존댓말 hit**이므로 '존댓말=죽는다'는 성립하지 않는다. 같은 계정 안에 존댓말 hit(kr-038)와 존댓말 flop(kr-039)이 동시에 있어, 변수는 존댓말이 아니라 **공지문/브랜드 화법 형식**임이 통제된 형태로 확인됨.
- **hook_type**: 신규 10건이 전부 `declaration`으로 태깅됐다. demo·question 표본은 이번에도 늘지 않았다 — `hook_type=demo의 hit 사례 0건` 공백은 **미해소**.
- **mood=busy**: kr-032(flop)·kr-036(**hit**)·kr-040(flop)·kr-021·kr-023(hit) 추가. **kr-036·kr-021·kr-023으로 busy hit 사례가 처음 확보**됐다 → elements-gaps의 'mood=busy hit 0건' 공백 **해소**. busy는 성패를 가르지 않는 쪽으로 기운다.
- **face**: 신규 10건 전부 F. `face=true의 flop 사례 0건` 공백 **미해소**.
- **cta_type**: choice 사례 추가 없음 → 공백 **미해소**.
