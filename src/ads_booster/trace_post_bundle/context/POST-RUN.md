---
version: v0.2
updated: 2026-09-10
status: run-02 사용자 육안 승인 반영 · 스킬 전 실행 계약 정리
---

# 컨셉 선택부터 국가별 이미지까지

## 1. 실행 입력과 격리

런 폴더 `run.json`을 읽는다. 필드: concept_card, template, device_date(ISO), countries, representative, motif, place, skeleton, seasonal_replacements.
이는 이번 실행의 선택값이지 새 운영 기본값이 아니다. 게시·커밋·푸시는 수행하지 않는다.
신규 실행은 이전 채팅·Claude 기록·handoff·옛 생성 결과를 읽지 않는다. 규칙과 입력만 읽는다. 실행 중 명세 외 판단은 defects.md에 남기고 첫 결과를 덮어쓰지 않는다.
먼저 이 문서, 카드, CAPTION-RULES, FACTS를 전문으로 읽는다. 생성 검수는 CAPTION-RULES §6를 사용한다. 옛 PRINCIPLES·VOICE·demo-kit는 생성에 로드하지 않는다.

## 2. 캡션·공통 일정

CAPTION-RULES §1과 아래 §3에 따라 package.json을 생성한다. package.md는 조립기가 자동으로 만든다. device_date는 run.json, week는 해당 날짜가 속한 월~일이다. 기기 시각은 카드 생활 리듬으로 한 번 도출한다.
전체 뼈대 후보를 요구하는 캡션 단독 드라이런과 달리, 이 게시물 런은 run.json의 skeleton 하나로 국가별 캡션 하나씩 출력한다. 원문 슬롯 구조를 유지하고 제품 사실과 사용 조건을 검토한다. recommend는 카드의 opener/recommend_middle/effect를 그대로 조립한다. 고정 줄을 실행 중 번역하거나 새 기능·경험 문장을 추가하지 않는다. 실측 예시와 형식 규칙의 충돌은 defects.md에 명시한다. 링크·튜토리얼의 열린 칸을 지어내지 않는다. 답글과 튜토리얼은 별도 게시 준비 항목으로 남긴다.
일정·할일은 기존 §4 규칙대로 새로 생성한다. 예시를 복사하지 않는다. 일정은 한국어 공통본이며 빈칸 4종을 사용한다. 화면에 보이게 하려고 새로운 수량 규칙을 만들지 않는다.
review-caption.md에 A·B 검수와 실제 개수를 기록한다. 1회 수정 시 package-v1.json과 기존 읽기용 문서를 보존한다. 다른 출력으로 교체할 때만 조립기 --replace를 명시한다. CAPTION-RULES §4-1 총량 예외를 적용한다. 18행 미만이면 package.json의 schedule_exception에 카드에 기간 근거가 없는 이유를 기록한다.

## 3. 국가별 현지화와 기계 입력

이 단계는 생성 실행자가 수행하고 `scripts/build_input.py`가 검증·조립한다. 별도 번역 Codex 호출 없이 같은 생성 실행에서 세 국가 번역을 출력하는 방식으로 이번 계약을 고정한다.
`package.json`이 정본이며 package.md는 읽기용 파생 파일이다. 실행자가 두 파일을 따로 쓰거나 런마다 create_package.py를 만들지 않는다. JSON을 직접 출력하고 표준 라이브러리 조립기로 읽기용 문서를 만든다. 필드:

```json
{
  "concept": "귀여움", "device_date": "2026-09-10", "device_time": "7:25",
  "week": ["2026-09-07", "2026-09-13"],
  "captions": {"kr": {"skeleton": "recommend", "text": "...", "reply_link": "[열린 칸]", "tutorial": "[열린 칸]"}},
  "schedule": [{"id":"s01","title":"{의원} 방문","date":"2026-09-10","days":1,"time":"종일","kind":"사건","color":3}],
  "todos": [{"id":"t01","title":"카드값 확인"}]
}
```

예시 항목·시각은 복사하지 않는다. captions에는 run.json의 모든 국가가 있어야 한다.
`localization.json`은 아래 구조다. 배열은 공통본 순서와 ID를 그대로 유지한다.

```json
{"kr": {
 "language":"ko", "date_line":"9월 10일 목요일",
 "headers":{"schedule":"일정","todos":"할 일","tomorrow":"내일","all_day":"하루 종일"},
 "schedule":[{"id":"s01","title":"피부과 방문","date":"2026-09-10","days":1,"time":"종일","kind":"사건","color":3}],
 "todos":[{"id":"t01","title":"카드값 확인"}],
 "substitutions":[{"id":"s01","token":"{의원}","value":"피부과"}],
 "seasonal_changes":[]
}}
```

time의 `종일`은 기계 데이터 공통값으로 번역하지 않는다. 화면 출력 시 headers.all_day로 바꾼다. HH:MM도 그대로 보존하고 주문서에서 h:mmAM/PM으로 표시한다.

JP·TW도 같은 형식. language는 ja / zh-TW, 헤더·종일·내일은 자연스러운 대상 언어로 번역하고 제품 실기 문자열 검증 여부를 기록한다. T2 명세에서 헤더 제목은 편집 필드다. 이 런은 번역 의미와 표시 날짜가 정확해야 하며, 제품 실기 UI 문자열과의 일치 여부는 별도 관찰 항목이다. 미대조는 미확인으로 남기되 그것만으로 이미지 생성을 차단하지 않는다. 잘못된 번역·날짜·구조 변경을 이 예외로 허용하지 않는다. 카드의 local 값을 먼저 넣고 문장을 번역한다. `{동네}`는 일정 배열→할일 배열 순서로 해당 국가 neighborhoods를 순환한다. substitutions는 사용한 모든 빈칸을 기록한다. `{의원}`은 진료과 전체이므로 중복하지 않는다.
계절 교체는 일반 규칙상 ≤2이나 이번 run의 seasonal_replacements=false면 0건이다. 켜진 런은 날짜가 일치하는 사건만 교체하고 ID·시각·기간·종류·색은 유지, 근거를 seasonal_changes에 기록한다. 현재 조립기는 false만 지원한다.
번역 의미·치환 출처·현지 날짜 표기를 review-localization.md에서 검수한다. 기계 검수는 ID·순서·날짜·시각·기간·색·개수의 보존을 담당하며 번역 품질 통과를 대신하지 않는다.
두 보고서의 의미 판정이 끝나면 `content_review.py record`로 run/package/localization·보고서·국가 input 해시를 묶는다. 보고서의 PASS 문자열을 검색해 승인으로 추정하지 않는다. 누락·반려·이후 입력 변경은 이미지 단계를 차단한다.

실행: `python3 scripts/build_input.py <run_dir>`.
package.json과 localization.json을 모두 저장한 다음 조립한다. 성공 시 package.md, `<국가>/input.md`, assembly-check.json 생성. 검수 보고서는 최근 시도를 나타내고, 기존 읽기용/국가 입력 내용이 다르면 기본적으로 덮어쓰기를 거부한다. 실패하면 입력을 보존하고 수정 전후·원인을 기록한다. 문서나 숫자를 몰래 바꾸어 통과시키지 않는다.

## 4. 주문서·이미지

IMAGE-RULES 전체와 T2 spec을 읽고 국가 input에서 order.md를 작성한다. 같은 일정 ID를 같은 화면 슬롯에 둔다. 대표의 슬롯 배정·숨김 수를 나머지 국가에 유지한다. 슬롯 색 제약과 일정 정렬이 동시에 만족되지 않으면 차이를 결함으로 명시하며 색을 바꾸거나 항목을 창작하지 않는다.
공통 메타는 common-scene.json에 저장한다. motif·place는 run.json 값, 시각은 package 값. 씬 토큰·소품·몸·색 계열은 IMAGE-RULES §3에서 한 번 도출한다.
§0-1과 §4-A/B/L/C대로 대표 A→B, 타국 L, 국가별 C를 실행한다. 먼저 prompt-A/B/L 및 단 하나의 공통 prompt-C.txt를 파일로 저장하고, 실제 도구에 전달한 문자열과 일치시킨다. 국가별 C 복사본은 공통 파일과 바이트 단위로 같아야 한다.
이미지는 built-in image_gen으로 생성/편집한다. 입력 이미지는 먼저 view_image로 확인한다. 기본 저장 경로에서 런 폴더로 복사하고 출처 경로를 calls.jsonl에 남긴다. 도구가 없으면 대체 생성기를 사용하지 않고 실행 환경 결함으로 보고한다.
이미지 호출은 `scripts/README.md`의 `image_call.py prepare/error|unresolved/complete/review` receipt 절차를 쓴다. prepare가 반환한 request 객체를 그대로 built-in image_gen에 전달하고 call_id를 수동으로 옮겨 적지 않는다. 호출 오류 시 receipt에 근거를 기록하고 중단한다. 출력 생성 여부가 불명한 request를 재호출하지 않고, 실제 source를 회수한 경우에만 unresolved receipt를 complete한다. CLI는 built-in 호출을 하거나 별도 API로 전환하지 않는다. receipt가 단계·국가·입력·프롬프트 해시·출력·실행자를 호출 로그에 연결한다. 알 수 없는 모델명·시간·호출 결과를 추정하지 않는다.
대표 B와 타국 L의 재시도 후에도 글자 또는 배경 보존이 명확히 실패하면 씬 이전에 중단한다. A 기하 실패는 B까지 관찰하되 결과에 남긴다. 씬은 §5-C와 참조 화면 문자열 대조를 모두 수행한다.

## 5. 결과

review.md: 단계별 실제 관찰, 최초/재시도 판정, 육안 디자인 동일성/픽셀 동일성(미측정 허용), 씬 화면 보존, 생성 호출 수와 시간, 문서 밖 판단을 기록한다.
defects.md: 규칙 충돌·카드 공백·번역/생성/검수 도구 실패를 분리한다.
POST.md: 국가별 게시 캡션·선택 이미지 링크·답글 준비 상태를 정리한다. 실제 게시하지 않는다. 산출물 완성과 게시 준비 완료를 구분한다.
사용자가 현재 요청에 실행 모델·reasoning effort를 명시했으면 그 선택을 쓴다. 미지정 시 Luna medium이 비용 기본값이다. 총괄은 일상적인 이미지 중복 검토를 하지 않고 규칙 충돌/미확인/최종 사람 확인에만 관여한다. 도구 반환 전체나 base64를 텍스트로 출력하지 말고 생성 이미지 표시·저장 경로·검수 요약만 전달한다.

규칙은 실행 중 수정하지 않는다. 결함을 발견하면 별도 버전에서 수정하고 다음 실험으로 검증한다. 한 원본 실험으로 성공률을 일반화하지 않는다.


## 6. 재개·기록·스킬 준비 경계

- 신규 런은 런 폴더를 새로 만들고 시작 시점 규칙 사본과 해시를 보존한다. 이미 실행한 런의 데이터를 새 규칙으로 덮어쓰지 않는다.
- 중단되면 run_state 상태에서 미완료 호출을 확인한다. 파일이 있다는 이유만으로 통과 처리하지 않는다. 실제 출력·입력·프롬프트를 확인하고 검수한 뒤 복구를 기록한다.
- 입력/프롬프트 해시가 달라진 단계는 기존 완료 상태를 재사용하지 않는다. 해당 단계와 의존하는 후속 결과를 재검토한다.
- 검수 항목은 templates/review-stage.md를 따른다. 모든 해당 항목을 기록하고 미확인 결과를 자동 통과시키지 않는다.
- 최초 이미지 실행과 복구 작업의 실행자가 다르면 각각 기록한다. 파일 수정 시각을 도구 호출 시작/종료 시각으로 바꾸어 기록하지 않는다.
- 사용자에게는 POST.md의 캡션·잠금화면·씬과 검수 요약을 제시한다. 이번 결과에 대한 사용자 육안 승인은 인간 평가 기록이며 다음 런 성공 보장은 아니다.
- 스킬의 선택·런 시작 정책은 RUN-POLICY.md에서 관리한다. SKILL.md는 이 규칙을 복사하지 않고 경로를 참조한다. 현재는 스킬 구현 전이다.
