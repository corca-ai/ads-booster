# 실행 도우미

Python 표준 라이브러리만 사용한다. 이미지 모델을 호출하거나 선택 정책을 대신 실행하지 않는다.

## 입력 조립

`python3 scripts/build_input.py RUN_DIR`

먼저 run.json·package.json·localization.json을 모두 저장한다. 검증 후 JSON 정본에서 읽기용 package.md와 국가별 input.md를 만든다. assembly-check.json은 항상 최근 시도 결과를 표시한다. 기존 출력 내용이 다르면 거부하며, 이전 버전을 보존한 뒤 명시적으로 `--replace`를 써야 교체한다. 번역 의미·카드 치환 출처·캡션 문체는 별도 검수다.

## 캡션·현지화 검수 승인

`build_input.py`가 생성한 국가별 `input.md`와 JSON 정본을 사람 또는 모델이 의미 기준으로 검수하고 `review-caption.md`·`review-localization.md`를 작성한 뒤 기록한다.

```sh
python3 RUN/source-snapshots/scripts/content_review.py record --run RUN --reviewer ACTUAL_REVIEWER --caption-verdict pass --localization-verdict pass
```

도우미는 보고서의 PASS 문자열을 검색하지 않는다. 의미 검수자의 명시 판정과 run/package/localization, 두 보고서, 모든 국가 input의 해시를 묶는다. 누락·반려·변경은 이미지 prepare와 최종 finish를 차단한다.

## 이미지 호출 receipt와 재개

```sh
python3 RUN/source-snapshots/scripts/image_call.py prepare --run RUN --stage C --country kr --prompt-file RUN/prompt-C.txt --input-file RUN/kr/final.png --executor ACTUAL_MODEL_AND_EFFORT
```

prepare는 저장된 prompt의 UTF-8 전문과 참조 경로를 `request` JSON으로 반환하고 call_id·canonical 출력·로그·고정 검수 보고서 경로를 receipt에 저장한다. `functions.exec`에서 stdout을 `JSON.parse`한 뒤 `await tools.image_gen__imagegen(plan.request)`로 request 객체를 그대로 넘긴다. 수동 재입력·요약·축약을 금지한다. CLI Python은 built-in image_gen을 호출하지 않고 별도 API로 바꾸지 않는다.

도구 호출이 오류로 끝나면 receipt의 `error_evidence_path`에 원문 오류·반환 상태를 적고 다음 중 하나를 기록한다.

```sh
# 도구가 출력을 만들지 않았음이 확인된 경우
python3 RUN/source-snapshots/scripts/image_call.py error --receipt RECEIPT_PATH --evidence-file RECEIPT_ERROR_EVIDENCE_PATH

# serialization/통신 오류 등으로 출력 생성 여부를 알 수 없는 경우
python3 RUN/source-snapshots/scripts/image_call.py unresolved --receipt RECEIPT_PATH --evidence-file RECEIPT_ERROR_EVIDENCE_PATH
```

`unresolved`면 중단하고 도구 반환·저장 경로에서 실제 source를 먼저 회수한다. 같은 prepared request를 다시 image_gen에 보내지 않고, source를 회수한 경우에만 아래 complete를 실행한다. `error`로 닫힌 receipt도 재사용하지 않는다. 재시도가 필요하면 새 prepare가 새 call_id를 만들어야 한다.

도우미는 receipt를 우회한 built-in tool 직접 재호출을 기술적으로 막을 수 없다. 따라서 재호출 금지는 실행자 계약이며, finish는 receipt에 연결된 호출만 완료 산출물로 인정한다. 도구 실제 호출 수와 로그 수가 다르면 그 차이를 검수 보고서에 남긴다.

```sh
python3 RUN/source-snapshots/scripts/image_call.py complete --receipt RECEIPT_PATH --source-path /actual/generated/image.png
```

complete는 prepared receipt 또는 실제 source를 회수한 unresolved receipt의 출력을 canonical 경로로 복사하고 end를 기록한다. 프롬프트·입력이 prepare 후 변경되었거나 다른 canonical 출력이 있으면 거부한다. 복사 후 end 전에 중단됐다면 동일한 원본 해시일 때만 `recovered` end로 복구하며 과거 종료 시각을 추정하지 않는다.

호출별 receipt의 `review_report_path`에 검수 형식에 따른 의미 판정을 작성한 뒤:

```sh
python3 RUN/source-snapshots/scripts/image_call.py review --receipt RECEIPT_PATH --report-file RECEIPT_REVIEW_REPORT_PATH --reviewer ACTUAL_REVIEWER --pass
python3 RUN/source-snapshots/scripts/run_state.py status --log RUN/calls.jsonl
```

`--pass`는 사람/모델의 실제 판정을 기록한다. 보고서 문구로 추정하지 않는다. receipt와 고정 검수 보고서는 call_id별 고유 경로에 그대로 보존한다. 재시도할 canonical 출력과 프롬프트만 필요할 때 `-v1` 이름으로 보존하고 prepare에 `--retry 1`을 준다. 같은 단계·국가의 미종료 호출이 있으면 새 prepare를 거부한다. 기록 없는 구형 frozen 런은 자동 변환하지 않는다.

경로는 절대경로로 저장한다. 옛 run-02 flat 로그는 새 도우미로 다시 쓰지 않는다(명시적 거부). 새로운 런부터 begin/end/review 형식을 사용한다. 각 호출 기록은 append-only이고 기존 이벤트를 고치지 않는다.

이 도우미는 단계 상태 기록만 한다. 프롬프트가 바뀌면 해당 단계가 needs-review가 되지만 후속 단계 재실행 여부는 POST-RUN 의존 순서에 따라 실행자가 판단한다. 파일 쓰기 전체가 트랜잭션인 것은 아니며 동시 실행은 각 런 폴더를 분리한다.
