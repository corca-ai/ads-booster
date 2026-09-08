# 온프레미스 마케팅 에이전트: Slack + main 자동 업데이트

신규 설치는 [CLI 설치 안내](slack-launch-guide.md)를 따른다. 아래 ZIP/wheel 수동 절차는
이전 후보 설치와 복구 참고용이며 새 사용자의 기본 설치 경로가 아니다.

Status: Candidate implementation — 실제 Ubuntu 서비스·Slack 왕복·main 자동 배포 검증 전.

이 디렉터리와 전달한 wheel은 아직 공개 v0.4.21과 다른 수정 후보다. 회사 OAuth 서비스가
없는 현재 설치는 **Slack 전용 모드**를 사용한다. 멘션·스레드·DM과 `/trace`로 조사, 추가 답변, 승인·거절,
결과 확인과 매일 자동 조사를 이용한다. 웹 UI·웹 API는 닫힌다. Cloudflare 이메일 로그인은
이 모드에 필요하지 않으며 아직 구현된 것으로 주장하지 않는다.

## 직접 할 일

**PR 병합부터 실제 Slack 사용까지는 [단계별 설치 안내](slack-launch-guide.md)를 따른다.**
Slack 앱 생성 → 토큰 입력과 서버 설치 → Event URL 검증 순서다. bootstrap manifest는
서버가 뜨기 전에 앱을 생성할 때, full manifest는 서버와 Tunnel이 연결된 뒤 사용한다.
최신 패키지는 `trace-agent-server-setup-20260907-slack-agent.zip`이다.
이전 autoupdate ZIP은 멘션·스레드·DM이 없는 후보다.

## 서버 Codex에게 전달

> 전달한 최신 자동 업데이트 패키지를 찾아 이 README와 verification.md를 읽고 설치해줘.
> Ubuntu 22.04.5 x86_64이고 Codex는 ChatGPT 로그인 상태야. 기존 cloudflared-ear.service는
> 실행 중이니 변경하지 마. 마케팅 터널은 별도 서비스·토큰 파일을 사용해줘.
> Slack 전용 모드로 설정해줘. 회사 OAuth와 웹 로그인은 요구하지 마.
> 아래 agent-manager.py로 격리 설치하고 trace-marketing.service와 update.service/timer를
> 사용자 서비스로 등록해줘. GitHub 읽기 권한과 사용자 서비스 PATH, linger도 확인해줘.
> 비밀값은 내가 서버에 입력할 위치를 준비하고 채팅이나 로그에 출력하지 마.
> 실제 멘션·스레드·DM·/trace 요청과 승인·일일 조사, 서비스 재시작 후 기록 보존,
> timer와 설치 SHA를 확인해줘. 실제 재부팅은 다른 서비스 영향을 먼저 확인해줘.
> main 변경이 없거나 아직 병합되지 않았다면 자동 업데이트 설정과 실제 적용 검증을 구분해줘.
> 설치본이나 서버에만 별도 코드를 고치지 말고 필요한 변경은 저장소의 브랜치로 돌려줘.

## 설치 순서

전용 사용자에서 Python 3.10+(Ubuntu 시스템 Python), uv, git, gh, 공식 Codex CLI를 확인한다.
에이전트용 Python 3.14는 uv가 준비한다. Appium과 Xcode는 필요 없다.
기존 `trace-marketing.service`나 `~/.local/share/trace-marketing-server/current`가 있으면
정체와 상태를 먼저 확인한다. 관리자가 자동 덮어쓰지 않는다.

패키지 압축을 푼 디렉터리에서 실제 wheel 경로를 지정한다.

```bash
python3 agent-manager.py install \
  --wheel ./trace_appium_capture-0.4.21-py3-none-any.whl \
  --requirements ./requirements.lock.txt
```

이 명령은 의존성을 잠긴 목록에서 설치하고 새 환경의 installed command를 검사한다.
실행 파일은 `~/.local/share/trace-marketing-server/current/.venv/bin/trace-marketing`이다.
기존 `~/.local/bin/trace-marketing`을 덮어쓰지 않는다. 새 서비스는 current만 사용한다.

배치할 파일:

- `~/.config/trace-marketing/agent.env` ← [환경 예시](agent.env.example)
- `~/.config/trace-marketing/slack-installation.json` ← [권한 예시](slack-installation.example.json)
- `~/.config/systemd/user/trace-marketing.service`
- `~/.config/systemd/user/trace-marketing-update.service`
- `~/.config/systemd/user/trace-marketing-update.timer`

설정 디렉터리는 700, 비밀 파일은 600으로 제한한다. `TRACE_MARKETING_SLACK_ONLY=1`을 유지한다.
member_id는 운영자가 정한 고정 ID(예: Slack 사용자 ID와 같은 값)를 사용하면 된다.
OAuth sub가 필요하지 않다. 승인 담당자만 can_approve를 true로 둔다.
TRACE_MARKETING_TENANT와 Slack 권한 파일의 tenant_id는 같아야 한다.
일단 등록한 동일 사용자의 ID/승인 권한을 변경하면 충돌 시 시작이 거절된다.
제거는 파일에서 빼고 서비스를 재시작하면 새 요청·대기 작업에 반영된다.

Codex/uv/gh가 npm/nvm 등 별도 경로에 있다면 **service와 update.service 양쪽 PATH**에
실제 bin 경로를 추가한다. 모델명은 해당 Codex 계정에서 실제 사용할 수 있는 값으로 지정한다.
서버 Codex가 같은 사용자에서 `codex login status`와 실제 모델 호출을 확인해야 한다.

```bash
systemctl --user daemon-reload
systemctl --user enable --now trace-marketing.service
curl -fsS http://127.0.0.1:8090/health
systemctl --user enable --now trace-marketing-update.timer
systemctl --user list-timers trace-marketing-update.timer
```

재부팅 후 로그인 없이 실행하려면 관리자 권한으로 해당 계정의 `loginctl enable-linger`를
설정한다. 계정 권한을 확인한 후 서버 Codex에게 맡긴다.
Cloudflare는 별도의 cloudflared 마케팅 서비스가 `localhost:8090`에 연결하게 한다.
기존 ear 서비스와 충돌하는 기본 `cloudflared service install`을 그대로 실행하지 않는다.
Slack Events와 명령 URL에는 Cloudflare Access 로그인 리디렉션을 적용하지 않는다.
이 경로는 Slack 원본 본문 서명, 타임스탬프, app/team/channel/member를 서버에서 검증한다.

## 자동 업데이트 동작

- 부팅 후 2분, 이후 이전 검사 종료 기준 5분마다 `main`을 fetch한다. 브랜치명뿐 아니라
  정확한 commit SHA를 기록한다. README만 바뀌어도 main 변경이면 검사한다.
- 그 SHA의 `Verify on-prem agent` GitHub Actions 검사가 완료·성공해야 설치한다.
  이 검사에는 서버와 도구 어댑터 계약 검증이 포함된다. 검사 없음·미완료·실패·권한 오류이면
  기존 버전을 유지한다. 별도 Mac 릴리스나 리뷰 체크 결과는 서버 설치를 막지 않는다.
- 이미 적용한 SHA면 작업하지 않는다. 기존 SHA의 후손만 허용한다. main 강제 되돌리기는
  자동 적용하지 않고 운영자가 검토한다.
- 별도 릴리스 디렉터리에 checkout 후 `uv sync --locked --no-dev --no-editable`로 설치한다.
  프로토콜과 설치된 service doctor가 실패하면 실행 중인 서비스는 건드리지 않는다.
- 유지보수 파일을 만들면 HTTP 접수와 queue recovery, 작업·알림 전송, 스케줄러가 새 일을
  시작하지 않는다. 이미 시작한 작업은 끝까지 진행한다. 5분 내 active=0이 안 되면 교체를
  미루고 접수를 다시 연다. 진행 중인 작업을 업데이트 목적으로 강제 종료하지 않는다.
- 안전한 경계에서 서비스를 멈추고 상태 루트를 백업한다. current를 원자적으로 교체하고
  새 서비스를 유지보수 상태로 시작한다. 정확한 SHA·프로토콜·active=0 건강 확인 후 접수를 연다.
- 새 버전 시작 실패 시 새 서비스를 멈추고 백업 상태·이전 설치본으로 복귀한다. 이전 서비스가
  정상인지 확인한 뒤 접수를 연다. 복구 실패 시 유지보수 상태를 유지하고 운영자 확인이 필요하다.
- 교체 중 updater 프로세스가 중단되면 다음 실행에서 transaction을 먼저 복구한다.
  활성화 확정 이후에는 새 작업이 있을 수 있으므로 DB를 과거로 되돌리지 않는다.
- 실패한 SHA는 last-failure.json에 남기고 같은 SHA를 자동 재시도하지 않는다. 다음 main 커밋을
  기다린다. 일시 장애를 해결한 뒤 같은 SHA를 다시 시도하려면 운영자가 실패 기록을 검토하고
  별도 위치로 옮긴 후 업데이트 서비스를 한 번 시작한다.

설정·비밀값은 `~/.config/trace-marketing`, Run·queue·receipt는
`~/.local/state/trace-marketing`에 유지한다. 설치본과 분리한다. 백업·이전 설치본·실패 상태는
자동 삭제하지 않으므로 서버 운영자가 용량과 보존 기간을 관리한다. 프로세스 중단 복구 검증은
전원 손실·디스크 손상 복구 보증이 아니다. 검사는 실제 Slack/Codex 외부 효과의 성공 보증도 아니다.
GitHub 원본 접근은 update.json의 허용된 저장소와 SSH/credential helper를 사용하고,
서비스의 Slack 비밀값을 updater unit에 전달하지 않는다.

상태 확인 및 수동 검사:

```bash
python3 ~/.local/share/trace-marketing-server/current/agent-manager.py status
systemctl --user start trace-marketing-update.service
journalctl --user -u trace-marketing-update.service -n 30 --no-pager
curl -fsS http://127.0.0.1:8090/health
```

`agent_update_complete`는 검사 종료를 뜻하며 새 SHA 적용의 증거는 아니다.
status의 current/release.json과 /health의 release가 원하는 main SHA인지 확인한다.

## Slack 사용과 매일 자동화

기본 시작점은 허용 채널의 `@Trace Marketing Agent` 멘션이다. 같은 스레드에서 멘션 없이
이어간다. `상태`, `검토 1`, `승인 해시`, `거절 해시`, `종료`, `다시 시작`도 같은 스레드에
보낸다. 사용자별 DM은 별도 문맥과 검색 전용 도구 권한으로 처리한다. 채널 일반 잡담,
봇 메시지와 첨부파일 내용은 처리하지 않는다. `/trace`는 기본 채널에서 계속 지원한다.

`TRACE_MARKETING_SLACK_BOT_USER_ID`가 있어야 Events가 활성화된다.
`TRACE_MARKETING_SLACK_ALLOWED_CHANNEL_IDS`는 쉼표 구분 허용 채널(기본: CHANNEL_ID),
`TRACE_MARKETING_SLACK_ALLOW_DM=1`은 허용 사용자의 DM을 켠다. Slack 이벤트 설정에는
app_mention, message.channels, message.groups, message.im과 대응 scope가 필요하다.

```text
/trace 일본 생산성 앱 마케팅 사례를 조사하고 출처와 다음 실험을 제안해줘
/trace status 실행ID
/trace input 실행ID 목표 고객은 일본 대학생이야
/trace review 실행ID 1
/trace approve 실행ID 승인해시
/trace reject 실행ID 승인해시
```

승인 전 review의 모든 페이지를 읽는다. 페이지 번호를 바꿔 전체 invocation JSON을 확인하고
각 페이지의 해시가 같은지 확인한다. 승인은 그 정확한 해시에만 적용된다.
결과 알림의 응답이 유실되면 무조건 재전송하지 않는다. status로 상태를 확인한다.
웹 로그인이 없는 Slack 전용 모드에서는 실행 결과 웹 링크를 표시하지 않는다.

수동 요청을 먼저 확인한 뒤 환경 예시의 daily 항목을 켠다. 주제는
[daily-research.example.json](daily-research.example.json)의 query로 지정한다.
`research.daily_slack_only`에는 Notion이 필요 없다. 설정한 시간 이후 당일 한 번 실행하고
재시작·중복 tick은 동일 Run을 사용한다. 이 설정은 해당 스킬의 Slack 전달만 승인한다.

## 실제 완료 확인

1. /health의 release, maintenance=false, update_protocol=1 확인.
2. 실제 멘션 → 스레드 후속 답변, DM 격리, /trace, 승인 내용 확인·승인·거절 검증.
3. 허용되지 않은 사용자·다른 채널 거절과 위조 서명 거절 확인.
4. 서비스 재시작 후 기존 기록·중복 방지 확인. daily 결과는 지정 채널에서 확인.
5. main의 다음 정상 커밋으로 current와 health SHA가 변경되는 것을 관찰.
6. Linux의 systemd/timer·재부팅 후 자동 실행 확인. 다른 서비스에 영향을 주지 않도록 조율.

회사 OAuth가 이미 있는 별도 설치는 기존 OAuth/PKCE 웹 모드를 계속 사용할 수 있다.
현재 사용자를 위해 이를 새로 준비할 필요는 없다. Cloudflare 이메일 로그인 연결은 별도 미구현 범위다.
