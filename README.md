# Trace Marketing Pipeline

`ads-booster` provides the public Trace marketing workspace on Cloudflare and a replaceable Mac
worker that creates verified Trace wallpaper images with Codex CLI and Appium. Candidate generation,
review state, account isolation, schedules, task leases, and artifacts remain hosted. Threads posting
is intentionally not implemented.

> Rollout note (2026-08-27): the feedback provenance, generated-batch quality gate, immediate image
> retry guidance, and zero-worker fail-fast described below are implemented on the current candidate
> branch but are not deployed product behavior until the D1 migration and Worker release are applied
> and read back from `workspace.borca.ai`.

## Current product surfaces

- Workspace: <https://workspace.borca.ai/>
- Mac worker CLI: `trace-marketing worker ...`
- Native capture: Appium + XCUITest + the `com.corca.Trace` debug build
- Planning model on a Mac: the official `codex` CLI using that macOS user's existing login
- Hosted candidate model: Cloudflare Workers AI, configured by `WORKSPACE_AI_MODEL`

The former `trace-agent` / `trace-ads` custom model shell is no longer installed. The Mac pipeline
does not use its OAuth store, Responses client, conversation memory, or tool loop.

## Pipeline

1. A teammate opens `workspace.borca.ai`, selects an account/country/profile, and generates or edits
   candidates. Generated batches are structurally validated and record prompt/model/feedback-rule
   provenance. Repeated rating-1–2 rejections from three distinct revisions in the same review stage
   activate only server-owned caption, concept, design, persona, or policy instructions; reviewer
   notes are never injected automatically. A rejected image's stage-valid tag instructions also
   guide that same candidate's immediate retry.
2. Candidate selection creates a hosted capture task whose approved caption, hypothesis, references,
   creative direction, background intent, profile, and Trace items are immutable inputs. D1 assigns
   one lease to a healthy enrolled Mac. If no non-revoked Mac is registered, the request returns
   `503` before creating a task instead of falling back to a shared Queue credential.
3. The Mac starts a new ephemeral `codex exec` turn. The marketing context is sent over stdin and the
   final output must match the strict `WallpaperPlan` JSON schema.
4. Code validates request ID, time zone, local event times, references, layout, and style. The
   Mac then records an execution barrier in D1; Appium cannot start unless that barrier succeeds.
   Invalid plans never reach Appium.
5. The deterministic runner finds an approved background and drives the real Trace Simulator app
   through Appium. A request-bound export, digest, nonce, device binding, and PNG provenance are
   verified.
6. The callback stores the verified image in R2 and exposes it for human review. Approval reaches
   `submitted`; no external social post is created.

Codex threads are ephemeral per task, so two accounts and two Macs do not share conversation
history. Validated plans and terminal outcomes are request-scoped under
`$TRACE_AGENT_HOME/codex-runs`; prompts, Codex responses, and auth data are not persisted there.

## Bootstrap a verified Mac worker release

```bash
bash -euo pipefail <<'TRACE_MAC_BOOTSTRAP'
repository="corca-ai/ads-booster"
release="$(gh release view --repo "$repository" --json tagName,isDraft,isPrerelease \
  --jq 'select(.isDraft == false and .isPrerelease == false) | .tagName')"
release_dir="$(mktemp -d "${TMPDIR:-/tmp}/trace-marketing-bootstrap.XXXXXX")"
trap 'rm -rf -- "$release_dir"' EXIT
gh release download "$release" --repo "$repository" --dir "$release_dir" \
  --pattern trace-marketing-release.json --pattern trace-marketing-bootstrap.py
manifest="$release_dir/trace-marketing-release.json"
bootstrap="$release_dir/trace-marketing-bootstrap.py"
bundle_name="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["bundle"]["name"])' "$manifest")"
commit_sha="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["commit_sha"])' "$manifest")"
[[ "$bundle_name" =~ ^trace-marketing-macos-arm64-v[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz$ ]]
[[ "$commit_sha" =~ ^[0-9a-f]{40}$ ]]
gh release download "$release" --repo "$repository" --dir "$release_dir" --pattern "$bundle_name"
for asset in "$manifest" "$bootstrap" "$release_dir/$bundle_name"; do
  gh attestation verify "$asset" --repo "$repository" \
    --signer-workflow "$repository/.github/workflows/release-mac-worker.yml" \
    --source-ref refs/heads/main --source-digest "$commit_sha" --deny-self-hosted-runners
done
python3 "$bootstrap" --manifest "$manifest" --bundle "$release_dir/$bundle_name" \
  --uv "$(command -v uv)" --gh "$(command -v gh)"
export PATH="$HOME/.local/share/trace-marketing/current/bin:$PATH"
trace-marketing version --json
TRACE_MAC_BOOTSTRAP
```

The one-time bootstrap requires `gh`, `uv`, and a locally available Python 3.14, but never installs
or upgrades them. Run `gh auth status` first as the same macOS user that will own the services. The
copied block verifies the downloaded bootstrap before executing it, then verifies a stable
versioned GitHub Release, its tag and exact commit, all
GitHub SHA-256 asset digests, local digests, and workflow-bound build attestations. It then performs an offline
wheelhouse install under `~/.local/share/trace-marketing/releases/<version>` and atomically creates
`current`. Mutable `main`, a Git checkout, PyPI resolution at update time, and in-place
`uv tool --force` replacement are not production install paths.

To inspect the plan without changing the Mac:

```bash
bash install.sh --dry-run --tag vX.Y.Z
```

Codex CLI, Xcode, Appium, XCUITest, and the Trace debug build remain manually owned prerequisites.

## Prepare a Mac

Run these as the same macOS user that will own the LaunchAgent:

```bash
codex login
codex login status
gh auth status
appium driver install xcuitest   # only if the driver is missing
trace-marketing worker doctor
```

Also install:

- Xcode and one available iPhone Simulator;
- Appium 3 with the XCUITest driver;
- the internal Trace debug app with bundle ID `com.corca.Trace` on that Simulator.

`worker doctor` must report `codex_cli`, `codex_authenticated`, Appium, XCUITest, Simulator, and
Trace as ready. An unauthenticated or incomplete Mac advertises itself as degraded and receives no
new task.

## Enroll a Mac

The usual operator path is the protected Mac manager inside the workspace. It creates one copyable
command block that resolves the latest stable release, performs the verified install, consumes a
short-lived single-use enrollment code, and starts both the worker and updater. The block contains
no administrator token, worker ID, committed device ID, or Codex credential. It runs in a fail-fast
subshell, so a release lookup, install, or doctor failure cannot consume the enrollment code.
Open `Mac 연결 관리` and expand `에이전트에게 Mac 등록 맡기기` to copy a Computer Use prompt that
performs the prerequisite checks, guides the operator-only token entry, runs the generated block,
and requires local service plus hosted heartbeat/version readback before reporting success. The
prompt never contains the control-plane token or a generated enrollment code.

The equivalent administrator CLI flow is:

```bash
export TRACE_MARKETING_CONTROL_TOKEN='...'
trace-marketing worker create-enrollment \
  --url https://workspace.borca.ai \
  --name 'Studio Mac'
```

On an already enrolled shared Mac, the release bootstrap preserves the existing mode-`0600`
credential, durable inbox/outbox, `codex-runs`, generated artifacts, and official Codex login. It
installs and verifies the worker and updater services automatically after the operator drains and
stops the old worker.

For a fresh Mac, the copied manager block bootstraps first. Installation deliberately stops before
service start because no machine credential exists; the next commands consume the code and finish
the one-time service transaction:

```bash
trace-marketing worker enroll \
  --url https://workspace.borca.ai \
  --code '...'
trace-marketing worker finish-bootstrap \
  --home "$HOME/.trace-agent" \
  --install-root "$HOME/.local/share/trace-marketing" \
  --uv "$(command -v uv)" \
  --gh "$(command -v gh)"
trace-marketing worker status
trace-marketing worker updater-status
```

Enrollment writes a revocable machine credential with mode `0600`. It is separate from Codex auth
and is not stored in macOS Keychain. The LaunchAgent stores neither credential; it contains the resolved `trace-marketing` and `codex`
executable paths, an allowlisted set of non-secret worker overrides, and runs in the current user's
`gui/<uid>` domain, so Codex resolves the same user's normal login cache or Keychain entry.
`worker status` reads and checks that pinned plist path rather than another `codex` found in the
invoking shell.

## Replace or operate a Mac

```bash
trace-marketing worker stop
trace-marketing worker start
trace-marketing worker restart
trace-marketing worker status
trace-marketing worker set-state --state draining
trace-marketing worker revoke
trace-marketing worker uninstall-service
```

To replace a machine, drain or revoke the old worker in the workspace, prepare another Mac, create a
new enrollment code, enroll it, and finish bootstrap. No source edit, committed UDID, shared Codex
thread, or Cloudflare Queue-token rotation is required.

## Automatic release updates

A qualifying PR checks the release envelope and fresh offline installation on an arm64 GitHub
runner. The checked bytes are transferred unchanged to the publication job. Merging to `main`
derives the version from `pyproject.toml`, creates an annotated tag for the exact merge SHA, uploads
and attests the three-asset envelope as a draft, verifies each artifact against the repository,
signer workflow, `main` ref and exact merge SHA, publishes the verified draft, and performs
an unauthenticated public readback. Repository-level immutable releases are not required. A rerun
resumes verification of an already-published exact managed release. The same merge independently applies Cloudflare
migrations, deploys the hosted workspace, and requires both health endpoints to report that exact
merge SHA. No CI job connects to a team Mac.

`com.corca.trace-marketing-updater` is separate from the KeepAlive worker and periodically runs a
pull update. It accepts only a newer stable release whose exact three-asset envelope has valid
workflow-bound provenance for the recorded commit, stages it beside the running version, and asks
the worker to stop claiming new leases. Already
durable work and callbacks continue. If received/running inbox rows, pending callbacks/approvals, or
an execution marker without `result.json` remain, the attempt is deferred without stopping the
worker.

After local quiescence, the updater unloads the worker, atomically switches `current`, then requires
launchd status, `worker doctor`, and a newly accepted heartbeat carrying the exact candidate
version. Any failure restores the previous last-known-good symlink and applies the same checks to
the old worker.

```bash
trace-marketing worker update --dry-run
trace-marketing worker update --apply
trace-marketing worker updater-status
trace-marketing worker uninstall-updater
```

The updater never stores an administrator token, enrollment credential, or Codex authentication in
its plist, logs, or state. It does not upgrade Codex CLI, Xcode, Appium, XCUITest, or the Trace app.

## Codex settings

By default the worker uses the selected user's normal Codex CLI configuration and model. Export any
optional non-secret overrides before `worker install-service`; the installer captures only the
allowlisted values in the plist. After changing one, rerun `worker install-service`.

```text
TRACE_CODEX_BIN                 # absolute Codex executable selected during service install
TRACE_CODEX_MODEL               # optional per-worker model override
TRACE_CODEX_TIMEOUT_SECONDS     # default: 180
```

The worker always adds `codex exec --ephemeral --sandbox read-only --output-schema ...`. It does not
pass auth environment variables or ignore the user's Codex configuration.

Other worker settings:

```text
TRACE_AGENT_HOME                       # default: ~/.trace-agent
TRACE_AGENT_APPIUM_SERVER              # default: http://127.0.0.1:4723
TRACE_AGENT_GENERATION_TIMEOUT_SECONDS # default: 120
TRACE_AGENT_TRACE_COMPONENTS  # default: packaged assets/trace-components.png
TRACE_AGENT_IPHONE_UI         # default: packaged assets/iphone-ui.png
```

### Candidate pipeline

A candidate is the post unit of the workspace: a topic, a caption, its hypothesis, the references
and principles behind it, and the free-form Appium prompt used to build its image. Candidates have two
entrances, automatic and manual, and both land in one list before a human approval gate. The 후보
tab opens on a 새 게시물 만들기 block: 후보 자동 생성 is the primary action and 수동 등록 is a
collapsed form for pasting one candidate by hand — 주제/컨셉 comes first and is required, and 국가
is a KR/JP/TW/US dropdown that defaults to KR. Below the block the tab lists every candidate in the
workspace, newest first, with the topic as its main line and the caption's first line beneath it.
The 캡션·주제 승인 tab shows one card per candidate that is still awaiting a decision: the topic is
the card headline, the caption sits directly under it, and the generated wallpaper when one exists,
hypothesis, applied principles, references, the recorded AI verdict, and the Appium prompt follow.
One approve/reject pair decides the topic and the caption together — there is no separate per-field
gate.

Every candidate row and approval card carries the same three-step journey line,
`① 캡션·주제 승인 → ② 이미지 승인 → ③ 제출`, so the current position is visible. Stages ① and ② are
implemented; ③ is reached by approving the image, and posting itself stays manual:

| Status | Meaning | Reachable today |
| --- | --- | --- |
| `awaiting_review` | 캡션·주제 검수 대기 | yes, on creation |
| `caption_approved` | 캡션·주제 승인됨 · 이미지 대기 | yes, by approving stage one or rejecting an image |
| `rejected` | 반려됨 | yes, by rejecting stage one |
| `image_awaiting_review` | 이미지 검수 대기 | yes, by generating an image |
| `submitted` | 제출됨 · 게시 준비 완료 | yes, by approving an image |

The 캡션·주제 승인 tab holds both gates. `① 캡션·주제` lists candidates awaiting the first decision.
`② 이미지` lists candidates that passed it: a `caption_approved` card offers `이미지 생성` and
shows `이미지 생성 중… (1~3분)` while the request runs, and an `image_awaiting_review` card shows the
generated wallpaper with the topic and caption beside `승인` and `반려` plus a reason field. A
rejection returns the candidate to `caption_approved` with the note, so a new wallpaper can be generated.

Every candidate carries a `🧠 생성 근거` panel naming the context documents the run read with their
sizes, the model, the instruction length, and the persona domains that batch was assigned. An image
card additionally shows the background search query, the source page the winning image came from,
and a `배경 심사` panel listing every image the judge looked at with its grades or the reason it was
gated, plus every search query the run tried. A locally composed image is labelled as such.

`삭제` on a candidate asks once inline — `정말 삭제할까요?` with `삭제 확정` and `취소` — and disarms
itself after eight seconds if left alone. Confirming removes the candidate at any stage together
with its artifact directory.

Opening the workspace database runs idempotent migrations for rows written before these fields
existed: `accepted` becomes `caption_approved`, candidates stored before `topic` was required are
backfilled with the placeholder `(주제 미기록)`, and `persona_domain`, generation provenance, and
background provenance are added as nullable columns. Every addition is additive, so a database
written by an older build stays readable. Posting stays manual and outside this runtime.

후보 자동 생성 is wired to the durable Agent. One click snapshots the operator's context
documents into one instruction and calls the model once. Each candidate in the batch is assigned
the least-covered persona domain, the recent topics are shown so a batch does not repeat itself, and
the reply must be a JSON array of exactly the requested length — one failed validation is retried
once, a second stores nothing. Candidates land as `source=auto`, awaiting caption approval like any
manual candidate, each carrying the provenance of the run that wrote it. The button disables itself
and shows `생성 중… (1~3분 소요)` while the request runs, then refreshes the list. It needs two things:

- **Provide context Markdown.** The generator reads the six documents it reasons from — the global
  and Korean principles, the Korean elements, voice, and facts, and the Korean reference index —
  below `<serve workspace>/context` unless `TRACE_AGENT_CONTEXT_DIR` points elsewhere; when neither
  exists it uses the packaged starter context. A missing directory, blank file, unreadable file, or
  symlink stops the run before any model call and the browser names what is unusable.
- **A logged-in agent credential.** Run `trace-agent auth login` in the terminal first; without it
  the button reports `AI 로그인이 필요합니다`.

The Agent run owns the provider/tool loop rather than parsing prose or a fenced JSON response.
The connector validates the candidate schema and distinct topics while country, posting slot,
background intent, and creative direction remain model decisions grounded in the supplied context;
typed tool failures return to the model for replanning without a hard-coded retry count. The
generated `appium_prompt` is stored as the candidate's Appium 프롬프트.

| Route | Purpose |
| --- | --- |
| `GET /api/candidates` | List the authenticated member's workspace candidates, newest first |
| `POST /api/candidates` | Create a manual candidate from `topic`, `country`, `caption`, `hypothesis`, `image_inputs`, and optional `refs_used`/`principles_applied`/`shooting_order`; the server forces `source=manual` and `status=awaiting_review` |
| `POST /api/candidates/generate` | Run a context-grounded Agent goal and store its distinct `source=auto` candidates through the typed Trace connector; `409` for a missing context folder or credential, `502` for a provider failure |
| `POST /api/candidates/{candidate_id}/review` | Stage-one decision on topic and caption together: `caption_approved` or `rejected`, with an optional note and an `expected_revision` guard |
| `POST /api/candidates/{candidate_id}/generate-image` | Stage-two wallpaper generation: search and import a background, configure Trace's real wallpaper editor, collect its verified full PNG, and move a `caption_approved` candidate to `image_awaiting_review`; `409` for the wrong stage, a stale revision, or a failed run |
| `POST /api/candidates/{candidate_id}/review-image` | Stage-two decision: `submitted`, or back to `caption_approved` with the note; `409` for the wrong stage or a stale revision |
| `GET /api/candidates/{candidate_id}/image` | Serve the verified Trace wallpaper PNG to the owning workspace; `404` when the candidate has no image |

Every decision carries the candidate's current revision and only applies from the expected stage. A
stale revision or a repeated decision returns `409`, an unknown candidate returns `404`, and
candidates are never visible or reviewable from another workspace. The Appium prompt is stored in
the `shooting_order` field; only its UI label was renamed.

`image_inputs` is the machine half of a candidate and the manual form collects it: 잠금화면 일정 is a
textarea with one item per line (1–8 lines), 기기 시각 takes `HH:MM`, 배경 소재 is a dropdown with
Korean labels, 배경 분위기 is a short free-text mood, 배경 검색어 is the optional query the open-web
background search runs verbatim, 페르소나 도메인 is optional on a manual candidate, and the content
language is derived from the selected country. Leaving 배경 검색어 blank builds the query from the
subject, the mood, and the topic instead.

#### What the image stage needs

`이미지 생성`은 후보의 승인된 컨텍스트를 durable Agent run으로 스냅샷하고 `trace-marketing`
version `1.0.0` connector를 실행합니다. 모델은 strict wallpaper plan의 IANA `time_zone`, 카드
제목, UTC timed 또는 all-day 일정, 이벤트 색상, 지원되는 row layout·style, 배경 검색 의도를
매번 새로 결정합니다. timed 일정의 source `trace_item`은 그 시간대의 local `HH:MM`과 time
prefix 없는 title이 일치해야 합니다. Trace connector는 실제 Simulator/Appium editor interaction과
full-wallpaper export provenance를 검증합니다. 주어지지 않은 연령·직업 같은 페르소나 사실은
기본값으로 꾸며내지 않습니다.

- **An image search route.** The stage uses the same provider selection as `trace-generate-one`:
  install `ddgs`, or set `BRAVE_SEARCH_API_KEY`. `TRACE_AGENT_WEB_SEARCH_PROVIDER` and
  `TRACE_AGENT_WEB_SEARCH_TIMEOUT_SECONDS` override the choice and its timeout. The background is
  searched across the open web rather than a stock-photo allowlist, because a persona's real lock
  screen holds specific people, characters, and teams. Stock-library hosts are dropped before
  download, portrait crops are preferred, no single site may supply the whole pool, and the model
  then looks at every surviving image and picks one. A round it rejects entirely is retried with a
  widened and then a rewritten query before the stage fails. Without a working route the browser
  reports a typed generation failure and the candidate stays `caption_approved`.
- **A native Trace wallpaper route, or the local composition.** A usable iPhone Simulator,
  Appium/WDA, and the Trace build are resolved at execution time. The normalized searched image
  enters Simulator Photos; the request-owned calendar/event data starts Trace, and Appium configures
  the real editor's visual controls before Save. The request-bound full wallpaper manifest must
  verify before review can advance. This is not a physical iPhone or iOS lock-screen screenshot
  route.

  On a host where no capture device resolves, the stage composes locally instead: the judged
  background, the packaged Trace component layer, and the packaged iPhone system UI merged
  deterministically. `TRACE_AGENT_TRACE_COMPONENTS` and `TRACE_AGENT_IPHONE_UI` override those two
  packaged assets. The local composition **cannot** draw the candidate's own schedule items or
  device time — the component layer is a fixture, not a capture — and it records itself as
  `local_fallback` in the candidate's background provenance and as `offline_fixture` in the capture
  provenance, so it can never pass the native export gates or be mistaken for a device export.

Artifacts are written under
`$TRACE_AGENT_HOME/generated/candidate-<candidate-id>-r<revision>/`. The candidate stores the final
relative path and SHA-256 only after Agent, native provenance, path confinement, and digest
checks pass.

### Continuous campaign and review flow

This flow is API-only after the two-tab restructure: the browser no longer has the 자료 준비,
생성 큐, or 새 자료 만들기 surfaces that used to drive it, and the routes below are reachable only
through the API.

Save a persona as `PersonaProfile` JSON and a promotion as `PromotionMaterial` JSON through
`/api/contexts`. A promotion may include one to eight `trace_items`. The planner turns them into
persona-specific Trace card headers, grouped event lists, and a native row layout before capture.
Upload optional visual references, then reference those records when creating a campaign
through `/api/campaigns`. Choose a finite count or leave continuous production enabled. The campaign freezes those inputs and the service creates one
uniquely identified variation at a time. Stopping a campaign prevents future variations without
erasing work already submitted or running. A known image-search, filesystem, or Appium
failure records the queue item as failed and automatically stops that campaign instead of producing
an unbounded failure loop.

Each variation becomes a typed `MarketingContextBundle`. Reference images are digest-checked again,
sent as Responses image inputs, and used with high-fidelity image editing. The scheduler claims due
queue records through a single active worker lease. The worker invokes `GenerateOneRunner`, verifies
run identity, output presence, and the artifact SHA-256, then moves a verified result to `review`.
The advanced one-shot JSON form and lower-level `/api/queue` route remain available for explicit
integrations.

Review is explicit: a member accepts or rejects the review record, and optimistic revisions
reject stale decisions. An expired running attempt becomes `failed/unknown_side_effect` and is
not blindly retried. `trace-agent serve` starts the web/API process and its persistent scheduler/
worker; it does not claim that a queued item has completed until the worker records that state.
The MVP has no automatic external publication step.

### launchd service

The native CLI installer supplies `trace-agent` on `PATH`. After activating the local venv or
installing the user-local tool, install the per-user macOS service:

```bash
trace-agent workspace start --port 8765 --workspace-name "Launch archive"
trace-agent service status
launchctl print "gui/$(id -u)/com.corca.trace-agent"
```

`workspace start` uses the same launchd service boundary. It writes
`~/Library/LaunchAgents/com.corca.trace-agent.plist`, creates
protected stdout/stderr files below `$TRACE_AGENT_HOME/logs/`, loads the job by default, waits for the
previous launchd job to finish unloading before replacement, and then waits briefly for the local
service and the emitted public URL to become ready. On an existing state root,
`--workspace-name` updates the stored workspace name when you intentionally rerun setup.
The default launchd command requests cloudflared; use `--tunnel none` for local-only service
operation. Use `--no-load` to generate the plist without starting it or `--plist <path>` to
choose an explicit plist path. To stop the loaded per-user job, run:

```bash
launchctl bootout "gui/$(id -u)/com.corca.trace-agent"
```

`service status` reports whether the plist exists and whether the saved local URL answers
`/health`. When the local service is healthy, it reports the public URL emitted by the configured
cloudflared process. It does not require a second public DNS probe from the same Mac, because a
local resolver failure can hide a tunnel that external team members can still reach.

### Optional public URL

The default cloudflared mode uses the `cloudflared` executable on `PATH`. The adapter starts a
bounded quick tunnel and accepts only a live `https://*.trycloudflare.com` URL emitted by that
process. If cloudflared installation is disabled, the executable is missing, the tunnel times out,
or it exits without a URL, the service stays available at its loopback URL and prints
`Public URL: unavailable (...)`. The URL is shown after the local service is healthy and the
tunnel process has emitted it; external reachability still depends on the network used by each
team member. Configure a stable domain separately if the deployment needs one.

### Generation prerequisites

The workspace web/API shell can start without native capture dependencies. A queued generation
or `generate-one` run also needs:

- network access to DDGS image search or `BRAVE_SEARCH_API_KEY` for the approved background-source search
- Xcode and an available iOS Simulator
- a Debug Trace build installed as `com.corca.Trace`
- the request-bound Trace wallpaper-export trigger and `LockScreenWallpaperSheet` accessibility
  controls from the sibling `Trace_iOS` checkout
- Appium 3 with the XCUITest driver, normally at `http://127.0.0.1:4723`

The installer does not install Xcode, Appium, the XCUITest driver, or the Trace Debug build.
Prepare those prerequisites manually before accepting a generated result.

### Compatibility smoke checks

The workspace commands are additive. These checks keep the existing aliases and one-shot path
visible while exercising the new service boundary:

```bash
uv run trace-ads --help
uv run trace-agent --help
uv run trace-agent serve --help
TRACE_AGENT_HOME="$(mktemp -d)" uv run trace-agent auth status
uv run trace-agent generate-one --help
uv run trace-agent workspace --help
uv run trace-agent service --help
```

## Context, compaction, and prompt cache

대화 history는 canonical transcript로 보존하고, provider에 보내는 model projection만
줄입니다. context pressure는 다음 순서로 처리합니다.

1. 큰 `function_call_output`을 request-local로 prune합니다.
2. soft limit에 도달하면 marketing goal, country/persona/caption 결정, artifact reference,
   미검증 gap을 보존하는 compact summary를 만들고 최근 turn tail을 유지합니다.
3. summary는 JSONL memory file에 저장하며 원본 history는 삭제하지 않습니다.
4. provider가 context overflow를 반환하면 같은 turn을 한 번만 compact 후 retry합니다.

기본값은 128k context, 70% soft threshold, 85% hard threshold, 최근 16k token tail,
tool output 6k 문자입니다. 다음 환경변수로 조정할 수 있습니다.

```text
TRACE_AGENT_CONTEXT_WINDOW_TOKENS
TRACE_AGENT_CONTEXT_SOFT_RATIO
TRACE_AGENT_CONTEXT_HARD_RATIO
TRACE_AGENT_CONTEXT_RECENT_TAIL_TOKENS
TRACE_AGENT_CONTEXT_MAX_TOOL_OUTPUT_CHARS
TRACE_AGENT_MEMORY_FILE       # default: ~/.trace-agent/memory.jsonl
TRACE_AGENT_WEB_SEARCH_PROVIDER       # auto, ddgs, or brave; default: auto
TRACE_AGENT_WEB_SEARCH_TIMEOUT_SECONDS # default: 30
TRACE_AGENT_DEVICE_UDID                # optional preferred Simulator; otherwise resolved dynamically
TRACE_MARKETING_CONTROL_TOKEN          # administrator commands only; never target-Mac enrollment
TRACE_MARKETING_INSTALL_ROOT           # default: ~/.local/share/trace-marketing
```

## Local state

| Path | Purpose |
| --- | --- |
| `$TRACE_AGENT_HOME/marketing-worker/config.json` | Non-secret worker identity and control-plane URL |
| `$TRACE_AGENT_HOME/marketing-worker/credential.json` | Revocable worker credential, mode `0600` |
| `$TRACE_AGENT_HOME/marketing-worker/runtime/` | Durable task inbox and callback outbox |
| `$TRACE_AGENT_HOME/codex-runs/<request-id>/` | Input digest, validated plan, execution marker, terminal result |
| `$TRACE_AGENT_HOME/generated/<request-id>/` | Background provenance and verified native PNG |
| `$TRACE_AGENT_HOME/logs/` | Protected LaunchAgent stdout/stderr |
| `~/.local/share/trace-marketing/releases/<version>/` | Immutable installed product and receipt |
| `~/.local/share/trace-marketing/current` | Atomic symlink to the active release |
| `~/.local/share/trace-marketing/update-state.json` | Non-secret candidate and last-known-good state |

Before Appium starts, the worker records a D1 execution barrier and then a local marker. If the Mac
stops after that boundary, lease expiry cannot move the task to another Mac. Before R2 or candidate
mutation, a second D1 reservation atomically binds the callback ID and normalized result digest to that
worker and lease, so a stale or changed callback cannot race a replacement. Worker revocation is
deferred while that reservation is incomplete. The original Mac can return `unknown_side_effect`; otherwise an
operator must inspect the task and explicitly revoke the old worker before allowing a retry.

## Development verification

Use focused checks for the boundary being changed:

```bash
uv run pytest -q \
  tests/providers/test_codex_cli.py \
  tests/connectors/trace/v1/test_codex_runtime.py \
  tests/marketing/test_worker_broker.py \
  tests/marketing/test_worker_update.py \
  tests/cli/test_release_builder.py \
  tests/cli/test_installer.py
uv run ruff check \
  src/ads_booster/providers/codex_cli.py \
  src/ads_booster/connectors/trace/v1/codex_runtime.py
```

For product proof, build the offline release envelope, install its wheelhouse into a fresh isolated
environment, resolve `trace-marketing` from that installed PATH, and run `version --json` plus
`worker doctor`. Worktree-only `uv run` success is development evidence, not fresh-install proof.
Merge automation completes the CI-owned release and hosted deployment surfaces. A Mac is an
independently enrolled dynamic consumer; after its first registration, reboot readback, exact-version
heartbeat, and one Codex → Appium → callback canary establish that machine's operational readiness
without becoming a CI-to-Mac deployment path.

## Current limits

- Threads publication and metrics readback are not implemented.
- Physical iPhone support, automatic Trace debug-build signing/install, geographic routing, and
  worker autoscaling are deferred.
- A real prepared-Mac canary is still required to prove the complete Codex → Appium → R2 round trip
  after deployment.
