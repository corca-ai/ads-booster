# Trace Marketing Pipeline

`ads-booster` provides the public Trace marketing workspace on Cloudflare and a replaceable Mac
worker that creates verified Trace wallpaper images with Codex CLI and Appium. Candidate generation,
review state, account isolation, schedules, task leases, and artifacts remain hosted. Threads posting
is intentionally not implemented.

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
   candidates.
2. Candidate selection creates a hosted capture task whose approved caption, hypothesis, references,
   creative direction, background intent, profile, and Trace items are immutable inputs. D1 assigns
   one lease to a healthy enrolled Mac.
3. The Mac starts a new ephemeral `codex exec` turn. The marketing context is sent over stdin and the
   final output must match the strict `WallpaperPlan` JSON schema.
4. Code validates request ID, time zone, local event times, references, layout, and style. The
   Mac then records an execution barrier in D1; Appium cannot start unless that barrier succeeds.
   Invalid plans never reach Appium.
5. The deterministic runner finds an approved background and drives the real Trace Simulator app
   through Appium. A request-bound export, digest, nonce, device binding, and PNG provenance are
   verified.
6. The callback stores the verified image in R2 and binds the exact wallpaper plan plus background
   source provenance to that candidate attempt before exposing it for human review.
7. Approval reaches `submitted`. Rejection sends its structured tags, rating, and note into the next
   image attempt immediately. A stage/target rule is learned only after the same signal appears on
   three different candidates; teammates can inspect or disable it without rewriting the profile.
   No external social post is created.

Codex threads are ephemeral per task, so two accounts and two Macs do not share conversation
history. Validated plans and terminal outcomes are request-scoped under
`$TRACE_AGENT_HOME/codex-runs`; prompts, Codex responses, and auth data are not persisted there.
The staged prompt, persona, structure, and evaluation roadmap is documented in
[`docs/plans/creative-quality-development.md`](docs/plans/creative-quality-development.md).

## Install the Mac worker CLI

```bash
curl -fsSL --proto '=https' --tlsv1.2 \
  https://raw.githubusercontent.com/corca-ai/ads-booster/main/install.sh | bash
source ~/.zshrc
trace-marketing --help
```

For a local checkout:

```bash
bash install.sh --source .
```

The installer uses a user-owned `uv tool` environment and verifies `trace-marketing`. It does not
install or authenticate Codex, Xcode, Appium, XCUITest, or the Trace debug build.

## Prepare a Mac

Run these as the same macOS user that will own the LaunchAgent:

```bash
codex login
codex login status
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

The usual operator path is the protected Mac manager inside the workspace. It creates a short-lived,
single-use enrollment command without putting the administrator token on the target Mac.

The equivalent administrator CLI flow is:

```bash
export TRACE_MARKETING_CONTROL_TOKEN='...'
trace-marketing worker create-enrollment \
  --url https://workspace.borca.ai \
  --name 'Studio Mac'
```

On the target Mac, use the returned code:

```bash
trace-marketing worker enroll \
  --url https://workspace.borca.ai \
  --code '...'
trace-marketing worker install-service
trace-marketing worker status
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
new enrollment code, enroll it, and install its service. No source edit, committed UDID, shared Codex
thread, or Cloudflare Queue-token rotation is required.

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
TRACE_AGENT_WEB_SEARCH_PROVIDER        # default: auto
TRACE_AGENT_WEB_SEARCH_TIMEOUT_SECONDS # default: 30
TRACE_AGENT_DEVICE_UDID                # optional preferred Simulator; otherwise resolved dynamically
TRACE_MARKETING_CONTROL_TOKEN          # administrator commands only; never target-Mac enrollment
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
  tests/cli/test_installer.py
uv run ruff check \
  src/ads_booster/providers/codex_cli.py \
  src/ads_booster/connectors/trace/v1/codex_runtime.py
```

For product proof, install into a fresh isolated `uv tool` directory, resolve `trace-marketing` from
that installed PATH, and run `worker doctor`. Worktree-only `uv run` success is development evidence,
not fresh-install proof.

## Current limits

- Threads publication and metrics readback are not implemented.
- Physical iPhone support, automatic Trace debug-build signing/install, geographic routing, and
  worker autoscaling are deferred.
- A real prepared-Mac canary is still required to prove the complete Codex → Appium → R2 round trip
  after deployment.
- Feedback rules currently learn from human caption/image review only. Post-publication outcome
  metrics remain unavailable while publication is disabled.
