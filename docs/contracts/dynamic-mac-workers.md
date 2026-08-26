# Dynamic Mac Worker Contract

Status: Implemented; one real prepared-Mac canary remains an operational acceptance check.

## Problem

Hosted native capture currently gives every participating Mac the same Cloudflare Queue pull token
and callback token. The control plane cannot name, observe, drain, revoke, or deliberately replace
one machine, and the public workspace cannot explain whether a queued image has any healthy worker.

## Capability Contract

An operator can enroll any prepared Mac with a short-lived one-time code, see its sanitized health
in the workspace, drain or revoke it independently, and replace it without rotating credentials for
other workers. A healthy worker claims exactly one compatible hosted capture task through the
control plane, keeps the existing local inbox/outbox durability, and returns the same verified
Appium PNG callback accepted by the current R2 boundary.

## Current Slice

- D1-backed worker registry, one-time enrollment, worker-scoped credential hashes, heartbeat,
  drain/revoke, task claim/release, and expiring leases.
- A worker-broker client and `trace-marketing worker` CLI for enrollment, doctor, foreground run,
  status, and macOS LaunchAgent lifecycle.
- A sanitized public worker-status endpoint and workspace status surface.
- Legacy direct Queue pull remains available for non-hosted control-plane tasks and rollback. Hosted
  workspace capture uses the worker broker once a non-revoked machine identity exists; degraded or
  offline workers leave that task queued until a healthy claimant appears.

## Fixed Decisions

- A worker is a machine identity, not a member identity or fixed Simulator UDID.
- Worker administration remains behind the token-protected `/v1` boundary; the login-free `/api`
  surface exposes sanitized status only.
- Macs do not receive a Cloudflare Queue token in the new path. They receive one revocable,
  worker-scoped bearer credential.
- Worker credentials are stored separately with mode `0600`; portable config contains no secret.
- D1 owns task lease concurrency. Local SQLite continues to own durable execution and callback
  delivery after a task is accepted.
- A claim starts with a two-minute lease. Durable local acceptance extends it to fifteen minutes,
  and live worker heartbeats renew that window for at most one hour from the original claim so a
  synchronous Appium run cannot silently lose ownership or remain stuck forever.
- Existing candidate revision, callback ID, digest, native provenance, R2, and human approval gates
  remain authoritative.

## Probe Questions

- Whether the internal Trace debug build can expose a stable machine-readable version. Until proven,
  doctor reports installed/not-installed and bundle visibility without inventing a release value.
- The first prepared-Mac canary should record end-to-end capture duration so the one-hour execution
  cap can be tuned from observed data instead of guesswork.

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
- Offline and degraded workers receive no new task.
- A stale or revoked worker cannot complete a lease it no longer owns.
- A late duplicate callback cannot change an already verified candidate result.
- Fresh-installed CLI behavior, not a worktree-only invocation, is the product acceptance surface.

## Success Criteria

- A prepared Mac can enroll without a Cloudflare Queue token and appears online within 45 seconds.
- One worker can be drained or revoked without changing another worker's credential.
- Two workers racing for one task produce exactly one lease owner.
- An expired lease returns to the claimable queue and can be completed by a different healthy worker.
- Heartbeat renewal keeps an executing task owned by one live worker and stops after the one-hour cap.
- The public workspace explains no-worker, queued, assigned, degraded, and offline conditions without
  exposing credentials or detailed host inventory.
- A fresh-installed worker can install/start/stop its LaunchAgent without hand-writing a plist.

## Acceptance Checks

- `unit`: enrollment codes are one-time and expiring; credentials are stored only as hashes server
  side; lease state transitions reject stale owners.
- `integration`: two worker clients race for one hosted task, one wins, a retry after expiry moves to
  the second worker, and duplicate completion remains idempotent.
- `integration`: local inbox persists before broker acknowledgement and callback delivery survives a
  transient control-plane failure.
- `manual`: 320/375/414/768px workspace layouts show worker availability without horizontal overflow.
- `e2e`: a fresh-installed prepared Mac enrolls, runs one real hosted Appium capture, stores the
  verified PNG in R2, and reaches `submitted` after human image approval.

## Boundary Ownership

- `cloudflare/` owns registry, enrollment, heartbeat, leases, authorization, sanitized status, and
  hosted task assignment.
- `marketing/` owns portable worker config, credential storage, broker transport, local inbox/outbox,
  doctor, and native capture execution.
- `capture/` continues to own Simulator/Appium/Trace artifact safety and provenance.
- `web/static/` renders status and operator guidance but does not decide health or lease state.

## Canonical Artifact

This file is the implementation contract for issue #37. Update it when implementation changes a
fixed decision, success criterion, or acceptance boundary.

## Implemented Composition

- `cloudflare/migrations/0008_dynamic_mac_workers.sql` owns registry and lease persistence.
- `cloudflare/src/mac-workers.js` owns enrollment, token-hash auth, heartbeat, claim/ack, drain,
  revoke, callback ownership, and sanitized public status.
- `worker_broker.py`, `worker_doctor.py`, and `worker_launchd.py` own the installed Mac boundary.
- `trace-marketing worker` owns admin enrollment, target-Mac enrollment, doctor, foreground run,
  service lifecycle, inventory, drain, and revoke commands.
- The canonical workspace renders a compact sanitized status strip and continues candidate polling.
