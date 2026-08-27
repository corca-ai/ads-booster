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

## Bootstrap an immutable Mac worker release

```bash
release="$(gh release view --repo corca-ai/ads-booster --json tagName --jq '.tagName')"
curl -fsSL --proto '=https' --tlsv1.2 \
  "https://raw.githubusercontent.com/corca-ai/ads-booster/$release/install.sh" |
  bash -s -- --tag "$release"
export PATH="$HOME/.local/share/trace-marketing/current/bin:$PATH"
trace-marketing version --json
```

The one-time bootstrap requires `gh`, `uv`, and a locally available Python 3.14, but never installs
or upgrades them. It verifies a stable immutable GitHub Release, its tag and exact commit, all
GitHub SHA-256 asset digests, local digests, and build attestations. It then performs an offline
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
  --uv "$(command -v uv)"
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

A repository administrator performs this one-time preparation before the first merge-authorized
release. The token is used only by the operator's `gh` process and is not stored in Actions or on a
Mac:

```bash
gh api --method PUT -H 'X-GitHub-Api-Version: 2026-03-10' \
  repos/corca-ai/ads-booster/immutable-releases
gh variable set TRACE_IMMUTABLE_RELEASES_ENABLED --repo corca-ai/ads-booster --body true
```

A qualifying PR checks the release envelope and fresh offline installation on an arm64 GitHub
runner. The checked bytes are transferred unchanged to the publication job. Merging to `main`
derives the version from `pyproject.toml`, creates an annotated tag for the exact merge SHA, uploads
and attests the three-asset envelope as a draft, publishes it as an immutable stable GitHub Release,
and verifies it again through an unauthenticated public readback. A rerun resumes verification of an
already-published exact immutable release. The same merge independently applies Cloudflare
migrations, deploys the hosted workspace, and requires both health endpoints to report that exact
merge SHA. No CI job connects to a team Mac.

`com.corca.trace-marketing-updater` is separate from the KeepAlive worker and periodically runs a
pull update. It accepts only a newer stable immutable release with the exact three-asset envelope,
stages it beside the running version, and asks the worker to stop claiming new leases. Already
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
TRACE_AGENT_WEB_SEARCH_PROVIDER        # default: auto
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
