# Dynamic Mac Worker Contract

Status: Implemented baseline; one real prepared-Mac Codex-to-Appium canary remains operational acceptance.

Candidate rollout status (2026-08-27): the feedback provenance migration and the broker-only
zero-worker behavior described below are implemented on the current branch but are not deployed
product behavior until the D1 migration and Worker release are applied, read back from
`workspace.borca.ai`, and proven by the first real Mac canary.

## Problem

The legacy hosted capture path gave every participating Mac the same Cloudflare Queue pull token and
callback token. It could not name, observe, drain, revoke, or deliberately replace one machine, and
the public workspace could not explain whether a queued image had any healthy worker.

## Capability Contract

An operator can open a dedicated workspace manager with the control-plane token, enroll any prepared
Mac with a short-lived one-time code, inspect its protected health and task state, drain or revoke it
independently, and replace it without rotating credentials for other workers. The public status strip
continues to show only sanitized health. A healthy worker claims exactly one compatible hosted capture task through the
control plane, keeps the existing local inbox/outbox durability, and returns the same verified
Appium PNG callback accepted by the current R2 boundary.

## Current Slice

- D1-backed worker registry, one-time enrollment, worker-scoped credential hashes, heartbeat,
  drain/revoke, task claim/release, expiring pre-execution leases, and a non-reassignable Appium
  execution barrier.
- A worker-broker client and `trace-marketing worker` CLI for enrollment, doctor, foreground run,
  status, and macOS LaunchAgent lifecycle.
- A sanitized public worker-status endpoint and workspace status surface.
- A protected Mac connection manager in the hosted workspace for inventory, refresh, active/draining
  state, explicit two-step revocation, and one-time enrollment code plus target-Mac commands.
- Legacy direct Queue pull remains available only for non-hosted simulation compatibility and
  existing legacy task handling. Every new hosted workspace capture uses the worker broker. If no
  non-revoked machine identity exists, the request fails before task creation; when registered
  workers are merely degraded or offline, the task stays queued until a healthy claimant appears.
- The Mac process uses the official Codex CLI as its only model harness. Each claimed task starts an
  ephemeral, read-only planning turn and validates the structured `WallpaperPlan`. After the remote
  execution barrier, a separate ephemeral Codex job receives only the non-secret job contract and
  directly operates the installed Appium/XCUITest/Simulator/Trace surface. The worker, rather than a
  deterministic UI runner, owns the outer UDID lease, one-hour ceiling, export verification, and
  callback. The former in-package `trace-agent` model loop is not part of the Mac worker path.

## Fixed Decisions

- A worker is a machine identity, not a member identity or fixed Simulator UDID.
- Codex authentication is a user identity, not a worker credential. The person preparing a Mac runs
  `codex login` once as the same macOS user that owns the per-user LaunchAgent. The service inherits
  that user's normal Codex credential lookup and never copies auth data into worker state or plist.
- Task conversations are ephemeral. Macs share neither Codex thread history nor task-local context;
  replacing a Mac only requires a valid Codex login plus the existing worker enrollment.
- Worker administration remains behind the token-protected `/v1` boundary; the login-free `/api`
  surface exposes sanitized status only.
- The browser receives an admin token only from an operator input. It keeps the token in JavaScript
  memory for the open manager, never in markup, URL, cookie, `localStorage`, or `sessionStorage`, and
  clears it with displayed one-time codes when the manager closes or locks.
- Macs do not receive a Cloudflare Queue token in the new path. They receive one revocable,
  worker-scoped bearer credential.
- Worker credentials are stored separately with mode `0600`; portable config contains no secret.
- D1 owns task lease concurrency. Local SQLite continues to own durable execution and callback
  delivery after a task is accepted.
- A claim starts with a two-minute lease. Durable local acceptance extends it to fifteen minutes,
  and live worker heartbeats renew pre-side-effect work for at most one hour. Immediately before
  Appium, D1 records `execution_started_at` and removes automatic expiry. A validated callback then
  reserves its callback ID and result digest against the current worker/lease before R2 mutation.
  Explicit revocation releases only unreserved work and returns `409` while callback application is
  incomplete, preserving the worker credential for an identical durable retry.
- Existing candidate revision, callback ID, digest, native provenance, R2, and human approval gates
  remain authoritative.

## Probe Questions

- Whether the internal Trace debug build can expose a stable machine-readable version. Until proven,
  doctor reports installed/not-installed and bundle visibility without inventing a release value.
- The first prepared-Mac canary should record end-to-end capture duration and post-barrier age so
  operator alerts and disposition guidance can be tuned from observed data instead of guesswork.

## Deferred Decisions

- Physical iPhone support, geographic routing, autoscaling, and worker priority weights.
- Automatic installation or signing of the separate Trace iOS debug build.
- Public member authentication for worker administration.

## Non-Goals

- Threads publication or metrics readback.
- Replacing the existing Cloudflare Workflow task bridge for non-hosted tasks.
- Remote desktop control of a team Mac.

## Constraints

- Secrets never enter D1 plaintext, task payloads, ordinary logs, browser markup, or test output.
- The worker does not persist Codex prompts, responses, tokens, API keys, or auth-cache files. Only
  validated plans and execution outcomes enter request-scoped durable state.
- Offline and degraded workers receive no new task.
- A stale or revoked worker cannot complete a lease it no longer owns.
- A late duplicate callback cannot change an already verified candidate result.
- Fresh-installed CLI behavior, not a worktree-only invocation, is the product acceptance surface.

## Success Criteria

- A prepared Mac can enroll without a Cloudflare Queue token and appears online within 45 seconds.
- `trace-marketing worker doctor` reports both Codex CLI availability and authenticated status; an
  unauthenticated Mac stays degraded and cannot claim a task.
- One worker can be drained or revoked without changing another worker's credential.
- Two workers racing for one task produce exactly one lease owner.
- An expired lease that has not crossed the execution barrier returns to the claimable queue and can
  be completed by a different healthy worker.
- After `execution_started_at`, expiry never assigns the task to another Mac; explicit two-step
  revocation is the operator disposition that may release it.
- Heartbeat renewal extends only accepted pre-execution work and stops after the one-hour claim cap;
  post-barrier work has no automatic expiry.
- The public workspace explains no-worker, queued, assigned, degraded, and offline conditions without
  exposing credentials or detailed host inventory. A no-worker image request returns `503` and does
  not accumulate an undeliverable capture task.
- An operator can use the workspace UI to list, activate, drain, revoke, and prepare a replacement Mac
  without copying worker IDs into CLI commands; the target Mac still consumes the one-time code locally.
- A fresh-installed worker can install/start/stop its LaunchAgent without hand-writing a plist.
- The LaunchAgent invokes the exact Codex binary verified during service installation and resolves
  authentication as the same GUI user; no Mac-specific binary path is committed to source.

## Acceptance Checks

- `unit`: enrollment codes are one-time and expiring; credentials are stored only as hashes server
  side; lease state transitions reject stale owners.
- `unit`: planning uses stdin, the `WallpaperPlan` schema, and an ephemeral read-only turn; native
  execution uses a second schema-constrained ephemeral turn in a mode-0700 workspace with a
  mode-0600 non-secret contract, rejects invalid output, and never supplies auth material.
- `integration`: two worker clients race for one hosted task, one wins, a pre-execution retry after
  expiry moves to the second worker, a post-barrier expiry does not, and duplicate completion remains
  idempotent.
- `integration`: local inbox persists before broker acknowledgement and callback delivery survives a
  transient control-plane failure.
- `manual`: 320/375/414/768px workspace layouts show worker availability without horizontal overflow.
- `browser`: the Mac manager rejects a wrong token without leaving the public workspace, sends the
  bearer token only to protected `/v1` calls, renders inventory and health, performs active/draining
  and two-step revoke actions, creates/copies an enrollment command, and clears secrets on close.
- `e2e`: a fresh-installed prepared Mac enrolls, runs one real hosted Appium capture, stores the
  verified PNG in R2, and reaches `submitted` after human image approval.

## Boundary Ownership

- `cloudflare/` owns registry, enrollment, heartbeat, leases, authorization, sanitized status, and
  hosted task assignment.
- `marketing/` owns portable worker config, credential storage, broker transport, local inbox/outbox,
  doctor, and native capture execution.
- `capture/` continues to own Simulator/Appium/Trace artifact safety and provenance.
- `web/static/` renders sanitized status and the protected operator manager, keeps the admin token
  ephemeral, and invokes control-plane transitions but does not decide health or lease state.

## Canonical Artifact

This file is the implementation contract for issue #37. Update it when implementation changes a
fixed decision, success criterion, or acceptance boundary.

## Implemented Composition

- `cloudflare/migrations/0008_dynamic_mac_workers.sql` owns registry and lease persistence;
  `0009_worker_execution_barrier.sql` adds the post-Appium-start reassignment barrier;
  `0010_worker_callback_reservation.sql` atomically binds callback ID and normalized result digest to the current
  worker lease before R2 or candidate mutation; `0011_hosted_feedback_provenance.sql` adds reviewed
  revision and generation/rule provenance without placing credentials in candidate or feedback rows.
- `cloudflare/src/mac-workers.js` owns enrollment, token-hash auth, heartbeat, claim/ack, drain,
  revoke, callback ownership, and sanitized public status.
- `worker_broker.py`, `worker_doctor.py`, and `worker_launchd.py` own the installed Mac boundary;
  `providers/codex_cli.py` owns the official CLI process adapter and
  `connectors/trace/v1/codex_runtime.py` owns validated Trace planning and Appium handoff.
- `trace-marketing worker` owns admin enrollment, target-Mac enrollment, doctor, foreground run,
  service lifecycle, inventory, drain, and revoke commands.
- The canonical workspace renders a compact sanitized status strip plus a separately unlocked Mac
  manager. The manager consumes the existing protected APIs and never persists its admin token.
