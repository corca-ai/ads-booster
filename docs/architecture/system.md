# System Architecture

Status: Active
Last reviewed: 2026-08-27

## Purpose

This document describes the deployed Cloudflare workspace and installed Mac worker. Product
behavior is established with a fresh-installed `trace-marketing` command plus the deployed
`workspace.borca.ai` surface; worktree execution is implementation evidence only.

## Process topology

```mermaid
flowchart LR
    UI[Public workspace UI] --> API[Cloudflare Worker API]
    API --> D1[D1 accounts contexts candidates runs workers leases]
    API --> WAI[Workers AI candidate generation]
    API --> WF[Cloudflare Workflow approvals]
    WF --> LEASE[D1 hosted capture lease]
    LEASE --> BROKER[trace-marketing Mac worker]
    BROKER --> CODEX[official codex exec ephemeral read-only]
    CODEX --> PLAN[WallpaperPlan validation]
    PLAN --> SEARCH[approved background search]
    SEARCH --> APPIUM[Appium XCUITest Trace debug app]
    APPIUM --> PNG[request-bound verified PNG]
    PNG --> CALLBACK[authenticated callback]
    CALLBACK --> R2[R2 artifact]
    CALLBACK --> D1
    D1 --> UI
```

The Cloudflare Worker owns public UI/API delivery, account silos, context profiles, candidate state,
schedules, task assignment, worker health, approval waits, and R2 callbacks. Each Mac is a replaceable
machine identity and claims one compatible task through a D1 lease.

The Mac worker uses the official Codex CLI as the model harness. It does not instantiate the former
in-package AgentSession, OAuth store, Responses client, memory, or tool loop. Every task invokes a new
`codex exec --ephemeral --sandbox read-only` turn with a strict output schema. Codex authentication
is resolved normally for the same macOS user that owns the per-user LaunchAgent.

## Installed commands

Commands are declared in `pyproject.toml`.

| Command | Composition root | Responsibility |
| --- | --- | --- |
| `trace-marketing` | `cli/marketing.py` | Worker enrollment, doctor, foreground/service lifecycle, drain/revoke, simulation and compatibility bridge |
| `trace-capture` | `cli/capture.py` | Legacy typed native component capture |
| `trace-compose` | `cli/compose.py` | Legacy validated offline composition |
| `trace-run` | `cli/trace_run.py` | Legacy resumable capture/composition state machine |

`trace-agent` and `trace-ads` are not installed entry points. Their old source modules may remain
temporarily for source compatibility tests, but no production Mac or installer path invokes them.

## Hosted candidate flow

1. The browser loads the login-free hosted workspace and selects a logical account. Account scope
   separates contexts, profiles, candidates, runs, feedback, and learned memory; it is not visitor
   authentication.
2. Cloudflare Workers AI generates context-grounded candidates using `WORKSPACE_AI_MODEL`.
3. A teammate may edit or delete submitted candidates. Candidate approval creates a version-bound
   capture task.
4. D1 assigns one lease to an active worker whose heartbeat doctor is ready. Offline, degraded,
   draining, and revoked workers receive no new task.
5. The Workflow waits for the callback and then for human image approval. Approval reaches
   `submitted`; Threads publication remains outside the runtime.

## Mac planning and capture flow

1. `marketing/worker_broker.py` accepts the lease into the local durable inbox before acknowledging
   remote ownership.
2. `service/worker.py` composes `connectors/trace/v1/codex_runtime.py`.
3. The immutable hosted caption, hypothesis, full topic, reference IDs, creative direction, background
   intent, profile and Trace items are mapped into `MarketingContextBundle`. Hosted textual reference
   IDs are deterministic plan authority even when no binary `reference_images` are attached.
4. `providers/codex_cli.py` passes the context through stdin and requests the Pydantic-generated
   `WallpaperPlan` JSON schema. Optional reference images are validated and attached with `--image`.
5. Domain code revalidates request ID, IANA time zone, every promotion-owned event and its plan-zone
   local `HH:MM`, reference IDs, layouts, colors, and style values.
6. Immediately before Appium, the worker first records `execution_started_at` in D1, which removes
   the expiring lease from automatic reassignment, and then writes the local execution marker. A
   crash after either barrier requires the original Mac callback or explicit operator revocation; it
   cannot silently run on a replacement Mac.
7. `GenerateOneRunner` downloads an approved background with provenance, then Appium drives the Trace
   wallpaper editor. Capture code validates the export nonce, request digest, bundle ID, device,
   artifact role, dimensions, and PNG digest.
8. The worker's local outbox delivers the authenticated callback. After payload validation,
   Cloudflare atomically reserves the callback ID plus normalized result digest against the current
   worker and lease before writing R2 or changing candidate state. An identical partial retry may
   continue; changed content is rejected, and revocation waits until the reservation completes.

## Authentication and secrets

There are three separate identities:

- the Cloudflare administrator token, used only to manage workers;
- a revocable per-machine worker credential stored in a mode-`0600` file;
- the current macOS user's Codex login, managed by the official CLI in its normal cache or Keychain.

The LaunchAgent plist contains no token. It stores the resolved `trace-marketing` and `codex` paths,
`TRACE_AGENT_HOME`, `PATH`, and only the documented allowlisted non-secret worker overrides, and runs in `gui/<uid>`. The worker never copies Codex auth into its
state, D1, task payloads, logs, or plist.

## Local state

## Generation and TraceRun flow

CLI and scheduled campaign generation submit one `AgentGoal` with a frozen
`MarketingContextBundle` to the durable Agent run store. The run selects connector identity
`trace-marketing` at exact version `1.0.0` from `connectors/trace/v1` and exposes only its allowed
semantic capability.

1. `AgentRuntime` loads the goal, connector version, tool policy, canonical history, latest memory,
   and structured persona/promotion/reference context into one `AgentSession`.
2. The model calls `trace_generate_marketing_image` with a complete strict `WallpaperPlan`: one
   explicit IANA time zone, background query, supplied reference IDs, row layouts, component titles,
   colored calendar events, and supported Trace visual style values. Events are either all-day or
   have unambiguous UTC start and end times. Agent code does not select those creative values from
   persona-specific tables.
3. The Trace connector verifies request ownership, source references, event coverage, native layout
   constraints, and the scheduled run's side-effect authority. For every timed event, it converts
   UTC only through `WallpaperPlan.time_zone` and requires the promotion `trace_item` to be that
   local `HH:MM` plus the event's clean title. This rejects a wrong UTC conversion or time repeated
   inside the title before Trace renders it.
4. The search adapter restricts results to approved public-source domains, downloads a readable
   background, normalizes it to PNG, and records URL and digest provenance.
5. `GenerateOneRunner.run_plan` creates a request-bound wallpaper capture contract without
   rewriting the model-authored plan.
6. The capture adapter clears a prior export, imports the normalized background with `simctl`
   Photos, and opens Trace with export binding metadata only. No card, row, title, time, all-day,
   or event-color payload is passed in launch arguments. Repeated Simulator captures reuse the
   installed WebDriverAgent instead of forcing an Xcode rebuild before every UI session; the Appium
   session and all later UI commands remain bounded by the capture deadline.
7. Appium uses Trace's real calendar, event, and `LockScreenWallpaperSheet` controls to create the
   request-owned data and set every visual value from the plan. Calendar/event content, including
   the explicit plan time zone, is entered through the UI; neither Trace nor Python may fall back
   to the Mac or Simulator time zone.
8. Save invokes Trace's `renderWallpaper` path, which writes `trace_wallpaper.png` and its native
   binding manifest into the App Group.
9. The collector copies the full wallpaper to the run output and validates opaque PNG bytes,
   SHA-256, request digest, nonce, device binding, dimensions, and manifest before accepting it.
10. `outputs/final.png` is that verified Trace export. `TraceRunResult` uses
    `trace.run-result.v2` and moves the Agent run to `awaiting_approval` for human review.

Agent runs persist goal, connector/tool policy, history, observations, revision, and lifecycle
in `agent-runs.sqlite3`. TraceRun records mechanical transitions in an append-only JSONL journal.
A newly constructed workspace process requeues Agent runs inherited in `running` with a durable
`service_restart` failure observation before it accepts requests. This recovers work interrupted by
the previous process while same-process duplicate requests still receive a conflict. The nested
TraceRun journal remains authoritative for external side effects and still resolves an interrupted
capability as `unknown_side_effect` instead of invoking it again.
A resumed TraceRun journal that stopped while
awaiting an external tool moves to `unknown_side_effect` instead of repeating an operation whose
effect cannot be proven. Run identity, idempotency key, paths, digests, and state transitions are
validated before completion is reported.

The primary pipeline captures a fresh full Trace wallpaper export for each run. It does not call an
image-generation model, set a physical iPhone wallpaper, or claim an iOS lock-screen screenshot.
`trace-capture`, `trace-run`, and `trace-compose` retain their separate legacy component-export and
offline-composition contracts; they are not part of `generate-one`.

## Team workspace and Web flow

The native installer only installs the CLI. `trace-agent workspace start` (or the lower-level
`service install`/`serve` commands) explicitly starts the workspace lifecycle. `trace-agent serve`
prepares a loopback listener, bootstraps a workspace when needed, requests a
cloudflared quick tunnel by default, and starts a Uvicorn process with the FastAPI application and
an explicitly attached automation worker. Readiness requires the loopback service to answer
`/health` and cloudflared to emit a public URL; it does not perform a second public DNS probe from
the same host. During launchd replacement, the service waits for the previous job to finish
unloading before bootstrapping the new plist. `--tunnel none` opts out of public access.

On first start:

1. `SqliteWorkspaceStore` creates one workspace and one owner member.
2. Plaintext workspace and member codes are returned once to the foreground CLI.
3. Only scrypt hashes and code versions are stored in SQLite.
4. `service.json` stores identifiers and service configuration, not plaintext access codes.

The first owner member recorded in `service.json` is the authenticated Web administrator. The owner
can use `POST /api/members/invite` from the browser to create a regular member and receive a
three-part member access ID once. A regular member can redeem that ID through
`POST /api/auth/member-login`; the existing four-part owner access ID and `/api/auth/login` contract
remain unchanged. `trace-agent workspace add-member --name <name>` remains a local fallback.

`workspace access` emits one copyable `%`-separated browser login ID containing the workspace ID,
member ID, workspace code, and member code. The browser parses that value at the entry boundary and
submits the existing four-field payload to the auth route. Successful login creates an HMAC-signed
cookie containing workspace/member IDs, code versions, and expiry. The signing secret is process-local
by default, so a service restart invalidates existing browser sessions. Code rotation also invalidates
sessions whose embedded versions are stale.

The browser surface is two tabs, 후보 and 캡션·주제 승인, both backed by `/api/candidates`. The
context, asset, campaign, queue, and chat routes described below still run inside the same process
and keep their tests, but no browser tab renders them; they are reachable only as API endpoints.

Shared context is workspace-scoped. Private conversation history is scoped by workspace, member,
and session. A chat request loads shared context as a read-only developer prefix, runs a fresh
request-local `AgentSession` built by the same factory as the TUI, and persists only the resulting
private history. The Web adapter dispatches `/auth`, `/model`, `/permission`, `/new`, `/clear`,
`/session`, and `/help` through the existing TUI command contract. Live model, reasoning, permission,
and approval state is isolated by authenticated member while the provider OAuth credential remains
host-owned. Optimistic revisions reject concurrent writes with a conflict instead of silently
overwriting a newer session.

Reference assets are workspace-scoped. The authenticated upload route accepts JPEG, PNG, or WebP
data, verifies the decoded image, writes a protected file below `TRACE_AGENT_HOME/assets/`, and
records its normalized path, digest, size, and optional context binding. Campaign creation verifies
the path, bytes, and digest again before freezing the reference into generation input.

Post candidates are workspace-scoped rows in the same workspace database. A candidate carries the
topic, caption, hypothesis, references, applied principles, the free-form Appium prompt stored in
`shooting_order`, and the machine `image_inputs` the image stage needs: one to eight lock-screen
schedule items, an `HH:MM` device time, a background subject drawn from a fixed vocabulary, a short
background mood, an optional authored background search query, and the content language. A
`background_intent` is composed from the subject and mood when a writer does not supply one, so a
payload carrying only that free-text field stays readable and one carrying only the vocabulary pair
still reaches the connector. A generated candidate also carries its `persona_domain` and the
generation provenance of the batch that wrote it, and a composed candidate carries the provenance of
the background behind its image. A candidate enters at `awaiting_review`. Topic and caption are
reviewed together as one decision, so the first gate has a single approve/reject pair.
`/api/candidates` creates manual candidates and lists a workspace newest-first, and
`DELETE /api/candidates/{candidate_id}` removes one candidate at any stage together with its
artifact directory. Deletion is not a review outcome, so it expects no revision.

A candidate travels three approval stages, and both browser surfaces render that journey so its
position is visible. Stages one and two are implemented:

```text
awaiting_review --approve--> caption_approved --generate image--> image_awaiting_review --approve--> submitted
       |                            ^                                     |
       +----reject--------> rejected +---------------reject---------------+
```

`/api/candidates/{candidate_id}/review` is the first gate and moves one candidate to
`caption_approved` or `rejected` with an optional note. `/api/candidates/{candidate_id}/generate-image`
is the second: it composes one lock-screen image and moves the candidate to
`image_awaiting_review`, and `/api/candidates/{candidate_id}/review-image` either submits the
candidate or returns it to `caption_approved` with the note so a new image can be composed. Every
transition requires the current revision and the expected source status, so a stale or repeated
decision fails with a conflict instead of overwriting the first one. Publishing a submitted post
stays a human action outside this runtime.

### Candidate wallpaper generation

The image stage synchronously submits the approved candidate to the same durable Agent and
Trace v1 connector used by campaign generation. Its primary artifact comes from:

| Input or artifact | Source | Verified by |
| --- | --- | --- |
| Background | `search/image/open_background.py` collects open-web images and `candidate_generation/background_selection.py` has the model choose one | Decodable bytes, minimum edge, stock-host exclusion, per-host cap, AI gate and rubric grades, recorded digest |
| Calendar/event content | Request-owned automation input | Explicit IANA time zone; strict UTC/all-day, event-color, and local `HH:MM` plus clean-title source validation |
| Visual configuration | Real `LockScreenWallpaperSheet` Appium interaction | Required editor accessibility controls and plan values |
| Final wallpaper | Request-bound `trace_wallpaper.png` rendered by Trace | PNG bytes, native manifest, request digest, nonce, device binding, and artifact digest |

The connector receives only facts present in the candidate snapshot. Missing persona attributes
remain absent instead of being filled with creative defaults. The Agent authors the wallpaper plan;
the connector validates it and delegates mechanical background import, editor interaction, and
opaque native-export verification. The Web route confines `outputs/final.png` beneath the Agent run
root and checks its SHA-256 before moving the candidate to image review. Any environment,
generation, provenance, path, or digest failure leaves the candidate at `caption_approved`.

### Background selection

Both composition paths take their background from the same seam, the `BackgroundFetcher` protocol
`GenerateOneRunner` calls with the scene plan's query. The stock allowlist can only ever return the
three photo libraries, so it cannot find the athlete, character, or idol a real lock screen holds.
The judged fetcher searches the open web instead, drops stock-library hosts before downloading,
prefers portrait crops and caps how many images one host may supply, and then shows every surviving
preview to the model. The judge gates the obviously wrong images, grades the rest on authenticity,
persona fit, and background fit, and breaks a near-tie by asking the same pair in both orders and
accepting only a verdict that survives the swap. A round that judges out entirely walks a short
query ladder — widen for free, then one model rewrite — and fails loudly rather than returning an
image the judge just rejected.

The chosen image and the whole judgment are written to the run's `inputs/background-source.json`,
which is the only handoff between the fetcher inside the Trace runner and the candidate store. Both
paths read it back onto the candidate, so a reviewer sees what the winner beat.

### Composition path selection

The native path needs a capture device. On a host where none resolves, the image stage composes
locally instead: the judged background, the packaged Trace component fixture, and the packaged
iPhone system UI merged deterministically. Which path ran is recorded, never inferred. The local
capture port writes `source: offline_fixture` with `native_export_binding_verified` false, so it can
never pass the connector's native export gates, and the candidate's background provenance carries
`pipeline` as `native` or `local_fallback`. The local path cannot render the candidate's own
schedule items or device time — the component layer is a fixture, not a capture — and the recorded
pipeline is what tells a reviewer so. With no local composition configured, an unavailable capture
environment fails the stage and leaves the candidate at `caption_approved`.

Opening the workspace database runs idempotent candidate migrations: rows written under the earlier
single-stage `accepted` status are rewritten to `caption_approved`, which carries the same meaning
on the journey, rows stored before `topic` became a required reviewable field gain the column with
the placeholder value `(주제 미기록)`, and `persona_domain`, `generation_provenance_json` and
`background_provenance_json` are added as nullable columns. Every addition is additive, so a
database written by an older build stays readable.

## Automatic candidate generation

`POST /api/candidates/generate` is the second candidate entrance. Two generators exist and both
live in `candidate_generation/`; the route runs the single-call script engine.

1. Resolve the context directory from `TRACE_AGENT_CONTEXT_DIR`, or `<serve workspace>/context`, or
   the packaged `assets/context/`.
2. Read the six documents the engine reasons from — the global and Korean principles, the Korean
   elements, voice, and facts, and the Korean reference index. The whole corpus cannot go into one
   instruction, so the set is named and an absent, unreadable, or empty document fails before any
   provider call and says which one.
3. Assign one persona domain per candidate from the running coverage counts, least-covered first,
   ties broken by a shuffle. Coverage is counted over a closed nine-token vocabulary and only over
   generated rows, so hand-written candidates do not exhaust a domain.
4. Assemble one instruction from the documents, the assignment, the recent generated topics, and the
   rules the team writes candidates against, including the persona-specificity block and the
   background rules that keep a search query pointed at what someone would keep on their phone
   rather than at their occupation.
5. Call the provider once. The reply must be a JSON array of exactly the requested length, and one
   failed validation is retried once with the validation detail quoted back. A second failure stores
   nothing.
6. Store the batch as `source=auto`, `status=awaiting_review`, each row carrying its assigned domain
   and the provenance of the run: the documents read with their UTF-8 sizes, the model, the
   instruction length, the moment of the call, and the domains assigned.

The Agent-kernel path composed by `build_candidate_generator` remains available and tested. It
admits a durable Agent run and exposes `trace_propose_marketing_candidates`, letting the model
revise invalid fields or duplicate topics through tool observations rather than a fixed retry count.
It is not what the Web route calls.

Neither run has publishing or filesystem-writing capability. The route is a synchronous request
handled in the FastAPI threadpool, so the browser waits for it. Failure modes are typed and mapped
to a status with an operator-facing Korean message: missing context or a missing provider credential
answer `409`, and a provider failure or a response that twice failed the format answer `502`.

## Automation queue

The campaign route creates a durable finite or continuous campaign from one stored persona,
one stored promotion, optional stored references, a reference date, and a device. `CampaignProducer`
keeps at most one outstanding queue item per campaign, assigns a monotonic variation index, and
continues after service restart. A finite campaign becomes `completed` after its requested count;
an operator can move an active campaign to `stopped`. A known production-generation exception is
converted to a failed run result, and a failed current queue item stops its campaign before another
variation is enqueued. The lower-level Web API can still submit an
immediate typed `MarketingContextBundle` or a UTC-due queue item directly.

```text
submitted -> claimed -> running -> review -> accepted
                                      |
                                      +-------> rejected
             |           |
             +-----------+---------> failed
```

- `(workspace_id, idempotency_key)` is unique.
- Repeating the same idempotency key and payload returns the existing record.
- Reusing the key with a different payload fails closed.
- SQLite permits only one `claimed` or `running` record at a time.
- Claims use bounded leases and optimistic revisions.
- `GenerateOneWorker` verifies run identity, idempotency key, artifact location, and artifact digest
  before moving a result to `review`.
- Human approval completes both the queue record and linked Agent run. Rejection invalidates the
  artifact, records feedback on the same Agent run, and requeues the same goal for replanning.

`AutomationServiceWorker` first lets the campaign producer supply the next safe variation, then
polls the durable queue inside the service lifespan. It uses `QueueScheduler` to claim one due
record and runs `GenerateOneWorker` in a worker thread so the
event loop can continue serving HTTP requests. The production worker builds the same
Agent + Trace connector composition used by the one-shot CLI, with service-owned run,
artifact, journal, and capture roots below `TRACE_AGENT_HOME`. Service shutdown cancels the polling
task.

## Local state and artifacts

`TRACE_AGENT_HOME` is the local service and agent state root. It defaults to `~/.trace-agent`.

| Path | Owner | Contents |
| --- | --- | --- |
| `marketing-worker/config.json` | `marketing/` | Non-secret worker identity, pool, origin and poll interval |
| `marketing-worker/credential.json` | `marketing/` | Revocable worker token, mode `0600` |
| `marketing-worker/runtime/` | `marketing/` | Durable inbox and callback outbox |
| `codex-runs/<request-id>/input.sha256` | Trace Codex runtime | Immutable input identity |
| `codex-runs/<request-id>/plan.json` | Trace Codex runtime | Validated structured plan only |
| `codex-runs/<request-id>/executing` | Trace Codex runtime | Native side-effect uncertainty barrier |
| `codex-runs/<request-id>/result.json` | Trace Codex runtime | Terminal typed result |
| `generated/<request-id>/` | `runtime/`, `capture/` | Background provenance and verified full-wallpaper PNG |
| `logs/` | LaunchAgent | Protected stdout and stderr |

Prompts, raw Codex responses, Codex threads, auth tokens, and auth-cache files are not stored below
`TRACE_AGENT_HOME` by this runtime.

## External boundaries

| Dependency | Boundary |
| --- | --- |
| Official Codex CLI | Ephemeral, read-only, schema-constrained subprocess; inherits same-user auth |
| Appium 3 / XCUITest | Simulator control and Trace accessibility interaction |
| Trace debug app | `com.corca.Trace` request-bound wallpaper export |
| Approved image sources | Search/download plus recorded source and digest provenance |
| Cloudflare Worker / D1 / Workflow / R2 | Hosted accounts, candidates, leases, approvals and artifacts |
| Workers AI | Hosted candidate generation only; its model is independent of Mac Codex planning |

## Invariants

- Canonical hosted account state remains siloed by account ID.
- A worker is a replaceable machine identity, never a committed person or Simulator UDID.
- An unhealthy worker cannot claim a new task; a stale or revoked worker cannot complete a lost lease.
- Model output cannot invoke Appium until it passes the deterministic `WallpaperPlan` checks.
- Generated images require native provenance and human review.
- A D1 execution barrier prevents lease expiry from reassigning work after Appium begins; a second
  D1 callback reservation binds result content and linearizes callback application against explicit
  revocation/replacement.
- Unknown external side effects are not retried automatically.
- No runtime path publishes to Threads or another marketing channel.
