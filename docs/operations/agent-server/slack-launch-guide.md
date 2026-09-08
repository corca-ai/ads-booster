# Trace Marketing Agent: 자기 서버에 설치하고 Slack에서 사용하기

Status: Candidate — 이 설치 경로가 포함된 변경을 main에 병합하고 CI가 성공한 뒤 사용한다.
로컬 후보 설치와 실제 배포된 URL을 이용한 설치, 사내 Slack 실사용 검증을 구분한다.

온프레미스 서버가 에이전트를 계속 실행하고, 기존 Cloudflare 터널·도메인이 Slack 요청을
전달한다. 기본 경로에 ZIP 업로드, 회사 인증 서비스, Mac/Appium 설치는 필요 없다.
Mac worker의 기존 연결 방식은 유지되며 on-prem 직접 등록/수명주기 관리는 아직 미구현이다.

## 1. 서버 설치 — 서버 Codex

이 변경을 main에 병합하고 해당 SHA의 `Verify on-prem agent` 성공을 먼저 확인한다.
PR 브랜치의 CI 성공은 main 설치 가능성을 뜻하지 않는다. 공개 설치 URL은 병합 전에는 없다.

지원 환경은 **Ubuntu 22.04 또는 24.04, x86_64 또는 aarch64, systemd**다.
일반 사용자는 sudo 권한이 필요하다. 설치기가 필요한 시스템 패키지와 uv·Codex·cloudflared를
준비하고 Python 3.14와 앱 의존성을 격리 설치한다. 기존 도구는 교체하지 않는다.
GitHub 로그인, Node.js 설치, ZIP 업로드는 필요 없다.

다음은 **변경 병합 후 main CI 성공을 확인하고** 실행한다.

```bash
curl -fsSL https://raw.githubusercontent.com/corca-ai/ads-booster/main/install-server.sh -o /tmp/trace-install.sh
bash /tmp/trace-install.sh
export PATH="$HOME/.local/bin:$PATH"
codex login --device-auth
```

curl도 없는 최소 서버라면 먼저 관리자가 `sudo apt-get update`와
`sudo apt-get install -y curl ca-certificates`를 실행한다.
root로 접속했다면 설치 명령에 `--user trace-marketing`을 추가한다. 일반 서비스 계정을
만든 뒤 그 계정에 설치하므로, 이후 `sudo -iu trace-marketing`으로 전환하여 Codex 로그인과
아래 설정을 진행한다. 이미 Codex에 로그인한 일반 계정이 있다면 그 계정에서 설치하면 된다.
로그인 화면에서 안내하는 인증 주소를 본인의 브라우저로 열어 완료한다. 비밀값은 채팅에 보내지 않는다.

`bash /tmp/trace-install.sh --check`는 로컬 준비 상태만 확인하고 변경하지 않는다.
설치 중 다운로드가 실패하거나 CI가 아직 진행 중이면 같은 설치 명령을 다시 실행한다.
완료된 설치는 유지하고 CLI 연결만 확인한다. 기존 다른 CLI와 충돌하면 보존하고 중단한다.
설치된 제품 업데이트는 `trace-marketing server update`로 요청한다.

개발자만: 로컬 checkout의 커밋된 HEAD를 설치하려면
`bash install-server.sh --source /절대경로/ads-booster`를 쓴다. 미커밋 파일은 설치하지 않는다.
이 설치는 `source-…`로 표시되며 이후 업데이트는 공개 main을 따른다.

설치기는 `~/.local/bin/trace-marketing`을 current의 설치된 CLI에 연결한다.
수동 PATH 명령은 현재 셸용이며, 이후 셸에도 쓰려면 사용자 shell 설정에 같은 경로를 반영한다.
서버 서비스의 PATH는 setup이 실제 도구 경로로 생성한다.

## 2. Slack 앱 만들기 — 본인

서버에서 다음 명령을 실행해 나온 JSON 전체를 복사한다. 비밀값은 없다.

```bash
trace-marketing server manifest --origin https://agent.example.com --bootstrap
```

1. <https://api.slack.com/apps> → Create New App → From a manifest.
2. 사용할 Slack 워크스페이스 → JSON 탭에 출력 내용 전체를 붙여 넣고 앱을 만든다.
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
| 공개 HTTPS 주소 | https://agent.example.com |
| 모델명 | 이 서버 Codex에서 실제 사용 가능한 모델 |
| 워크스페이스 이름 | marketing (팀을 구분할 이름) |
| Slack App ID | 만든 Trace 앱의 App ID |
| 기본/허용 채널 ID | 테스트 채널 ID |
| 허용 사용자 ID | 본인 ID, 여러 명이면 쉼표 구분 |
| 승인 담당 사용자 ID | 허용 사용자 중 승인할 사람의 ID |
| Bot User OAuth Token | Slack의 xoxb- 토큰 |
| Signing Secret | 같은 Slack 앱의 Signing Secret |
| 전용 Cloudflare 커넥터 실행 여부 | 이 서버에서 마케팅 터널을 실행하려면 Yes |
| 마케팅 터널 토큰 | 자신의 마케팅 터널 커넥터 토큰 |

Slack auth.test로 토큰과 팀/봇 사용자 ID를 확인하고 설정 파일과 서비스 파일을 만든다.
비밀 파일은 600 권한이며 채팅·명령줄 인자·서비스 파일에 토큰을 넣지 않는다.
기존 agent.env/사용자 설정과 다른 도구가 만든 동명 서비스가 있으면 보존하고 중단한다.
설정 저장 도중 종료되면 같은 `server setup`으로 남은 저장을 이어간다. 완료 후 재실행해도
기존 값을 유지한다. 사람이 중간에 변경한 파일은 덮어쓰지 않고 알려준다. 토큰 검증 전에
중단되었다면 다시 입력한다. 기존 수동 설치의 설정을 자동 병합하지 않는다.

설치기가 누락된 cloudflared를 준비하며 setup에서 `--token-file` 지원을 확인한다.
마케팅 커넥터는 사용자 서비스 `trace-marketing-tunnel.service`로 별도 생성한다.
이미 별도의 마케팅 커넥터가 관리되고 있으면 No를 선택하고 기존 연결을 유지한다.
`cloudflared-ear.service`는 어느 경우에도 수정하지 않는다.

Cloudflare에 저장한 hostname route는 아래여야 한다. setup은 Cloudflare 계정의 DNS/route를
변경하지 않는다. 현재 자원을 재사용한다.

```text
agent.example.com → http://localhost:8090
```

## 4. 서비스 시작 — 서버 Codex

```bash
trace-marketing server doctor
trace-marketing server start
trace-marketing server status
```

start가 에이전트, 선택한 전용 터널, 5분 업데이트 timer를 활성화한다.
설치기가 로그아웃·재부팅 후에도 실행할 linger를 활성화한다. 누락되면 start가 관리자 명령을 출력한다.
서버 Codex가 해당 사용자에 `sudo loginctl enable-linger 사용자명`을 실행하게 한다.
start 출력은 시작 요청의 성공이며 실제 연결 성공은 status로 확인한다.

status에서 local/public health, 서비스와 timer의 active 상태, linger=yes를 확인한다.
health의 owner는 on_prem_agent, maintenance=false, update_protocol=1이며 local/public의
release가 현재 설치 SHA와 일치해야 한다. Slack-only에서는 홈페이지 `/`의 404가 정상이다.
Slack Events/commands 경로에는 Cloudflare Access 로그인이나 브라우저 챌린지가 없어야 한다.

## 5. Slack 메시지 연결 — 본인

```bash
trace-marketing server manifest --origin https://agent.example.com
```

1. 같은 앱의 App Manifest에 이번 출력 전체를 적용한다.
2. Event Subscriptions의 URL이 아래이고 Verified인지 확인한다.
   `https://agent.example.com/channels/slack/events`
3. Bot events는 app_mention, message.channels, message.groups, message.im이다.
4. 권한 재설치 안내가 있으면 Reinstall to Workspace를 실행한다.
5. App Home에서 Messages 탭이 켜져 있고 메시지를 보낼 수 있는지 확인한다.
6. /trace URL은 별도로 `https://agent.example.com/channels/slack/commands`다.

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

## 자동 업데이트 결과 읽기와 문제 해결

`server status`의 `current/release.json.release`가 설치된 버전이며 local/public의 release도
같아야 한다. `last-check.json.result`는 `up_to_date`(최신), `waiting_for_ci`(CI 대기),
`staging_failed`(다운로드·설치 실패, 다음 검사에서 재시도), `quarantined`(기동 실패로 해당
SHA 보류), `candidate_ready`(후보 준비)를 구분한다. 실제 전환 성공은 `last-success.json`과
local health의 새 SHA로 확인한다. GitHub API 제한이나 네트워크 오류일 때는 현재 버전을 유지한다.

- `server doctor`가 실패하면 출력에서 false인 항목을 확인한다. Codex는 같은 계정에서
  `codex login --device-auth`, 설정은 `trace-marketing server setup`으로 완료한다.
- local은 정상인데 public이 안 되면 도메인 route·터널 상태를 확인한다.
- updater 실패 원인은 `journalctl --user -u trace-marketing-update.service -n 30 --no-pager`로 확인한다.
  앱 설정 파일과 토큰을 로그나 채팅에 붙여 넣지 않는다.
- 기존 수동 설정 때문에 setup이 멈추면 파일을 임의 삭제하지 말고 기존 운영 상태를 먼저 확인한다.

Corca의 기존 환경에서는 이 문서의 `agent.example.com`을 `marketing-agent.borca.ai`로,
터널을 `marketing-agent-onprem`으로 선택하면 된다. 기존 `cloudflared-ear.service`는 그대로 둔다.


## 기존 8765 설치에서 8090으로 전환

이 변경이 main에 병합되고 서버 CI가 성공한 뒤 서버 담당 Codex가 한 번 수행한다.
구버전 업데이터는 8765로 고정되어 있어 일반 `server update`만으로 이번 전환을 맡기지 않는다.

1. 8090 포트가 비어 있는지 확인한다. 8765를 점유한 다른 서비스와 EAR 터널은 유지한다.
2. 마케팅 업데이트 timer를 멈추고, 실행 중인 updater/transaction/작업이 없는지 확인한다.
   진행 중이라면 정상 종료·복구를 먼저 완료한다. 진행 중인 작업을 강제 종료하지 않는다.
3. 마케팅 agent 서비스만 정지한다. 상태 DB와 설정을 백업하고, 기존 `current` 심볼릭 링크를
   충돌하지 않는 백업 이름으로 보존한다. 릴리스 디렉터리·설정·대화 기록은 삭제하지 않는다.
4. `~/.config/trace-marketing/server.json`의 나머지 값은 보존하고 `"port": 8090`을 저장한다.
   구버전 설치기에 새 포트 옵션이 있는 것으로 가정하거나 전체 설정을 다시 만들지 않는다.
5. 최신 main의 공식 설치기를 다시 받아 같은 일반 계정에서 실행한다. `current`가 없으므로
   CI를 통과한 새 버전을 격리 설치하고 연결한다. 실패하면 백업해 둔 링크를 복구하고
   서비스를 정지한 상태에서 원인을 확인한다. 잘못된 8765 서비스로 연결하지 않는다.
6. 마케팅 Cloudflare route를 `http://localhost:8090`으로 바꾸고 `server doctor`,
   `server start`, `server status`를 실행한다. 내부·공개 health에 `owner: on_prem_agent`와
   같은 release가 있어야 한다. `{"status":"ok"}`만으로 연결 성공이라고 판단하지 않는다.
7. Slack Event Subscriptions의 Retry로 Verified를 확인한 뒤 실제 멘션/스레드/DM을 확인한다.
   Slack 공개 URL에는 `:8090`을 붙이지 않는다. 자동 업데이트 timer의 active도 확인한다.

새 버전은 실행·업데이트·상태 확인 모두 같은 `server.json.port`를 사용하므로 이후 main
업데이트에도 포트 설정이 유지된다. 이 파일이 없거나 port가 없으면 기본값은 8090이다.
