# Dynamic Mac Workers

Status: Active
Last reviewed: 2026-08-31

An enrolled Mac has a revocable worker identity and private local credential. It has no Cloudflare
account token, Threads OAuth token, publishing capability, fixed UDID, or another person's Codex
login. Threads publication and engagement remain Cloudflare-owned and never expand the worker task
kinds, inbox, Appium contract, or callback payload.

```text
heartbeat -> D1 claim -> SQLite inbox -> safe prepare -> local admission
          -> D1 execution_started -> one Codex/Appium job -> callback outbox
```

D1 may move a lease before `execution_started`. After that barrier, the original worker owns the
ambiguous side effect until callback or operator action.

Safe preparation validates the task, resolves a Simulator, checks readiness, and retrieves the
hosted `background_intent` with digest/provenance. Admission persists job digest, export nonce, and
workspace identity before D1 barrier. The one v2 job gives Codex context, background, device,
digest/nonce, locale/time zone, and request-owned calendar namespace, but no execution plan.
After the D1 barrier, the worker runs the DEBUG Trace EventKit helper to seed and verify the exact
request-owned calendar before Codex starts.
The `trace-appium` permission profile restricts commands to the request workspace and allowlisted
loopback Appium. It blocks home credential reads and external hosts. The worker, outside that
sandbox, owns Simulator readiness, Calendar seed/cleanup, background import, and App Group
collection. Codex owns only Trace layout, settings, and Save.

PNG and native manifest must independently bind request digest, nonce, bundle, Simulator,
dimensions, SHA-256, and native provenance. Callback retries only redeliver. A post-barrier crash or
validation failure becomes `unknown_side_effect`, never a new automatic capture. Restart requeues
only safe interrupted work.

```bash
trace-marketing worker doctor
trace-marketing worker enroll --url https://workspace.borca.ai --code '...'
trace-marketing worker install-service
trace-marketing worker set-state --state draining
trace-marketing worker revoke
```

Current labels are `com.corca.trace-marketing-worker` and
`com.corca.trace-marketing-updater`. `com.corca.trace-agent` and `com.corca.trace-ads` are
migration-only legacy plist names; current commands do not manage them.
