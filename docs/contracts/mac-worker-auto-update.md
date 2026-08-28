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

```bash
trace-marketing worker update --dry-run
trace-marketing worker update --apply
trace-marketing worker updater-status
```

`com.corca.trace-agent` and `com.corca.trace-ads` are migration-only legacy plist labels outside
updater ownership.
