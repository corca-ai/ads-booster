---
country: JP
derived_from: context/references/JP/ELEMENTS-TABLE.md (jp-001~032)
axes_version: AXES.md v1
updated: 2026-08-24
version: v1.1
status: draft (opus 검증 패스 정정 반영)
---

# 일본 요소 판정표 (JP)

> **v1.1 정정 (2026-08-24, `research/verification-pass.md`)**
> 1. `cta_type=comment_gate` 행 **무효화** — 두 근거가 모두 의심 태깅(§3-1).
> 2. **jp-018의 이미지 5축(`minimal/clean/heavy/F/staged`)은 `?`로 취급할 것.** 이 레코드는 `visual.captured: false`인데 ELEMENTS-TABLE에 이미지 축이 채워져 있다. 근거가 된 `description`이 캡처 없이 작성됐다. jp-013도 같은 상태이나 그쪽은 대부분 `?`로 남아 있다.
> 3. **jp-018의 `cta_type=direct_link`는 오류** — 캡션에 CTA가 없다(레코드 본문이 "리드매그넷 CTA는 캡션이 아니라 **이미지에**"라고 명시하는데, 표 상단 규칙은 "cta_type: **캡션 기준**"). 정정값은 `none`.
> 4. **jp-016의 `account_type: maker`는 원본 frontmatter 오기** — 레코드 `why`가 스스로 "사실상 브랜드_대행"이라 적고 PRINCIPLES-JP도 브랜드로 인용한다.
> 5. **jp-022의 relative 0.027은 오기** — 반응 합계 4 / 팔로워 187 = 0.0214. hit/flop 판정은 불변.
> 6. `has_numbers` 행의 결론(숫자 무변별)이 **원리 4 개정을 이끌었다** — `PRINCIPLES-JP.md` v3 원리 4 참조.

> **요소 수준** 판정만 담는다. 포맷 수준 원리는 `PRINCIPLES-JP.md`, 중복은 상호 참조로 대체.
> 등급 정의는 `ELEMENTS-KR.md` 상단과 동일.
> **시딩 격리**: jp-003 / jp-004 / jp-008(Taska), jp-006(KUU 부분)은 아래 판정 근거에서 전부 제외했다. 격리 후 유효 표본 **28건**.
> **outcome은 파생 판정이다** — JP 레코드에는 outcome 필드가 없어 각 레코드의 `why` 자기 평가를 옮겼다(`ELEMENTS-TABLE.md` 상단 참조).

## 이미지 요소

| 요소 | hit 근거 | flop 근거 | 등급 | 한 줄 해석 |
|---|---|---|---|---|
| background_subject = 캐릭터 월페이퍼(전면) | jp-020 애니 캐릭터 11.33 / jp-030 키티 5.31 · jp-031 키티 1.58 · jp-032 키티 0.316 (**독립 2계정**) | 캐릭터 배경 flop 0건 | **지지(조건부)** | 배경 전면을 캐릭터가 차지한 4건은 전부 hit/mid, flop 0. **단 3건이 한 계정(@aoi_1124_gram)이라 독립 계정은 2개뿐이고, 키티만 놓고 보면 단일 계정 3회 게시다** |
| background_subject = character_kitty 단독 | jp-030, jp-031, jp-032 (**전부 동일 계정**) | — | **표본 부족** | "키티 배경이 좋다"는 사용자 제보는 **독립 계정 1개**에만 근거한다. 같은 계정 안에서도 재탕한 jp-032는 좋아요 1700→104로 급락 → 배경이 아니라 신선도가 지배 변수일 수 있다 |
| 소형 장식 일러스트(배경 아님) | — | jp-022(rel 0.027, 우하단 커플 일러스트) | 표본 부족 | 캐릭터가 **배경 전면**인지 **구석 장식**인지는 전혀 다른 사안으로 보인다. 후자 유일 사례가 flop |
| background_subject = pet | (jp-003·006·008 전부 격리) | — | **판정불가** | 반려동물 배경 3건이 **전부 시딩 의심 레코드**다. 격리하면 표본 0건 — Taska 시딩 캠페인이 pet 템플릿을 썼다는 사실만 남는다 |
| mood = cute | jp-001, 007, 009, 017, 020, 023, 026, 030, 031 (+jp-032 mid) — **9계정** | jp-022 | **지지** | JP hit의 지배적 무드. cute 11건 중 flop 1건. KR(warm 4/4)·TW(변별 없음)와 갈리는 지점 |
| device_realism = high vs staged | high: jp-001, 007, 020, 030, 031 / staged: jp-005, 014, 023 | high: jp-021, 022 / staged: jp-018 | **반증** | **KR과 같은 결론 — 실기기감은 성패를 가르지 않는다.** staged(목업·소개 슬라이드)도 4/5 살아남는다 |
| text_in_image | heavy 13hit+4mid : 3flop / light 2:0 / none 2:1 | — | **반증** | 이미지 텍스트 밀도는 무관. JP는 heavy가 기본값이고 그 상태로 rel 9.52(jp-007)까지 나온다 |
| face = true | jp-007(9.52) | — | 표본 부족 | 1건뿐 |

## 캡션 요소

| 요소 | hit 근거 | flop 근거 | 등급 | 한 줄 해석 |
|---|---|---|---|---|
| hook_type = empathy(감정 토로·공감 호소) | — | jp-018(0.001), jp-021(0.004) (2계정) | **반증** | **JP에서 empathy 훅은 2/2 flop, hit 0건.** jp-021은 같은 계정·같은 영상 포맷의 정보형 훅(rel 4.95) 대비 1,238배 낮다 |
| hook_type = number | jp-015(8.61), jp-028(3.85) (+jp-010 mid) — 3계정 | — | **지지** | 숫자를 앞세운 훅 3/3 hit·mid. KR(5/6)과 같은 방향 |
| hook_type = question | jp-011(17.68) (+jp-032 mid) | — | 표본 부족 | hit 1건. 다만 그 1건이 JP 전체 1위다 → `PRINCIPLES-JP.md` 원리 3 참조 |
| hook_type = demo(기능 나열) | jp-005(1.57) | — | **표본 부족 + 국가 차이 가설** | JP 유일 사례가 hit인데 **KR은 2/2 flop**(kr-006, kr-029). 「個人開発」 태그가 기능 나열을 살린다는 원리 2와 정합하나 요소 표본은 1건뿐 |
| app_name_shown = true | jp-005, jp-014, jp-025 (+jp-010 mid) — **4계정, flop 0** | — | **반증(KR과 역전)** | **JP에서는 앱명을 밝힌 4건이 전부 hit·mid다.** KR은 정반대(밝힌 쪽 50%) → 국가 차이 §1 참조. `PRINCIPLES-JP.md` 원리 7·10의 요소 수준 확인 |
| cta_type = none | hit 15 / mid 3 / flop 4 (28건 중 22건이 none) | — | **지지(부재의 관찰)** | **JP는 캡션에 CTA를 거의 쓰지 않고, 그 상태로 rel 9.52·11.33이 나온다.** 원리 13(댓글비가 KR의 1/29)과 정합 → 한국식 댓글 게이트를 그대로 옮기지 말 것 |
| cta_type = comment_gate | ~~jp-011, jp-024~~ | — | **판정불가(v1.1에서 무효화)** | **두 근거가 모두 의심 태깅이다.** jp-011의 캡션은 질문 한 문장뿐인데 `hook=question`으로 이미 센 것을 CTA 축에서 또 셌고(이중 계상), jp-024의 「仲良くしてください」는 관계 요청이지 댓글 요청이 아니다. → **JP에 깨끗한 comment_gate 표본은 0건.** 원리 13("일본은 도달의 게임")과 정합하므로 한국식 댓글 게이트를 JP에 옮길 근거는 없다. 상세: `research/verification-pass.md` §3-1 |
| has_numbers | true: 4hit+3mid : 2flop (78%) | false: 14hit+2mid : 3flop (84%) | **반증** | **숫자 유무는 JP에서도 성패를 가르지 않는다.** `PRINCIPLES-JP.md` 원리 4("구체적 효용·숫자")가 실제로 잡은 것은 숫자가 아니라 **효용 서술의 구체성**이다 — 원리 4의 핵심 근거 jp-001은 has_numbers=false다 |
| tone = formal(です・ます) | jp-001, 005, 011, 014, 017, 023, 026 — 7hit + 2mid : 1flop (70%) | jp-018 | **반증(KR과 역전)** | **JP에서 존댓말은 전혀 불리하지 않다.** JP 전체 1위 jp-011이 formal이다. 유일 formal flop은 브랜드 계정(jp-018) — KR과 같은 구조로 죽인 건 화법이 아니라 화자 |
| emoji_density | none 11hit:2flop / light 5hit+3mid:2flop / heavy 2hit+1mid:0 | — | **반증** | 무관. heavy 3건(jp-005, 010, 031)이 전부 hit·mid지만 표본 부족 |
| length | short 14hit:4flop / medium 3hit+1mid:0 / long 1hit+1mid:1flop | — | 표본 부족 | medium 4/4는 대조 flop이 없어 판정 불가. 전반적으로 short가 기본값 |

## 메타 요소

| 요소 | hit 근거 | flop 근거 | 등급 | 한 줄 해석 |
|---|---|---|---|---|
| freshness = repost(동일 이미지 재탕) | — | jp-032(1700→104, 약 16배) / jp-004(245만→3만, 약 80배, **격리**) | **표본 부족(격리 후 1계정)** | 방향은 확고하나 비격리 근거는 jp-032 한 계정뿐. **이 축이 캐릭터 배경 판정의 최대 교란 변수다** — jp-030과 jp-032는 이미지가 완전히 같은데 16배 갈렸다 |
| freshness = series(변주 게시) | jp-030(5.31), jp-031(1.58) — 동일 계정 | jp-021 | 표본 부족 | 이미지를 변주하면(jp-031 캐릭터 3종 카탈로그) 회차가 이어져도 살아남는다는 관찰. 단일 계정 근거 |
| seeding_suspect = true | jp-003(1.31), jp-006(0.858), jp-008(9.64) | jp-004(0.08, 재탕분) | 판정 대상 아님(관찰) | **조직적 시딩이 낼 수 있는 화력 상한이 rel 9.64·조회 385만이라는 것만 기록한다.** 서로 다른 두 계정이 동일 UI 템플릿·동일 캡션 공식을 쓴다 — TW tw-015가 같은 공식의 중국어판이다 |

## 국가 차이 (요소 수준)

1. **캐릭터 배경** — JP 지지(조건부, 독립 2계정 4건 전부 hit/mid) vs KR 표본 부족·유일 사례가 flop(kr-012) vs TW 표본 0건. **다만 JP 키티 근거는 단일 계정 3회 게시이고, 그 안에서도 재탕분(jp-032)은 16배 급락했다.** "키티가 좋다"가 아니라 "캐릭터 월페이퍼가 JP에서 최소한 안 죽는다"까지가 데이터가 허락하는 진술이다.
2. **app_name_shown** — JP는 밝힌 4건 전부 hit·mid(100%), 안 밝힌 쪽 75%. KR은 정반대(밝힌 50% vs 안 밝힌 87%). TW는 무관(70% vs 83%). 캡션에서 앱명을 감추는 KR식 여백을 JP에 그대로 옮길 근거는 없다.
3. **hook_type = empathy** — JP 2/2 flop(반증) vs TW 2/2 hit(지지 약) vs KR 2:2(표본 부족). 감정 토로형 훅의 수용도가 세 나라에서 가장 크게 갈린다.
4. **mood** — JP cute 11건 중 flop 1 / KR warm 4/4·busy 0/2 / TW 변별 없음.

## AXES 승격 후보

`ELEMENTS-KR.md` 하단의 3개 후보(`benefit_concreteness`, `speaker_type`, `character_present` 분리)를 JP 데이터가 전부 재확인한다. 특히 **`benefit_concreteness`** 없이는 JP의 최강 원리(원리 4)를 요소 수준에서 검증할 수 없다.
