# ORIGIN — 이 컨텍스트 디렉터리의 출처와 신뢰 등급

**이 디렉터리가 2026-08-26부터 Trace 마케팅 컨텍스트의 정본(canonical live context)입니다.**
게시물 후보 생성은 여기 있는 문서만 근거로 삼습니다.

원본 리서치 아카이브는 <https://github.com/corca-ai/trace-marketing-context> 이며,
`core/`와 `references/KR/`의 문서는 그 저장소의 `b77d8b541828319d67750d87ff5188216f1c2e3d`
커밋에서 **바이트 단위로 동일하게** 복사했습니다(frontmatter 포함). 아카이브는 이후 수집·검증
작업의 기록 보관소이고, 파이프라인이 읽는 사본은 이 디렉터리입니다. 두 곳이 갈라지면 이
디렉터리가 이깁니다.

아카이브에서 그대로 가져온 파일:

```
core/  PRINCIPLES-GLOBAL.md  PRINCIPLES-KR.md  PRINCIPLES-JP.md  PRINCIPLES-TW.md
       ELEMENTS-KR.md  ELEMENTS-JP.md  ELEMENTS-TW.md
       VOICE-KR.md  SHOOTING-KR.md  AXES.md  FACTS.md
references/KR/  RESEARCH-INDEX.md(= 아카이브 INDEX.md)  ELEMENTS-TABLE.md  kr-001.md ~ kr-041.md
```

## 이 저장소에만 있는 파일

아카이브에 대응 문서가 없고 파이프라인 운영을 위해 이 저장소가 소유하는 파일입니다.

| 파일 | 역할 |
|---|---|
| `core/PIPELINE-SCOPE.md` | 파이프라인 범위(사람 승인 필수, 자동 게시 없음)와 문서 읽는 규칙 |
| `references/KR/INDEX.md` | 페르소나가 참조하는 **장면 인덱스**(`kr-study-day` 등) |
| `markets/*.md` | 국가별 언어 지침과 장면 참조 id |
| `profiles/*.json` | 국가별 시작 페르소나와 manifest |

**`references/KR/INDEX.md`와 `references/KR/RESEARCH-INDEX.md`는 다른 문서입니다.** 앞의 것은
`profiles/*.json`의 `reference_ids`가 가리키는 장면 인덱스이고, 뒤의 것은 수집 레코드 41건의
스크리닝 표입니다. 서로 덮어쓰지 마세요.

이관 전 이 디렉터리에는 아카이브 문서의 **축약본**이 들어 있었고, 전부 아카이브 원본으로
교체했습니다. 예전 `core/PRINCIPLES-KR.md`는 586바이트 의역본이었고 실제 문서는
8,617바이트입니다. `PRINCIPLES-GLOBAL.md`(496 → 13,218), `VOICE-KR.md`(526 → 4,094),
`ELEMENTS-KR.md`, `FACTS.md`도 마찬가지였습니다. 축약본에만 있던 운영 규칙은
`core/PIPELINE-SCOPE.md`로 옮겼습니다. 후보의 출력 필드 계약은 문서가 아니라 두 엔진이 각각
코드로 강제합니다(hosted는 `candidateResponseSchema`, 로컬은
`candidate_generation/instruction.py`의 출력 템플릿). 축약본에서 사라진 내용은 없습니다.

## 신뢰 등급

### 리서치 검증됨 — KR

수집 레코드 41건(`kr-001` ~ `kr-041`)에 근거하며 원리·요소 판정 모두 opus 검증 패스를
거쳤습니다. 장면 인덱스, 문체 스펙(`VOICE-KR`), 촬영 컨텍스트(`SHOOTING-KR`)까지 갖춘
유일한 국가입니다.

### 리서치 검증됨, 단 일부 계층 부재 — JP·TW

원리와 요소 판정표는 KR과 같은 방식으로 검증됐습니다(JP는 `jp-001`~`jp-032`, TW는
`tw-001`~`tw-018` 기반). 다만 두 국가에는 다음이 **없습니다**:

- 문체 스펙(`VOICE-*.md`) — KR에만 존재합니다. 없는 것을 지어내지 마세요.
- KR과 같은 형식의 레퍼런스 INDEX — 아카이브에서도 `JP-log.md` / `TW-log.md`가 유사 역할을
  할 뿐 표+top-N 형식으로 통일되지 않았습니다.
- 레퍼런스 레코드 본문 — 이번 이관에서는 KR만 가져왔습니다.

JP·TW의 언어 지침과 장면 참조 id는 `markets/JP.md` / `markets/TW.md`가 계속 담당합니다.

### 리서치 근거 없음 — US·DE·FR·BR (가설 시장)

`markets/US.md`, `markets/DE.md`, `markets/FR.md`, `markets/BR.md`와 각 `profiles/*.json`은
**수집·검증을 거치지 않은 플랫폼 자체 작성 가설**입니다. 실제 게시물을 관찰해서 얻은 것이
아니며, 어느 문장도 근거 문서를 가리키지 못합니다. 이 네 국가의 후보를 검증된 결과처럼
다루지 마세요. 해당 시장을 실제로 운영하려면 KR과 같은 수집·검증 절차가 선행되어야 합니다.

네 국가도 `core/PRINCIPLES-GLOBAL.md`를 받습니다. 이 문서는 KR·JP·TW 데이터에서 도출된
것이므로, 네 국가에 대해서는 검증된 원리가 아니라 **이식 가설**로 읽어야 합니다.

## 이관하지 않은 것

- **레퍼런스 이미지** — 아카이브의 `references/KR/img/`(약 4MB)는 가져오지 않았습니다. 본문
  28건에 남아 있는 `img/...` 링크는 이 사본에서 열리지 않습니다. 캡션과 `visual:` 블록의
  텍스트 묘사는 그대로 있습니다.
- **아카이브의 `research/` 조사 원문** — `stage3-korea.md`, `verification-pass.md` 등 원리
  문서가 각주로 인용하는 조사 원문은 아카이브에만 있습니다. 이 사본의 원리 문서에서 해당 경로
  참조는 아카이브를 가리키는 출처 표기로 읽으세요.
- **JP·TW 레퍼런스 본문** — 아카이브에 `jp-001`~`jp-032`, `tw-001`~`tw-018`가 있습니다.
- **아카이브 `context/README.md`** — 아카이브 기준 디렉터리 구조를 설명하므로 이 사본의
  구조와 어긋납니다. 이 파일이 그 역할을 대신합니다.

## 남아 있는 데이터 결함 (지어내서 메우지 말 것)

- **`core/FACTS.md`의 답란 15개가 전부 비어 있습니다.** 배포 상태, 위젯 인터랙션, 스타일 옵션
  수, 개발 비하인드, 화자·계정 정책, 댓글 게이트 운영 리소스, 촬영 정책이 미기입입니다. 이
  공백은 팀이 채워야 하며, 그때까지 이 사실들을 전제로 한 주장은 캡션에서 빼야 합니다
  (`core/PIPELINE-SCOPE.md` 참조).
- KR 41건 중 다수가 `views: null`입니다. 도달을 논하는 KR 서술은 대부분 반응 수로부터의
  추론입니다.
- `kr-025`의 `views: 39`(좋아요 163)는 물리적으로 불가능한 값입니다.
- 일부 레퍼런스는 `visual:` 블록이 YAML frontmatter 경계 안팎 어디에 오는지 파일마다
  다릅니다. 기계적으로 파싱할 때 본문이 딸려 나오거나 누락될 수 있습니다.
- `PRINCIPLES-KR` 원리 13(영상 우위)은 대조군을 세어본 적이 없어 **미검증**입니다.
