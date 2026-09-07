# Trace Marketing Agent: 온프레미스 + 기존 Cloudflare + Slack

Status: Candidate — 이 설치 경로가 포함된 변경을 main에 병합하고 CI가 성공한 뒤 사용한다.
로컬 후보 설치와 실제 배포된 URL을 이용한 설치, 사내 Slack 실사용 검증을 구분한다.

온프레미스 서버가 에이전트를 계속 실행하고, 기존 Cloudflare 터널·도메인이 Slack 요청을
전달한다. 기본 경로에 ZIP 업로드, 회사 인증 서비스, Mac/Appium 설치는 필요 없다.
Mac worker의 기존 연결 방식은 유지되며 on-prem 직접 등록/수명주기 관리는 아직 미구현이다.

## 1. 서버 설치 — 서버 Codex

이 변경을 main에 병합하고 해당 SHA의 `Verify on-prem agent` 성공을 먼저 확인한다.
PR 브랜치의 CI 성공은 main 설치 가능성을 뜻하지 않는다. 공개 설치 URL은 병합 전에는 없다.

서비스를 실행할 일반 Linux 사용자에서 Python 3.10+, git, gh, uv, 공식 Codex CLI,
systemd를 준비한다. Ubuntu 22.04 시스템 Python을 사용할 수 있고 애플리케이션 Python 3.14는
uv가 준비한다. 현재 설치기는 누락된 시스템 도구를 알려주고 중단한다. 도구 설치는 서버
Codex에게 맡긴다. 기존 cloudflared-ear.service와 설정을 유지한다.
GitHub CLI는 커밋 검사 조회용으로 로그인하고, Codex는 같은 사용자에서 로그인한다.
두 로그인은 별개다. 실제 사용할 모델 호출도 확인한다.

다음은 **변경 병합 후** 사용할 공개 설치 명령이다.

```bash
curl -fsSL https://raw.githubusercontent.com/corca-ai/ads-booster/main/install-server.sh | bash
export PATH="$HOME/.local/bin:$PATH"
trace-marketing server doctor
```

소스에서 설치하려면 저장소를 clone한 뒤 `bash install-server.sh`를 실행한다.
`bash install-server.sh --check`는 설치 없이 준비 상태만 확인한다.
설치기는 main을 가져오고 같은 SHA의 서버 CI 성공을 확인한 뒤 잠긴 의존성으로 격리 설치한다.
기존 CLI나 managed 설치가 있으면 덮어쓰지 않고 중단한다. 기존 설치의 상태를 확인해
`trace-marketing server update` 또는 기존 업데이트 unit으로 main을 받아가게 한다.

설치기는 `~/.local/bin/trace-marketing`을 current의 설치된 CLI에 연결한다.
수동 PATH 명령은 현재 셸용이며, 이후 셸에도 쓰려면 사용자 shell 설정에 같은 경로를 반영한다.
서버 서비스의 PATH는 setup이 실제 도구 경로로 생성한다.

## 2. Slack 앱 만들기 — 본인

서버에서 다음 명령을 실행해 나온 JSON 전체를 복사한다. 비밀값은 없다.

```bash
trace-marketing server manifest --origin https://marketing-agent.borca.ai --bootstrap
```

1. <https://api.slack.com/apps> → Create New App → From a manifest.
2. Corca 워크스페이스 → JSON 탭에 출력 내용 전체를 붙여 넣고 앱을 만든다.
3. OAuth & Permissions → Install to Workspace → Allow. 필요하면 Slack 관리자 승인을 받는다.
4. Bot User OAuth Token, Basic Information의 App ID와 Signing Secret을 확인한다.
5. 테스트 채널에 Trace Marketing Agent를 초대한다.
6. 채널 상세의 Channel ID, 본인 프로필의 Member ID를 준비한다.

기존 Trace 앱이 있으면 같은 앱에 설정을 적용한다. Ceal 앱을 수정하거나 토큰을 재사용하지
않는다. Socket Mode는 끈 상태이며 App-Level Token은 필요 없다.
처음에는 본인만 허용 사용자이자 승인 담당자로 설정할 수 있다.

## 3. 초기 설정 — 본인이 서버 터미널에서 실행

```bash
trace-marketing server setup
```

대화형 질문에 아래처럼 입력한다. 토큰 입력은 화면에 표시되지 않는다.

| 질문 | 입력값 |
| --- | --- |
| 공개 HTTPS 주소 | https://marketing-agent.borca.ai |
| 모델명 | 이 서버 Codex에서 실제 사용 가능한 모델 |
| 워크스페이스 이름 | corca-marketing |
| Slack App ID | 만든 Trace 앱의 App ID |
| 기본/허용 채널 ID | 테스트 채널 ID |
| 허용 사용자 ID | 본인 ID, 여러 명이면 쉼표 구분 |
| 승인 담당 사용자 ID | 허용 사용자 중 승인할 사람의 ID |
| Bot User OAuth Token | Slack의 xoxb- 토큰 |
| Signing Secret | 같은 Slack 앱의 Signing Secret |
| 전용 Cloudflare 커넥터 실행 여부 | 이 서버에서 마케팅 터널을 실행하려면 Yes |
| 마케팅 터널 토큰 | 기존 marketing-agent-onprem 터널의 커넥터 토큰 |

Slack auth.test로 토큰과 팀/봇 사용자 ID를 확인하고 설정 파일과 서비스 파일을 만든다.
비밀 파일은 600 권한이며 채팅·명령줄 인자·서비스 파일에 토큰을 넣지 않는다.
기존 agent.env/사용자 설정과 다른 도구가 만든 동명 서비스가 있으면 보존하고 중단한다.
setup은 신규 설치용이다. 기존 수동 설치의 설정을 자동 덮어쓰거나 병합하지 않는다.

cloudflared는 미리 설치되어 있어야 하며 `--token-file` 지원을 확인한다.
마케팅 커넥터는 사용자 서비스 `trace-marketing-tunnel.service`로 별도 생성한다.
이미 별도의 마케팅 커넥터가 관리되고 있으면 No를 선택하고 기존 연결을 유지한다.
`cloudflared-ear.service`는 어느 경우에도 수정하지 않는다.

Cloudflare에 저장한 hostname route는 아래여야 한다. setup은 Cloudflare 계정의 DNS/route를
변경하지 않는다. 현재 자원을 재사용한다.

```text
marketing-agent.borca.ai → http://localhost:8765
```

## 4. 서비스 시작 — 서버 Codex

```bash
trace-marketing server start
trace-marketing server status
```

start가 에이전트, 선택한 전용 터널, 5분 업데이트 timer를 활성화한다.
로그인 없이 재시작할 linger가 없으면 필요한 관리자 명령을 출력한다.
서버 Codex가 해당 사용자에 `sudo loginctl enable-linger 사용자명`을 실행하게 한다.
start 출력은 시작 요청의 성공이며 실제 연결 성공은 status로 확인한다.

status에서 local/public health, 서비스와 timer의 active 상태, linger=yes를 확인한다.
health의 owner는 on_prem_agent, maintenance=false, update_protocol=1이며 local/public의
release가 현재 설치 SHA와 일치해야 한다. Slack-only에서는 홈페이지 `/`의 404가 정상이다.
Slack Events/commands 경로에는 Cloudflare Access 로그인이나 브라우저 챌린지가 없어야 한다.

## 5. Slack 메시지 연결 — 본인

```bash
trace-marketing server manifest --origin https://marketing-agent.borca.ai
```

1. 같은 앱의 App Manifest에 이번 출력 전체를 적용한다.
2. Event Subscriptions의 URL이 아래이고 Verified인지 확인한다.
   `https://marketing-agent.borca.ai/channels/slack/events`
3. Bot events는 app_mention, message.channels, message.groups, message.im이다.
4. 권한 재설치 안내가 있으면 Reinstall to Workspace를 실행한다.
5. App Home에서 Messages 탭이 켜져 있고 메시지를 보낼 수 있는지 확인한다.
6. /trace URL은 별도로 `https://marketing-agent.borca.ai/channels/slack/commands`다.

Verified 실패 시 서버 Codex에게 공개 health와 Signing Secret, Cloudflare 리디렉션을
확인하게 한다. 토큰을 다시 발급했다면 서버의 agent.env를 갱신하고 서비스를 재시작한다.

## 6. 실제 사용과 자동 업데이트 검증 — 본인 + 서버 Codex

- 테스트 채널에서 `@Trace Marketing Agent 일본 대학생 대상 마케팅 사례를 조사해줘`.
- 같은 스레드에 멘션 없이 `그중 두 개만 자세히 알려줘`.
- 봇 DM에서 별도 질문, DM 내용이 채널에 나타나지 않는지 확인.
- 승인이 필요한 경우 `검토 1`부터 필요한 페이지를 읽고 `승인 표시된해시` 또는
  `거절 표시된해시`. 단순 조사에는 승인 요청이 없을 수 있다.
- 서비스 재시작 뒤 같은 스레드의 상태와 후속 질문, 중복 실행 방지 확인.
- Termius 종료 후 계속 실행되는지, 협의된 재부팅 후 자동 시작되는지 확인.

운영 명령:

```bash
trace-marketing server doctor
trace-marketing server status
trace-marketing server update
trace-marketing server stop
trace-marketing server start
```

update는 비동기 검사 요청이다. main의 Verify on-prem agent가 성공해야 설치하고, 바쁜 작업은
끝날 때까지 기다린다. 새 SHA가 없으면 설치하지 않는다. status의 release 변경으로 적용을
확인한다. Mac 릴리스 체크는 서버 업데이트 조건이 아니다. 설정·기록은 유지하고 시작 실패는
기존 관리자의 백업/복구 절차를 따른다. stop은 현재 실행을 멈추지만 부팅 자동 시작 설정을
해제하지 않는다. 실제 다음 main 변경 적용을 관찰하기 전에는 자동 적용은 미검증이다.

Ceal 형태의 멘션/스레드/DM 대화를 제공하지만 첨부파일, Slack 전체 검색, 버튼 승인,
스트리밍, AI 사이드패널은 아직 포함하지 않는다. DM 도구는 공개 검색 중심이다.
수동 대화가 확인된 후에만 선택 사항인 일일 연구 설정을 별도로 활성화한다.

공식 Slack 기준: [manifest](https://docs.slack.dev/app-manifests/),
[Events API](https://docs.slack.dev/apis/events-api/),
[auth.test](https://docs.slack.dev/reference/methods/auth.test/).
