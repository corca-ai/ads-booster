# 온프레미스 마케팅 에이전트 운영 문서

신규 설치와 일상 운영은 [CLI 설치 안내](slack-launch-guide.md)를 따른다.
설치 → Slack 앱 생성 → 초기 설정 → 서비스 시작 → Slack 연결 → 실제 사용·업데이트
검증 순서로 진행한다.

Status: Candidate — 문서는 설치 절차를 설명하며 실제 배포 완료를 뜻하지 않는다.
공개 설치는 해당 변경의 main 병합과 전용 CI 성공을 확인한 뒤 실행한다. 로컬 후보 검증,
공개 URL의 fresh install, 실제 Ubuntu·Slack 운영 검증은 각각 구분한다.

| 문서 | 역할 |
| --- | --- |
| [CLI 설치 안내](slack-launch-guide.md) | 신규 설치, Slack 연결, 서비스 운영과 실제 완료 확인의 기준 |
| [검증 기록](verification.md) | 2026-09-07 후보별 검증 근거와 당시 미검증 항목 |
| [수동 wheel 설치·복구 기록](legacy-wheel-recovery.md) | 2026-09-07 후보의 ZIP/wheel, 수동 서비스·설정·스케줄러·복구 절차 보존 |

수동 wheel 기록은 기존 설치를 대조·복구할 때 참고한다. 새 사용자에게 ZIP 전달이나
수동 systemd 등록을 기본 절차로 안내하지 않는다.

## 설정 참고 자료

- [환경변수 예시](agent.env.example)
- [Slack 사용자·승인 권한 예시](slack-installation.example.json)
- [일일 조사 입력 예시](daily-research.example.json)

설정은 CLI 설치 안내에 따라 생성한다. 예시 파일은 설정 의미를 확인하기 위한 자료이며,
기존 설정을 덮어쓰는 용도가 아니다.

관리형 서버의 기본 포트는 `8090`이다. 기존 `8765` 설치는
[포트 전환 절차](slack-launch-guide.md#기존-8765-설치에서-8090으로-전환)를 따른다.
모델 예시는 `gpt-6-astra`이며, 실제 서버 Codex 계정에서 사용 가능한 모델을 확인한다.

## Team knowledge configuration (source wiring; verification deferred)

The on-premises service can enable the server-owned knowledge store by setting all three absolute
paths together:

```bash
export TRACE_MARKETING_KNOWLEDGE_ROOT='/absolute/path/to/knowledge'
export TRACE_MARKETING_KNOWLEDGE_CONTROL_ROOT='/absolute/path/to/knowledge-control'
export TRACE_MARKETING_KNOWLEDGE_POLICY='/absolute/path/to/knowledge-control/policy.json'
```

Leave all three unset to keep knowledge disabled. A partial set is rejected. The service user must
own the root and control directories with mode `0700`; the policy file and control `identity.json`
must be mode `0600`. `service run` starts the knowledge owner, ingress outbox, curation jobs, index
worker, and memory-view worker in the same process as the canonical Agent Service. The owner lock
rejects a second process using the same root.

Authenticated Slack shared threads enter workspace scope. Private DMs retain member and conversation
scope and expose read-only knowledge search/get, memory get/explain, and source read capabilities;
they cannot write shared memory, schedule jobs, purge data, or send external effects. Corrections and
message edits/deletes create a pending fence before the affected Run is prepared again.

The service assembles bounded revision- and digest-bound context for the canonical Run.
Preexisting transfer replicas remain `purge_pending` until separately reconciled. No remote purge
transport is configured; local cleanup neither executes nor acknowledges external deletion. Validate a fresh installed Linux service and live
Slack/Codex separately from local tests.

The `trace-marketing knowledge` command group is registered as a local admin surface. Every command
requires `--root`, `--control-root`, and `--policy`. The current commands are `init --workspace`,
`doctor`, `ingest --envelope FILE [--attachment ORDINAL=/absolute/path]`,
`run [--model MODEL] [--service-database PATH] [--once|--until-idle [--flush-batches]]`,
`search --query TEXT [--limit N]`, `get --id ID [--revision REVISION]`,
`context --request FILE [--brand ID]`, `schedule --request FILE`, `backup --destination PATH`,
`restore --backup PATH`, `retract --source ID`, `purge --request ID`, and
`questions --pending|--answer ID --text TEXT`. Subgroups are `memory get|explain|correct|consolidate`,
`brand register|list`, and `task open|close`. `memory consolidate` requires `--until-idle`, and
`--flush-batches` requires `--until-idle`.

This is a source-implemented reference surface for one private store. Installed help, fresh Linux
installation, live Slack/Codex, and deployment verification remain
deferred.
