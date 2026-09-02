# Mac Worker Automatic Update

Status: Active
Last reviewed: 2026-08-28

The updater manages versioned releases under `~/.local/share/trace-marketing`. It verifies the
release envelope, stages beside the current release, drains safely, switches `current`, and verifies
the new worker with `doctor` plus a version-matching heartbeat. It never updates Codex, Xcode,
Appium, XCUITest, the Simulator, or Trace.

Before activation it waits for no received/running/guarded inbox tasks, no pending callbacks, and no
legacy `codex-runs/<id>/executing` marker without `result.json`. This is read-only compatibility
inspection: the updater defers; it never changes, completes, or resumes an old run. Existing
`~/.trace-agent` credentials, inbox/outbox, artifacts, and compatibility files remain unchanged.

On failure the updater restores the prior `current` release and verifies the last known good worker.

## Immediate stable-release signal

When a release includes control-plane paths, the release workflow waits for the matching Cloudflare
deployment and exact health SHA before it makes the GitHub release public. After public manifest
readback, it writes `TRACE_MARKETING_RELEASE_VERSION` to the Cloudflare Worker as a secret binding.
This value is only a version wake-up signal. It contains no artifact URL, release digest, or
authority to install bytes.

On every authenticated heartbeat, the control plane returns `update_target_version` only when the
binding and reported worker version are strict `major.minor.patch` versions and the target is newer.
The worker then calls `launchctl kickstart` for the already-loaded
`com.corca.trace-marketing-updater` job. It never uses force-restart mode. The updater remains the
only component that fetches the release, verifies attestation, drains work, switches `current`, and
rolls back. Until activation changes the reported version, every 15-second heartbeat returns the
same target and attempts another non-forced kickstart. An updater that was already running is never
killed; the next heartbeat wakes it after that run exits.

Heartbeats run every 15 seconds, so an enrolled worker with this contract normally starts an update
within one heartbeat after the signal is written. The hourly LaunchAgent interval remains a fallback
for missed heartbeats. A worker installed before this contract does not understand the signal; run
`trace-marketing worker update --apply` once to install a release that does.

```bash
trace-marketing worker update --dry-run
trace-marketing worker update --apply
trace-marketing worker updater-status
```

`com.corca.trace-agent` and `com.corca.trace-ads` are migration-only legacy plist labels outside
updater ownership.
