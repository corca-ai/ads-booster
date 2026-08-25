# AGENTS.md

이 문서는 `ads-booster` 개발 지침입니다. 런타임 성격, 사용자 프로필, 장기 기억과 시스템
프롬프트는 범위가 아닙니다.

## 프로젝트 개요

`ads-booster`는 Trace 마케팅 에이전트, 팀 워크스페이스와 Appium 이미지 파이프라인을
제공합니다. 진입점은 `trace-ads`, `trace-agent`, `trace-capture`, `trace-compose`, `trace-run`입니다.

## 제품 기준 환경

- 제품 동작의 최우선 기준은 현재 worktree가 아니라 처음 설치한 격리된 `trace-agent`
  환경입니다.
- 설치, PATH, CLI 노출, 기본 설정, 상태 디렉터리와 service lifecycle은 fresh install에서
  확인합니다.
- worktree의 source, `uv run`, local venv 성공은 후보 변경의 개발 근거이며 설치된 제품이
  작동한다는 증거가 아닙니다.
- public installer나 원격 설치 명령은 실제 배포된 URL과 ref를 fresh environment에서 실행한
  경우에만 작동한다고 주장합니다.

## 기준 문서와 읽기 순서

모든 문서를 매 작업마다 읽지 않습니다. 작업 범위에 필요한 기준만 선택해 읽습니다.

1. 제품 동작과 설치 가능성은 fresh installed `trace-agent` 환경에서 먼저 확인합니다.
2. worktree의 코드와 테스트는 구현 분석과 후보 변경 검증에 사용합니다.
3. 프로세스와 실행 흐름은 [시스템 아키텍처](./docs/architecture/system.md)를 확인합니다.
4. 코드 책임과 배치는 [코드 아키텍처](./docs/architecture/code.md)를 확인합니다.
5. 사용자 명령과 환경변수는 [README](./README.md)를 기준으로 확인합니다.
6. 테스트 범위와 실제 QA는 [테스트와 검증](./docs/development/testing.md)을 따릅니다.
7. Git 작업은 [GitHub 컨벤션](./docs/conventions/github.md)을 따릅니다.
8. 실패와 버그는 [실패 처리 원칙](./docs/principles/failure-handling.md)을 따릅니다.

`tasks/todo.md`와 `tasks/lessons.md`는 작업 기록이며 제품 동작의 근거가 아닙니다.

## 작업 시작

- 먼저 `git status --short`로 현재 브랜치와 작업트리 변경을 확인합니다.
- 기존 변경과 untracked 파일은 사용자의 작업으로 간주하고 보존합니다.
- 요청 범위 밖의 파일을 정리, 복원, 이동, 삭제하지 않습니다.
- `.codegraph/`가 있으면 코드 위치와 호출 경로를 이해할 때 CodeGraph를 먼저 사용합니다.
- CodeGraph가 없거나 stale, lock, 동기화 실패를 보고하면 해당 결과를 현재 코드로 간주하지
  않고 필요한 live source만 직접 확인합니다.
- 읽기 전용 분석, 설명, 리뷰 요청은 사용자가 변경도 요청하지 않은 이상 코드나 문서를
  수정하지 않습니다.

## 문서 동기화

- 진입점, 프로세스 구성, 실행 흐름, 상태 저장, 인증·승인 또는 외부 시스템 경계를 바꾸면
  같은 변경에서 `docs/architecture/system.md`를 갱신합니다.
- package 책임, 의존 방향, composition root, type owner 또는 코드 배치 규칙을 바꾸면 같은
  변경에서 `docs/architecture/code.md`를 갱신합니다.
- 테스트 위치, 선택 기준, 공식 검증 명령 또는 실제 QA 기준을 바꾸면 같은 변경에서
  `docs/development/testing.md`를 갱신합니다.
- 사용자 명령, 환경변수, 설치 또는 운영 절차를 바꾸면 같은 변경에서 `README.md`를
  갱신합니다.
- 아직 구현되지 않은 설계 문서는 `Status: Draft`와 미구현 범위를 표시합니다.

필요한 문서가 빠진 구조 변경은 완료한 것으로 간주하지 않습니다.

## 핵심 불변식

- `trace-agent`는 `trace-ads`의 호환 진입점입니다.
- canonical conversation history를 보존하고 compaction은 provider projection만 줄입니다.
- shared workspace context는 private chat에서 read-only입니다.
- private session은 workspace, member, session scope를 모두 적용합니다.
- secret을 로그나 테스트 산출물에 기록하지 않고 외부 side effect는 승인 또는 worker 경계를
  통과합니다.
- artifact는 설정된 root와 digest provenance를 유지하며 확인할 수 없는 side effect를
  무조건 재시도하지 않습니다.
- 생성 결과는 artifact 검증과 사람의 review 승인을 거쳐야 합니다.
- 현재 런타임은 Notion, Threads 또는 다른 외부 마케팅 채널에 자동 게시하지 않습니다.

## 검증

수정한 동작과 직접 영향받는 경계만 검증합니다. 전체 test suite와 repository 전체 정적
검사를 관성적으로 실행하지 않습니다. 전체 검증은 사용자가 명시적으로 요청한 경우에만
실행합니다. 변경과 무관한 test, 중복 test, production owner가 없는 orphan test를 만들지
않습니다. 사용자 동작은 worktree command보다 fresh installed command로 우선 확인합니다.
test selection, authoring gate, focused command와 실제 표면 QA는
`docs/development/testing.md`를 따릅니다.

## 실패 처리

버그 수정은 직접 원인, 동일 패턴, 반복을 만드는 구조, 처리 상태를 확인합니다. 증상을
우회하는 retry, timeout 증가, consumer-only guard로 원인 소유권을 숨기지 않습니다.

재현 가능한 버그는 수정 전 실패하는 회귀 테스트로 고정하고 같은 사용자 경로를 다시
실행합니다. 세부 기준은 `docs/principles/failure-handling.md`를 따릅니다.

## Git과 GitHub

Git 작업을 시작하기 전에 `docs/conventions/github.md` 전체를 읽고 적용합니다.

- 커밋은 사용자가 요청할 때만 합니다.
- 푸시는 사용자가 명시적으로 요청할 때만 합니다.
- Pull Request 생성, 병합과 GitHub 상태 변경은 사용자가 요청할 때만 합니다.
- stage할 때 요청 범위의 파일만 포함합니다.
- 기존 dirty worktree와 `tasks/` 변경을 임의로 stash, restore, 삭제하지 않습니다.
