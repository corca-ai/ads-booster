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
