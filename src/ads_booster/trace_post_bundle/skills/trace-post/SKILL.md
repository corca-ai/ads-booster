---
name: trace-post
description: Trace 컨셉 카드로 국가별 캡션·일정·공통 배경 잠금화면·폰을 든 사진을 자동 생성하거나 중단된 생성 런을 재개한다. “귀여움으로 게시물 만들어줘”, “trace-post”, Trace 국가별 이미지 생성 요청에 사용한다. 규칙 설계·성과 조사·실제 SNS 게시 요청 자체를 대신 처리하지 않는다.
---

# Trace 게시물 자동 생성

사용자는 컨셉을 선택한다. 이 스킬은 캡션·일정·현지화·입력 조립·이미지·검수를 끝내고 국가별 결과를 보여준다. **자동형**이다. 중간에 관여하며 만들기를 원하면 그 요청을 존중하되 별도 관여형 스킬이 구현된 것으로 말하지 않는다.

## 저장소와 실행자

이 스킬은 Trace 저장소와 함께 사용한다. 먼저 현재 작업 폴더 또는 스킬 위치의 상위에서 `context/POST-RUN.md`, `concepts/`, `scripts/build_input.py`가 함께 있는 저장소 루트를 찾는다. 설치된 스킬만 있고 저장소를 찾지 못하면 저장소 경로 하나만 묻는다. 아래 모든 명령은 찾은 루트를 `--repo`로 전달하거나 그 루트에서 실행한다. 규칙 사본을 스킬 안에 만들지 않는다.

실행 모델을 사용자가 현재 요청에 명시했으면 그 선택과 reasoning effort를 우선한다. 명시 선택이 없을 때의 비용 기본값은 **GPT-5.6 Luna / medium**이다. 별도 실행 수단이 필요하면 이 스킬 경로·저장소·사용자 선택값만 전달하고 대화 전체는 넘기지 않는다. 현재 실행자가 선택값을 만족하면 다시 위임하지 않는다. 모델 전환 수단이 없으면 실제 모델과 제한을 알린다. 총괄은 이미지 중복 검토를 하지 않고 실행 결과 요약과 미확인 사항만 받는다.

## 시작 또는 재개

1. [최소 실행 규칙](../../context/RUN-POLICY.md)을 읽는다. 컨셉이 없으면 `concepts/*.md`의 이름을 보여주고 고르게 한다. 자연어를 카드 이름/컨셉에 대응시키되 여러 카드면 확인한다. 현재 지원은 T2, KR 대표, recommend 자동 조립이다. 기존 규칙의 템플릿/모티프 오버라이드 외 임의 필드를 새 사용자 입력 요구로 늘리지 않는다.
2. 카드 전문을 읽고 `manage_run.py history`로 **동일 카드의 확인된 완료 런** 선택값만 읽는다. 정책대로 모티프·장소를 고른다. history가 비어 있으면 확인된 완료 이력이 없다고 기록하고 카드 첫 후보로 시작한다. 옛 실험 폴더의 존재나 사용자 승인 없는 임의 summary를 완료 이력으로 만들지 않는다. 명시적 모티프는 카드 허용 목록 안에서 우선한다. 준비되지 않은 뼈대나 템플릿을 지원한다고 말하지 않는다. 카드의 국가 블록과 recommend 필드가 준비됐는지 확인한다.
3. `manage_run.py init`으로 새 폴더·run.json·문서 스냅샷을 만든다. 스크립트는 YAML 카드 의미를 해석하지 않으므로 카드/모티프/장소/국가 대조는 이 단계의 실행자가 맡는다. 선택과 예외는 run.json 옆 기록에 남긴다. 옵션은 `manage_run.py --help`와 각 하위 명령 도움말로 확인한다.
4. 재개 요청이면 새 폴더를 만들지 않는다. 현재 요청이나 세션에서 이미 알려진 런 경로를 우선 사용한다. 경로가 없으면 최근 런의 run.json과 단계 상태만 확인해 미완료 후보를 찾는다. 후보 하나면 그 런을 이어가고 여러 개라 구분할 수 없을 때만 선택을 묻는다. 완료 런을 중단 런으로 추정하지 않는다. 지정 런의 run.json·실행 당시 규칙 사본·현재 단계 기록만 읽는다. 새 형식 런의 사본은 source-snapshots/에 있으며 재개 시 이 폴더의 규칙·도우미를 사용한다. `scripts/run_state.py status`와 [실행 도우미](../../scripts/README.md) 절차로 출력과 검수를 확인한다. 새로운 규칙으로 옛 입력을 덮어쓰지 않는다. 옛 flat 로그는 자동 변환하지 말고 복구 가능 범위를 알린다.

```sh
python3 skills/trace-post/scripts/manage_run.py history --repo REPO --card concepts/cute.md
python3 skills/trace-post/scripts/manage_run.py init --repo REPO --card concepts/cute.md --motif '선택한 모티프' --place '선택한 장소'
```

## 생성

[통합 실행 계약](../../context/POST-RUN.md)을 기준으로 아래 순서를 수행한다. 계약의 수량·사용 조건·검수·재시도 규칙을 그대로 사용한다.

1. **캡션·공통 일정:** 카드 + [CAPTION-RULES](../../context/core/CAPTION-RULES.md) + [FACTS](../../context/core/FACTS.md)를 읽고 package.json을 작성한다. recommend는 카드 고정 문장으로 조립한다. 과거 채팅·옛 후보·참고 문서 전체는 읽지 않는다.
2. **현지화·조립:** 같은 실행에서 localization.json을 작성한다. 두 JSON을 모두 저장하고 검수한 뒤 `python3 RUN_DIR/source-snapshots/scripts/build_input.py RUN_DIR`로 package.md와 국가 input.md를 만든다. 실패를 임시 코드/규칙 변경으로 숨기지 않는다. 수정 전 JSON과 실패한 assembly-check.json을 보존한 후 필요한 경우에만 `--replace`로 파생 출력을 교체한다. 조립 통과는 번역 검수 통과가 아니다. review-caption.md와 review-localization.md에 실제 문구·국가별 치환 출처를 검수한 결과를 남긴다. 캡션 형식·사실성, 일정 규칙, 번역 의미·치환 출처·날짜·구조 보존 중 미완료/미확인/반려가 있으면 A를 포함한 이미지 호출을 시작하지 않는다. defects.md에 적는 것만으로 이 조건을 건너뛰지 않는다. 자연스러운 화면 헤더 번역의 제품 실기 문자열 대조 여부는 POST-RUN §3의 관찰 항목으로 별도 기록하며, 미대조를 통과로 바꾸거나 그것만으로 생성을 차단하지 않는다.
3. **주문서·프롬프트:** [IMAGE-RULES](../../context/core/IMAGE-RULES.md) + 선택된 템플릿 명세 + 국가 input.md만 사용한다. 새 런을 만든 뒤 규칙·템플릿·도우미는 RUN_DIR/source-snapshots/ 사본에서 읽고 실행한다. A의 실제 입력도 그 사본의 context/templates/T2/template.png를 사용한다. 공통 씬 메타와 프롬프트 C는 한 번 고정한다. 실제 전달할 A/B/L/C 프롬프트를 호출 전에 파일로 저장한다. 호출 시 그 파일을 도구로 전체 읽은 문자열 변수를 그대로 전달하며, 수동 재입력·요약·축약한 문자열을 대신 보내지 않는다.
4. **이미지:** 캡션·현지화 의미 검수를 `content_review.py record`로 해시 바인딩한 뒤 시작한다. 각 호출은 `image_call.py prepare`의 request 객체를 그대로 built-in image_gen에 전달하고 반환 원본 경로와 receipt를 `complete`에 넘긴다. 호출 오류는 receipt 고유 경로에 근거를 쓴 뒤 `error`(미생성 확정) 또는 `unresolved`(생성 여부 불명)로 기록한다. unresolved 요청을 재호출하지 않고 실제 source 회수 시에만 complete한다. 대표 A→B, 같은 대표 완성본에서 다른 국가 L, 국가별 C를 실행한다. C는 공통 prompt-C.txt를 강제한다. API/다른 이미지 생성기로 바꾸지 않는다.
5. **기록·검수:** prepare receipt가 begin을, complete가 canonical 출력 복사와 end를 기록한다. [검수 형식](../../context/templates/review-stage.md)의 실제 의미 판정을 receipt의 고정 `review_report_path`에 작성한 후 receipt 경로로 `image_call.py review`를 실행한다. call_id를 수동으로 옮겨 적지 않는다. 기존 호출이 in-progress면 반환 경로와 도구 상태를 먼저 확인하며 새 호출을 하지 않는다. 미확인은 통과로 바꾸지 않는다. receipt와 고정 검수 보고서는 call_id별 경로에 그대로 보존하고, 재시도할 canonical 출력·프롬프트만 필요할 때 `-v1`로 보존한다. 도구 원본은 source-path로 연결한다. base64나 전체 도구 반환을 텍스트 로그로 출력하지 않는다.

설계와 규칙 변경은 생성 런 밖에서 한다. API/도구 사용량 중단은 품질 반려와 구분한다. 이미지 경로·호출 상태·실패 사유를 남겨 이어갈 수 있게 한다.

## 마무리

1. POST.md에 국가별 캡션, 잠금화면/씬 링크, 답글·튜토리얼 준비 상태를 정리한다. review.md에는 판정과 남은 관찰을, defects.md에는 문서·입력·도구 공백을 남긴다.
2. 국가별 C 프롬프트가 공통 파일과 동일한지 확인하고 `manage_run.py finish`로 완료 여부와 결과 해시를 기록한다. 실패 또는 검수 대기 상태를 완료 이력에 넣지 않는다. 이력은 **생성 완료**이며 게시 완료가 아니다.
3. 사용자에게 결과 링크와 핵심 검수만 짧게 보여준다. 실제 SNS 게시·커밋·푸시는 생성 요청의 범위에 포함하지 않는다.

```sh
python3 skills/trace-post/scripts/manage_run.py finish --repo REPO --run RUN_DIR
```
