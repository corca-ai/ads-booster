# 기록 축 v1 — 요소 수준 관찰 스펙

> 2026-08-24 확정 (3층 구조: **기록 축은 선행 / 판정은 데이터 후행 / 열린 칸 상시**).
> 모든 레퍼런스 수집·재채굴 에이전트는 이 축으로 기록한다. 판정("이 요소가 유효한가")은 이 파일에 쓰지 않는다 — 판정은 hit↔flop 대조와 독립 표본이 채워질 때 ELEMENTS-{국가}.md에서 이뤄진다.
> 축 목록은 버전 관리 대상: 열린 칸에서 반복 관찰된 요소는 다음 버전에서 축으로 승격한다 (선례: 사용자 제보 '키티 배경' → background_subject 축 신설).

## 이미지 요소 (visual 블록 확장)

| 필드 | 값 | 비고 |
|---|---|---|
| background_subject | character_kitty / character_other / family_photo / person / pet / scenery / minimal / none | **잠금화면에 깔린 폰 배경화면 이미지의 소재** (위젯 뒤 배경 — 장면 연출 묘사가 아님). 생성·촬영 시에는 "그 페르소나가 실제로 설정해뒀을 법한 배경"을 고른다 — 분위기 연출용 배경은 진정성 원칙(실제 유저의 폰처럼) 위반 (2026-08-25 정의 보강). 캐릭터는 이름 특정 가능하면 특정 |
| mood | cute / clean / warm / busy / dark / 기타 한 단어 | 이미지 전체 무드 |
| text_in_image | none / light / heavy | 기존 스펙 유지 |
| face | true / false | 기존 스펙 유지 |
| device_realism | high(실기기감 완전) / staged(연출 티) | 상태바·배터리·배경 종합 |

## 캡션 요소

| 필드 | 값 | 비고 |
|---|---|---|
| hook_type | question / declaration / empathy / number / demo | 첫 1~2줄의 훅 유형 |
| has_numbers | true / false | 구체 수치·기간·개수 포함 여부 |
| emoji_density | none / light(1~2) / heavy(3+) | |
| tone | banmal / formal / mixed | 반말/격식 |
| cta_type | none / comment_gate / profile / direct_link / choice | 전환 장치 유형 |
| app_name_shown | true / false | 캡션 내 앱명 노출 |
| length | short(3줄 이하) / medium / long(8줄+) | |

## 메타 요소

| 필드 | 값 | 비고 |
|---|---|---|
| freshness | first(첫 게시) / repost(재탕) / series(시리즈 후속) | 감가상각 판별용 — 3개국에서 최대 16~80배 차이 확인 |
| seeding_suspect | true / false | 조직적 시딩 의심 시 true + 사유 |

## 열린 칸 (필수)

`notable: "..."` — 축에 없는데 눈에 띈 것을 자유 기술. 비워도 되지만 필드 자체는 모든 레코드에 존재해야 한다. 여기서 반복되는 관찰이 다음 축 승격 후보다.
