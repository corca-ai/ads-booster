# Trace Slack 에이전트: PR 병합부터 실사용까지

Status: Candidate — 로컬 구현·격리 설치 검증과 실제 사내 Slack/Ubuntu 검증을 구분한다.

목표는 Corca의 Ceal처럼 **하나의 봇을 멘션하고 같은 스레드에서 대화하는 방식**이다.
Trace Marketing Agent라는 별도 Slack 앱을 사용한다. Ceal의 토큰이나 설치를 재사용하지 않는다.
이 후보는 멘션, 스레드 문맥, 사용자별 DM, 접수·결과 답변, 텍스트 승인·거절, 대화 종료,
재시작 후 기록 유지와 main 자동 업데이트를 제공한다. Ceal의 모든 도구를 복제한 것은 아니다.
첨부파일·Slack 전체 검색·AI 사이드패널·버튼 승인·스트리밍은 포함하지 않는다.
DM은 공개 검색과 답변만 가능하며 공유 워크스페이스를 수정하거나 외부 도구를 실행하지 않는다.

## 1. GitHub 변경을 main에 넣기 — 저장소 담당자

작업 브랜치는 `feature/agent-web-slack-onboarding`이다. 이 브랜치의 PR을 열고
Files changed에 아래 항목이 들어 있는지 확인한다. 로컬 설치 ZIP만으로 main이
갱신되는 것은 아니므로 PR 병합과 main 검사 성공을 별도로 확인한다.

- Slack Events, 대화 저장소와 HTTP/CLI 연결
- Slack 앱 manifest 두 개와 새 환경변수 예시
- agent-manager.py, service, update.service와 5분 timer
- `.github/workflows/verify-agent-server.yml`
- 테스트와 운영 문서

GitHub PR의 Checks에서 `Verify on-prem agent`와 다른 필수 검사가 성공한 뒤 리뷰를 완료하고
**Squash and merge**한다. 병합 후 **main 커밋의** 같은 검사도 성공해야 서버가 받아간다.
PR 브랜치의 성공 표시만으로는 부족하다. Actions에서 정확한 main 커밋을 확인한다.

저장소 규칙상 병합 후 새 버전 태그와 GitHub Release도 담당자가 완료한다. 기존 태그를
덮어쓰지 않는다. 서버 업데이트는 Release 게시가 아니라 main SHA와 CI 결과를 기준으로 한다.
이 설치 ZIP은 변경 후보이며 기존 공개 v0.4.21과 내용이 다르다.

완료 기준: 원격 main에 위 변경이 있고 해당 SHA의 `Verify on-prem agent`가 성공함.

## 2. 최신 설치 ZIP을 서버로 옮기기 — 본인

Mac 다운로드 폴더의 `trace-agent-server-setup-20260907-ci-separated.zip`을 사용한다.
이전 `slack-agent.zip`과 `autoupdate.zip` 대신 이 파일을 사용한다. 이번 파일에 Mac 릴리스와
분리된 서버 업데이트 관리자가 포함되어 있다.
Termius에서 설치할 Ubuntu 세션의 SFTP를 열고 ZIP을 서버의 로그인 사용자 홈으로 업로드한다.
업로드 후 서버 Codex에 **실제 절대 경로**를 알려준다. 예: `/home/실제계정/trace-agent-server-setup-20260907-ci-separated.zip`.
계정 이름은 예시를 그대로 쓰지 않는다.

서버 Codex에게 이 문서 마지막의 지시문을 전달하면 된다. 압축 해제, 의존성 설치와 파일 배치는
서버 Codex에게 맡긴다. 기존 설치가 있다면 상태 백업과 관리 방식부터 확인하게 한다.

완료 기준: 서버 Codex가 ZIP 안의 README, 이 문서, verification.md와 wheel을 실제로 찾음.

## 3. Slack 앱 최초 생성과 설치 — 본인 또는 Slack 관리자

1. <https://api.slack.com/apps>에서 `Create New App` → `From a manifest`를 선택한다.
2. 설치 대상은 **Corca 회사 워크스페이스**로 선택한다.
3. JSON 탭에 `slack-app-bootstrap-manifest.json` 전체를 붙여 넣는다.
   처음에는 서버가 없어도 생성할 수 있도록 Event Subscriptions를 제외한 파일이다.
4. 앱 이름은 `Trace Marketing Agent`로 생성한다. 사내 이름을 바꾸려면 표시 이름만 바꿔도 된다.
5. `OAuth & Permissions` → `Install to Workspace` → 권한 허용을 진행한다.
   회사에서 앱 설치 승인을 요구하면 Slack 관리자에게 승인을 요청한다.
6. 같은 화면의 `Bot User OAuth Token` (`xoxb-...`)을 준비한다.
7. `Basic Information`의 `App Credentials`에서 `App ID`와 `Signing Secret`을 준비한다.
8. 봇의 `Bot User ID`와 `Team ID`는 서버에서 이 Bot Token으로 Slack `auth.test`를 호출해
   응답의 `user_id`, `team_id`만 확인하면 된다. **Bot ID(`B...`)나 App ID(`A...`)를
   Bot User ID(`U...`) 대신 넣지 않는다.** 서버 Codex가 비밀 토큰을 출력하지 않고 조회하게 한다.

이미 Trace 앱을 만들었다면 새 앱을 중복 생성하지 말고 그 앱에 같은 설정을 적용한다.
Ceal 앱을 수정하지 않는다. Socket Mode와 별도의 App-Level Token(`xapp-...`)은 필요 없다.
이 연결은 Cloudflare를 통해 들어오는 HTTP Events API를 사용한다.

## 4. 사용할 채널과 사람 정하기 — 본인

1. 우선 테스트 채널 하나를 정한다. 예: `#trace-agent-test`.
2. 채널에서 앱을 추가하거나 `/invite @Trace Marketing Agent`를 실행한다.
   비공개 채널은 반드시 해당 채널 안에서 앱을 초대한다.
3. 채널 이름을 눌러 상세 정보에서 Channel ID를 복사한다.
4. 본인과 초기 사용자의 프로필 → 더 보기 → Member ID 복사로 Slack 사용자 ID를 준비한다.
5. 누가 실행을 승인할지 정한다. 허용 사용자만 요청할 수 있고 승인 담당자는 별도로 지정한다.

서버의 `slack-installation.json`에는 `app_id`, `team_id`, `tenant_id=corca-marketing`,
사용자 목록을 넣는다. `member_id`에는 같은 Slack 사용자 ID를 써도 된다.
승인 담당자만 `can_approve: true`, 나머지는 `false`로 둔다. 회사 인증 계정은 필요 없다.

여러 채널을 쓰면 `TRACE_MARKETING_SLACK_ALLOWED_CHANNEL_IDS=C첫번째,C두번째`처럼 지정하고
각 채널에 앱을 초대한다. `TRACE_MARKETING_SLACK_CHANNEL_ID`는 `/trace`와 일일 보고의 기본 채널이다.
일반 채널 잡담에는 답하지 않고, 멘션으로 시작한 스레드의 허용 사용자 답글에만 반응한다.
DM도 같은 허용 사용자 목록을 적용한다.

## 5. 서버 설정과 별도 Cloudflare 터널 연결 — 서버 Codex, 비밀값은 본인 입력

서버 Codex가 `~/.config/trace-marketing/agent.env`와 `slack-installation.json`을 준비하게 한다.
본인이 서버 편집기에서 토큰·Signing Secret을 직접 입력한다. 채팅에 붙이지 않는다.
`agent.env.example`의 모든 `REPLACE...`를 실제 값으로 채운다.

| 설정 | 입력할 값 |
| --- | --- |
| `TRACE_MARKETING_SLACK_ONLY` | `1` |
| `TRACE_MARKETING_PUBLIC_ORIGIN` | `https://marketing-agent.borca.ai` |
| `TRACE_MARKETING_MODEL` | 같은 서버 Codex 계정에서 실제 호출에 성공한 모델 |
| `TRACE_MARKETING_TENANT` | `corca-marketing` |
| `TRACE_MARKETING_SLACK_BOT_TOKEN` | 앱의 Bot User OAuth Token |
| `TRACE_MARKETING_SLACK_SIGNING_SECRET` | 앱의 Signing Secret |
| `TRACE_MARKETING_SLACK_BOT_USER_ID` | `auth.test`의 `user_id` |
| `TRACE_MARKETING_SLACK_CHANNEL_ID` | 기본 채널 ID |
| `TRACE_MARKETING_SLACK_ALLOWED_CHANNEL_IDS` | 허용 채널 ID, 여러 개면 쉼표로 연결 |
| `TRACE_MARKETING_SLACK_ALLOW_DM` | `1` |
| `TRACE_MARKETING_SLACK_INSTALLATION` | 서버 권한 JSON의 절대 경로 |

Codex는 **서비스를 실행할 같은 Linux 사용자**에서 로그인되어 있어야 한다.
`codex login status`뿐 아니라 실제 모델 요청도 확인한다. uv/git/gh/Codex의 실제 경로를
systemd 서비스의 PATH에 반영한다. Appium이나 Xcode를 설치할 단계는 아니다.

Cloudflare에서 `marketing-agent-onprem` 터널을 열어 Linux 커넥터 토큰을 준비한다.
이미 저장한 공개 주소는 `marketing-agent.borca.ai` → `http://localhost:8765`다.
서버 Codex가 **별도 마케팅 cloudflared 서비스와 비밀 토큰 파일**을 준비하게 한다.
기존 `cloudflared-ear.service`는 실행·자동 시작 모두 그대로 보존한다.
기존 서비스와 충돌할 수 있는 기본 `cloudflared service install`을 무작정 실행하지 않는다.

서버 Codex가 패키지 README에 따라 wheel을 격리 설치하고 `trace-marketing.service`를 시작한다.
사용자 서비스의 linger를 설정해 SSH/Termius를 닫아도 실행되게 한다.

완료 기준:

```bash
curl -fsS http://127.0.0.1:8765/health
curl -fsS https://marketing-agent.borca.ai/health
```

둘 다 정상 JSON이고 `maintenance=false`, `update_protocol=1`이어야 한다.
Cloudflare에서 새 커넥터도 Healthy여야 한다. HTTP Events와 commands 경로에는 Cloudflare
Access 로그인 화면·이메일 OTP·브라우저 챌린지가 끼면 안 된다. Slack은 그 화면을 통과하지 못한다.
서버는 대신 Slack 서명과 설치·사용자·채널 권한을 검증한다. 웹 UI는 404가 정상이다.

## 6. 같은 Slack 앱에 이벤트 연결 — 본인

1. Slack 개발자 페이지에서 **방금 만든 Trace 앱**을 연다.
2. `App Manifest`에서 이번에는 `slack-app-manifest.json` 전체를 적용한다.
3. `Event Subscriptions`가 켜져 있고 Request URL이 아래인지 확인한다.

   `https://marketing-agent.borca.ai/channels/slack/events`

4. URL 옆의 **Verified**를 확인한다. 실패하면 먼저 5단계 health, 서버의 BOT_USER_ID,
   Signing Secret, Cloudflare 리디렉션을 서버 Codex가 확인하게 한다.
5. `Subscribe to bot events`에 `app_mention`, `message.channels`, `message.groups`,
   `message.im`이 있어야 한다.
6. `OAuth & Permissions`의 Bot Token Scopes는 `commands`, `chat:write`, `app_mentions:read`,
   `channels:history`, `groups:history`, `im:history`다. 권한이 변경되어 재설치 안내가 나오면
   `Reinstall to Workspace`로 다시 허용한다. 토큰이 바뀌면 서버 값도 바꿔 재시작한다.
7. `App Home`에서 Messages 탭이 켜지고 사용자가 메시지를 보낼 수 있어야 한다.
8. `Slash Commands`의 `/trace` URL은 별도 경로다.

   `https://marketing-agent.borca.ai/channels/slack/commands`

일반 스레드 답글을 받기 위해 message.channels/groups 권한이 필요하다. 앱이 초대받은
채널의 이벤트가 도착해도 허용 채널·사용자·이미 시작한 대화에 해당하는 텍스트만 저장·처리한다.
전체 Slack 과거 메시지를 긁어오지 않는다. 앱 아이콘의 온라인 표시가 구동 검증은 아니다.

공식 기준: [manifest](https://docs.slack.dev/reference/app-manifest/),
[Events API](https://docs.slack.dev/apis/events-api/), [auth.test](https://docs.slack.dev/reference/methods/auth.test/).

## 7. main 자동 업데이트 켜기 — 서버 Codex, GitHub 인증은 본인

서버 계정에 ads-booster 저장소 읽기 권한을 연결한다. Codex 로그인과 GitHub 로그인은 별개다.
서버 Codex가 Git의 main fetch와 gh의 해당 커밋 checks 조회를 둘 다 확인하게 한다.
SSH 키 또는 HTTPS credential helper를 사용하고 토큰을 저장소 URL에 넣지 않는다.
GitHub 조직에서 SSO 승인이 필요하면 본인이 로그인 화면에서 승인한다.

```bash
systemctl --user enable --now trace-marketing-update.timer
systemctl --user list-timers trace-marketing-update.timer
systemctl --user start trace-marketing-update.service
python3 ~/.local/share/trace-marketing-server/current/agent-manager.py status
curl -fsS http://127.0.0.1:8765/health
```

부팅 후 2분, 이후 검사 종료 기준 약 5분마다 main을 확인한다. 새 SHA가 없으면 설치하지 않는다.
새 SHA의 `Verify on-prem agent`가 완료·성공해야 별도 환경에 설치하고, 실행 중인 작업이 끝난 뒤
백업·교체·건강 확인한다. 시작 실패 시 이전 코드와 상태로 복구한다. 설정·로그인·기록은 유지한다.
바쁜 상태가 계속되면 교체를 미루므로 **main 병합 후 반드시 5분 안에 적용된다는 뜻은 아니다**.
이 전용 검사에 서버와 도구 어댑터의 연결 계약 검증이 포함된다. 별도 Mac 릴리스 검사의
실패·대기는 서버 자동 업데이트 조건이 아니다. Mac 호환성 CI는 계속 실행되지만 버전이
바뀌지 않으면 Mac 릴리스 게시를 시도하지 않는다. Slack 기동에 Mac 설치가 선행 조건은 아니다.
Mac을 온프레미스 에이전트에 직접 등록하고 수명주기까지 관리하는 기능은 아직 미구현이다.

완료 기준: 첫 수동 검사 뒤 `/health`의 `release`가 main SHA와 같고 timer가 활성화됨.
이후 실제 다음 main 변경 때 SHA가 바뀌는 것을 관찰해야 자동 적용까지 검증한 것이다.
검사 종료 로그만으로 업데이트 적용을 주장하지 않는다. 백업·이전 설치본의 용량은 운영자가 관리한다.

## 8. Slack에서 직접 써보고 완료 판정 — 본인 + 서버 Codex

테스트 채널에서 다음 순서대로 한다.

1. `@Trace Marketing Agent 일본 대학생 대상 생산성 앱 마케팅 사례를 조사해줘`.
2. 같은 스레드에 접수 표시와 출처를 포함한 답변이 오는지 확인한다.
3. 멘션 없이 `방금 사례 중 예산이 적게 드는 것 두 개만 골라줘`라고 **같은 스레드**에 답한다.
   에이전트가 앞 대화를 이어야 한다. 질문을 되묻는 경우 답하면 같은 실행에 입력이 이어진다.
4. 앱 DM에서 다른 질문을 하고 후속 질문을 보낸다. DM 답변이 채널로 나오면 안 된다.
5. 실행 승인이 필요한 요청은 `검토 1`, 필요한 다음 페이지를 모두 읽고 `승인 표시된해시`
   또는 `거절 표시된해시`를 보낸다. 해시는 실제 표시된 값을 복사한다. `좋아`만으로 승인되지 않는다.
6. `종료`를 보내 자동 응답을 닫고, `다시 시작`으로 연다. 종료는 실행 중인 외부 작업의 강제 취소가 아니다.
7. 서버 Codex가 서비스를 재시작한 뒤 같은 스레드의 `상태`와 후속 대화, 중복 실행 방지를 확인한다.
8. 허용하지 않은 사용자·채널에 반응하지 않는지 확인하고 기존 ear 서비스가 계속 정상인지 확인한다.
9. 다른 서버 서비스의 영향을 확인하고 재부팅 점검 일정을 잡는다. 재부팅 후 에이전트·터널·timer가
   로그인 없이 살아나는지 확인한다.

답변 전송 결과가 유실되면 임의 재전송하지 않으므로 `상태`로 확인한다.
파일 첨부 대신 필요한 내용을 텍스트로 보낸다. 승인 없는 외부 자동 게시는 지원하지 않는다.

매일 자동 보고까지 쓸 경우 수동 대화가 확인된 뒤 `agent.env.example`의 daily 설정을 켠다.
`daily-research.example.json`에 주제를 넣고 시간·시간대·기본 채널을 확정한다.
이 설정은 해당 일일 스킬의 Slack 전달만 사전 승인한다. Notion 가입·연결은 필요 없다.

## 서버 Codex에 그대로 전달할 지시문

```text
서버에 올린 trace-agent-server-setup-20260907-ci-separated.zip을 찾아 README.md,
slack-launch-guide.md와 verification.md를 읽고 Trace Slack 에이전트를 설치해줘.
ZIP의 실제 절대 경로는 내가 전달한 업로드 위치를 사용해줘.
Ubuntu 22.04.5 x86_64이며 Codex는 ChatGPT 로그인 상태야.
기존 cloudflared-ear.service는 변경하지 말고 마케팅 터널은 별도 서비스로 연결해줘.
회사 OAuth 없이 Slack-only=1, 멘션·스레드·DM을 사용하는 설치야.
새 BOT_USER_ID와 허용 채널/사용자 설정까지 확인해줘. auth.test로 team_id와 user_id만
확인하고 토큰은 출력하지 마. 비밀값은 내가 직접 넣을 서버 파일을 준비해줘.
agent-manager.py로 격리 설치하고 서비스 PATH, Codex 실제 모델 호출, Git fetch와
gh checks 읽기 권한, systemd 서비스/linger, 5분 업데이트 timer를 설정해줘.
local/public health가 정상화되면 내가 Slack Event URL을 Verified로 만드는 순서를 안내해줘.
실제 Slack 메시지는 내가 테스트 채널에서 보낼게. 결과와 스레드/DM 격리,
승인 해시, 재시작 후 기록 보존, current와 main SHA, 기존 ear 상태를 검증해줘.
main에 변경이 없으면 실제 다음 자동 업데이트는 미검증이라고 구분해줘.
재부팅은 다른 서비스 영향을 먼저 확인하고 나와 시점을 정해줘.
설치본에 별도 코드를 고치지 말고 발견한 결함은 ads-booster 작업 브랜치로 돌려줘.
완료한 항목, 실제 증거, 내가 추가로 입력하거나 클릭할 항목을 구분해서 알려줘.
```
