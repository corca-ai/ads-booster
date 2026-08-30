# System Architecture

Status: Active
Last reviewed: 2026-08-30

## Runtime boundary

Cloudflare owns hosted candidates, D1 leases/callback acceptance, R2 storage, and review state. An
enrolled Mac owns local durability, admission, one official Codex CLI process, Appium side effects,
and native export validation. A fresh managed `trace-marketing` install and deployed workspace are
product evidence; a checkout is development evidence only.

```mermaid
flowchart LR
  Candidate[Hosted candidate] --> Lease[D1 lease]
  Lease --> Inbox[SQLite inbox]
  Inbox --> Prepare[Context background readiness]
  Prepare --> Admit[Local admission]
  Admit --> Barrier[D1 execution barrier]
  Barrier --> Codex[One codex exec]
  Codex --> Export[Trace PNG and manifest]
  Export --> Validate[Independent validation]
  Validate --> Callback[Durable callback]
  Callback --> Store[R2 and D1]
  Store --> Review[Human review]
```

## Request-time capture

1. The workspace may create a `hosted_workspace_generation_v1` task for automatic candidate drafts.
   A compatible Mac executes exactly one schema-constrained official Codex CLI turn and returns its
   result through the same durable callback path; it has no Agent loop or plan object.
2. The workspace creates a `hosted_workspace_capture_v1` task from an approved candidate.
3. A ready Mac claims the D1 lease and inserts the task in its local inbox before remote ack.
4. Safe capture preparation resolves a Simulator, validates country locale/time zone, prepares a
   provenance- and digest-verified `background_intent` image, creates a mode-0700 request root,
   and checks readiness. These failures have not started Appium.
5. Local SQLite records the immutable admission digest/nonce. The worker then records the D1
   barrier. If it cannot, it does not start native work.
6. The worker writes `trace.codex-appium-job.v2` and runs one ephemeral official `codex exec` with
   user/project configuration disabled and the `trace-appium` permission profile. Commands can use
   the request workspace and the allowlisted loopback Appium endpoint, but cannot read home secrets
   or reach external hosts. The contract supplies context, prepared background, device/UDID, Trace
   bundle, endpoint, locale/time zone, digest/nonce, and `trace-<request-id>` calendar namespace.
   Codex owns UI observation and navigation; the worker owns Simulator preparation and collection.
   Before Save, Codex publishes its active wallpaper-editor state. The worker independently checks
   the Trace editor identifier, every requested title, and the live Trace process arguments in the
   same Appium session. A bundle-only terminate/activate cycle loses the immutable export binding,
   so the worker rejects that session before Save. It checks the binding again at the saved marker,
   clears any earlier App Group export, and acknowledges Save only after those boundaries.
   Collection therefore cannot wait on or accept an export from an unbound process.
7. The worker independently requires the PNG and manifest SHA-256, request digest, nonce, bundle,
   UDID, dimensions, native export binding, and `native_appium` provenance to agree.
8. The worker sends that verified native PNG to the same official Codex CLI's ImageGen capability.
   ImageGen edits the lock-screen UI dynamically, preserves the Trace cards, and excludes Dynamic
   Island, notch, settings controls, and a device frame. The worker uses only the generated top and
   bottom UI bands and keeps the native middle/card pixels unchanged; the native source and edited
   result remain separate so provenance is not lost.
9. It commits a callback to the outbox. Callback delivery retries without rerunning Codex; Cloudflare
   stores accepted output in R2/D1 and opens human image review.

The v2 contract fixes non-secret input and completion bindings, not selectors or UI procedures.

## Failure, services, and compatibility

A pre-barrier failure is ordinary task failure. A post-barrier crash or exception is
`unknown_side_effect`; the worker never repeats potentially completed Trace work. Restart recovery
requeues interrupted safe work and leaves post-barrier ambiguity visible. Callback retries are
delivery-only.

`trace-marketing worker run` is the foreground worker. The managed labels are
`com.corca.trace-marketing-worker` and `com.corca.trace-marketing-updater`; they run in the same
`gui/<uid>` domain as the Codex login and pin its executable. `~/.trace-agent` remains the default
state home. The updater only reads legacy `codex-runs/<id>/executing` without `result.json` to
defer activation; it preserves that compatibility state across releases.

`com.corca.trace-agent` and `com.corca.trace-ads` are migration-only old plist labels, not current
service instructions. Native manifest validation does not prove visual semantics. Human review is
mandatory, and no external marketing channel is auto-published.
