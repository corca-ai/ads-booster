# GitHub 컨벤션

이 문서는 `ads-booster`의 브랜치, 커밋, Pull Request 작업 규칙을 정의합니다.
코드 변경은 이 문서의 흐름을 따르고, 작업을 시작하기 전에 현재 브랜치와 원격 상태를 확인합니다.

## 핵심 규칙

- 일반 작업은 `main`에 직접 커밋하지 않고 작업 브랜치와 Pull Request를 사용합니다.
- 브랜치 타입은 축약하지 않고 `feature/`, `fix/`, `hotfix/`처럼 전체 이름을 사용합니다. `feat/`는 브랜치 접두사로 사용하지 않습니다.
- 커밋 메시지는 `<type>: <message>` 형식을 사용합니다.
- Pull Request는 항상 Draft로 먼저 생성하고, 리뷰 준비가 끝난 뒤 `Ready for review`로 전환합니다.
- 승인된 Pull Request는 Squash Merge합니다.
- `main`에 변경이 반영되면 해당 커밋의 태그와 GitHub Release를 반드시 갱신합니다.

## 브랜치 규칙

### 기본 브랜치

- `main`: 통합 및 배포 기준 브랜치입니다.
- 일반 작업을 `main`에 직접 커밋하지 않습니다.
- 긴급한 운영 장애 수정은 `hotfix/` 브랜치로 분리합니다.

### 작업 브랜치 이름

```text
<type>/<short-description>
```

| 접두사 | 용도 | 예시 |
| --- | --- | --- |
| `feature/` | 새로운 기능 추가 | `feature/add-campaign-health-check` |
| `fix/` | 일반적인 버그 수정 | `fix/handle-missing-database-url` |
| `hotfix/` | 긴급한 운영 장애 수정 | `hotfix/restore-health-endpoint` |

브랜치 이름은 짧고 작업 목적이 드러나도록 작성합니다. 단어는 소문자와 하이픈을 사용하고,
서로 다른 목적의 변경을 하나의 브랜치에 섞지 않습니다.

## 커밋 메시지 규칙

### 형식

```text
<type>: <message>
```

예시:

```text
feat: add campaign health check
fix: handle missing database url
refactor: simplify database options
docs: add GitHub conventions
test: cover health endpoint failure
chore: update development dependencies
```

### 타입

- `feat`: 기능 추가
- `fix`: 버그 수정
- `refactor`: 동작 변경 없는 구조 개선
- `docs`: 문서 변경
- `test`: 테스트 추가 또는 수정
- `chore`: 빌드, 도구, 의존성 등 유지보수 작업

커밋은 하나의 의도를 담는 의미 있는 단위로 만듭니다. 커밋하기 전에 변경 파일과 diff를 확인하고,
비밀번호·토큰·`.env` 파일 같은 비밀 정보를 포함하지 않았는지 확인합니다.

## 작업 흐름

### 1. 최신 `main`에서 작업 시작

```bash
git status --short
git switch main
git pull --ff-only origin main
git switch -c feature/<short-description>
```

작업 중인 변경 사항이 있으면 먼저 그 상태를 확인합니다. 다른 작업을 덮어쓰거나 섞지 않습니다.

### 2. 의미 있는 단위로 커밋하고 원격에 푸시

```bash
git diff --check
git diff --stat
git add <intended-files>
git commit -m "<type>: <message>"
git push -u origin <branch-name>
```

### 3. Draft Pull Request 생성

```bash
gh pr create --draft --base main --title "<type>: <summary>" --body "<description>"
```

Pull Request 본문에는 최소한 다음 내용을 포함합니다.

- 변경 목적과 해결하려는 문제
- 주요 변경 내용
- 실행한 검증 명령과 결과
- 마이그레이션, 환경변수, 배포 시 주의사항

### 4. 리뷰와 머지

1. CI와 로컬 검증을 확인합니다.
2. 변경 파일과 Pull Request 설명이 현재 head와 일치하는지 확인합니다.
3. 리뷰 준비가 끝나면 `Ready for review`로 전환합니다.
4. 피드백을 반영하고 같은 브랜치에 푸시합니다.
5. 승인 후 Squash Merge합니다.
6. 머지 직후 로컬 `main`을 다시 동기화합니다.

```bash
git switch main
git pull --ff-only origin main
```

## `main` 변경 후 태그와 GitHub Release

`main`에 변경이 반영되면, 그 변경은 태그와 GitHub Release가 갱신되어야 완료됩니다. 이 절차는
`main`의 실제 최신 커밋만 대상으로 하며, 다른 브랜치나 로컬 HEAD를 릴리스 대상으로 삼지 않습니다.

1. 최종 `main` 변경 범위를 기준으로 새 버전과 릴리스 노트를 확정합니다.
2. 원격 `main`을 다시 읽어 태그 대상 SHA를 확인합니다.
3. 그 SHA에 주석 태그 `v<version>`을 생성하고 원격에 푸시합니다.
4. 같은 태그를 가리키는 GitHub Release를 생성하거나, 이미 있다면 릴리스 노트를 갱신합니다.
5. 원격 태그의 peeled SHA가 `origin/main` SHA와 같은지, GitHub Release의 태그가 의도한 `v<version>`인지 다시 확인합니다.

```bash
git fetch origin main --tags
git rev-parse origin/main
git tag -a v<version> <main-sha> -m "v<version>"
git push origin v<version>
# 새 GitHub Release일 때
gh release create v<version> --target <main-sha> --title "v<version>" --notes-file <release-notes-file>
# 이미 GitHub Release가 있을 때
gh release edit v<version> --title "v<version>" --notes-file <release-notes-file>
git ls-remote --tags origin refs/tags/v<version> refs/tags/v<version>^{}
gh release view v<version> --json tagName,targetCommitish,url
```

태그 푸시와 GitHub Release 생성·수정은 외부 상태 변경이므로, 사용자가 `main` 병합 또는 릴리스를 명시적으로 요청한 작업에서만 실행합니다. 검증 전에는 릴리스 완료를 주장하지 않습니다.

## 머지 전 체크리스트

- [ ] 현재 브랜치가 의도한 Pull Request의 head 브랜치인지 확인했습니다.
- [ ] `git status --short`와 `git diff --stat`로 변경 범위를 확인했습니다.
- [ ] `git diff --check`가 통과했습니다.
- [ ] 관련 테스트, `bun run check`, `bun run build`를 실행했습니다.
- [ ] Pull Request가 Draft 상태로 생성되었고 설명이 실제 변경과 일치합니다.
- [ ] 비밀 정보와 무관한 변경을 포함하지 않았습니다.
- [ ] 리뷰 승인 후 Squash Merge할 준비가 되었습니다.
- [ ] `main` 반영 뒤 새 태그와 GitHub Release가 같은 `main` 커밋을 가리키는지 확인했습니다.
