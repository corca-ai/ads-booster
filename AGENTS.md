# AGENTS.md

This file provides guidance to coding agents when working in this repository.

## 프로젝트 개요

새 요구사항으로 전환하기 위해 애플리케이션 코드, 설정, 의존성, 테스트를 제거한 초기화 상태입니다.
새 요구사항이 확정되기 전에는 이전 헬스 체크와 광고 데이터 스키마를 다시 만들거나 전제하지 않습니다.

## 현재 아키텍처 기준

- `docs/architecture.md`는 의도적으로 유지하지 않습니다. 명시적인 요청 없이는 새로 만들거나 참조하지 않습니다.
- 현재는 실행 가능한 애플리케이션 아키텍처가 없습니다. 새 요구사항이 확정된 뒤에도 구조 기준은 코드와 이 `AGENTS.md`에만 두며, 별도 아키텍처 문서는 만들지 않습니다.

## 컨벤션 참조

Git과 GitHub 작업의 유일한 기준은 [GitHub 컨벤션](./docs/github-conventions.md)입니다.

Notion의 레포 온보딩과 컨벤션 원문은 이 문서의 Draft PR, 전체 브랜치 타입명 규칙을 뒷받침하지만, Notion은 이 레포 문서를 기준으로 안내합니다. 다른 프로젝트의 Notion Git/머지 규칙은 이 문서를 대체하지 않습니다.

브랜치 생성, 커밋, Pull Request, 머지 작업을 시작하기 전에는 이 문서 전체를 읽고 적용합니다.
부분만 발췌하거나 추정하지 않습니다.

## 최상위 필수 규칙

- Git 작업은 `docs/github-conventions.md`의 브랜치, 커밋, Draft Pull Request, Squash Merge 규칙을 따릅니다.
- 커밋은 사용자가 요청할 때만 합니다.
- 푸시는 사용자가 명시적으로 요청할 때만 합니다.
- Pull Request 생성, 병합, GitHub 상태 변경은 사용자가 요청할 때만 합니다.
- 새 요구사항으로 코드 동작, 실행 방법, 환경변수, 마이그레이션 절차를 도입하거나 바꾸면 관련 로컬 문서도 같은 변경 범위로 갱신합니다.
