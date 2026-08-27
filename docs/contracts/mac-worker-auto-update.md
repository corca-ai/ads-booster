# Shared Mac Worker Auto-Update Contract

Status: Draft — the MVP is implemented locally and focused verification is in progress. No release
has been published and no shared Mac has been changed under this contract.

Last reviewed: 2026-08-27

Issue: [#45](https://github.com/corca-ai/ads-booster/issues/45)

## Goal

After one operator-drained bootstrap, a shared Mac keeps its installed `trace-marketing` worker on
the latest stable, immutable GitHub Release without CI-to-SSH push. The update sequence is:

```text
verify release -> stage separate version -> request local drain -> wait for quiescence
-> stop worker -> atomically switch current -> start worker -> doctor + exact heartbeat
-> commit last-known-good, or atomically roll back and re-verify
```

The worker continues to use the same macOS user's official Codex CLI login. This updater never
installs or upgrades Codex CLI, Xcode, Appium, XCUITest, the Trace application, or their state.

## Baseline discovered before implementation

The previous public installer defaulted to mutable `main` and replaced an existing uv tool in place.
Stable release `v0.2.3` has no assets and GitHub reports it as mutable. The MVP replaces that
production path; the published `v0.2.3` surface itself remains unsuitable and is not retrofitted.

On 2026-08-27, the repository immutable-releases API returned `404 Not Found`, which GitHub uses
when immutable releases are not enabled. This is an explicit rollout blocker, not something the
workflow bypasses: a repository administrator must enable immutable releases before the approved
release run. The workflow rechecks the setting and fails before it creates a draft release.

## Fixed decisions

### Immutable release envelope

Only GitHub's public `releases/latest` response is eligible. The updater fails closed unless:

- `draft=false`, `prerelease=false`, and `immutable=true`;
- the tag is exactly `v<semver>` and equals the manifest tag;
- the manifest version equals the tag version and installed package version;
- the manifest records the exact 40-character release commit SHA;
- the Git tag resolves to that commit and `target_commitish` equals it;
- the release contains exactly the manifest, macOS arm64 bundle, and bootstrap assets;
- GitHub's asset `digest`, manifest metadata, and locally computed SHA-256 agree; and
- platform is `macos-arm64` and Python is the supported `3.14` line.

`trace-marketing-release.json` uses `trace.marketing-release.v1` and names a
`trace-marketing-macos-arm64-v<version>.tar.gz` bundle. The bundle contains the project wheel and a
locked wheelhouse so the Mac installs with `--no-index`; update time never resolves mutable PyPI
content.

The release workflow first checks `GET /repos/{owner}/{repo}/immutable-releases`. It refuses before
creating a draft when immutability is not enabled. All assets are uploaded to a draft before it is
published. An operator still approves tag/release creation.

### Managed install layout

The default root is `~/.local/share/trace-marketing`:

```text
releases/<version>/        complete immutable virtual environment and release receipt
staging/<attempt>/         candidate extraction and installation
current -> releases/<v>    atomically replaced symlink
update-state.json          non-secret current/LKG/attempt status
```

Enrollment config/credential, inbox/outboxes, generated images, `codex-runs`, logs, and heartbeat
receipt remain under `TRACE_AGENT_HOME`. Official Codex login remains under the user's existing
Codex home. Releases are never installed over one another, and the MVP does not garbage-collect old
releases automatically.

### Separate launchd ownership

- `com.corca.trace-marketing-worker` runs `current/bin/trace-marketing worker service` with
  `KeepAlive=true`.
- `com.corca.trace-marketing-updater` runs `current/bin/trace-marketing worker update --apply` with
  `RunAtLoad=true` and `StartInterval`; it is not `KeepAlive`.
- Worker and updater plists contain only executable/state paths, the pinned official Codex
  executable, and allowlisted non-secret runtime settings. They contain no enrollment credential,
  control-plane token, GitHub token, or Codex authentication material.

### Local drain and quiescence

The updater takes a non-blocking process lock, then writes a PID-bound, non-secret local drain guard.
While a live guard exists, the worker stops pulling new broker leases but continues to:

- execute already persisted `received`/`running` work;
- deliver callback outbox entries; and
- deliver any applicable approval outbox entries.

The updater does not stop launchd until all of these are true:

- no inbox row is `received` or `running`;
- no callback or approval outbox row is undelivered; and
- no `codex-runs/*/executing` marker exists without a terminal `result.json`.

If the deadline expires, the update is recorded as `deferred`, the worker stays running, and the
guard is removed. A stale guard whose PID is no longer alive does not block new work.

### Switch, verification, and rollback

Before drain, the candidate is extracted without following archive links, installed into staging
from its wheelhouse, and checked with its own `trace-marketing version` and `worker doctor` commands.
After quiescence, the updater:

1. stops only the ads-booster-owned worker LaunchAgent and confirms it is unloaded;
2. atomically replaces the `current` symlink on the same filesystem;
3. bootstraps the worker LaunchAgent;
4. requires `launchctl print` success;
5. runs the new installed `worker doctor`; and
6. waits for a post-switch heartbeat receipt whose successfully accepted payload carries the exact
   candidate version.

The worker writes that receipt only after the heartbeat HTTP request returns success, so it proves
the control plane accepted and stored the version sent by the worker. It contains no credential.

Any stop, start, launchd, doctor, or heartbeat failure stops the candidate, atomically restores the
previous last-known-good symlink, starts the old worker, and applies the same launchd/doctor/exact
heartbeat checks. A rollback verification failure is reported distinctly and never marked healthy.

### One-time bootstrap

Legacy-to-managed migration is intentionally operator-drained:

1. operator sets the existing worker to draining and proves it has no active task;
2. operator stops the legacy worker;
3. operator downloads a specific immutable release and verifies it with `gh release verify` and
   `gh release verify-asset`;
4. the release bootstrap stages the managed installation, installs both LaunchAgents, and starts
   them when enrollment already exists; and
5. after doctor and exact-version heartbeat succeed, bootstrap removes only strictly owned legacy
   plists. A fresh unenrolled Mac stops after verified product installation so the operator can
   enroll it and run the displayed `bootstrap-managed` completion command.

Bootstrap preserves the enrollment credential and runtime directories. It removes a legacy
`trace-agent`, `trace-ads`, or LaunchAgent only when the exact label and executable basename prove
ads-booster ownership. The MVP leaves unproven legacy files in place.

## CLI contract

- `trace-marketing version [--json]` — cheap installed provenance probe.
- `trace-marketing worker update --dry-run` — fetch and verify eligibility without staging/switching.
- `trace-marketing worker update --apply` — execute one bounded update attempt.
- `trace-marketing worker finish-bootstrap` — after verified install and enrollment, install both
  services and require doctor plus the exact-version accepted heartbeat.
- `trace-marketing worker install-updater` — install the separate updater LaunchAgent after managed
  bootstrap.
- `trace-marketing worker updater-status` — print machine-readable non-secret update/LKG state.
- `trace-marketing worker uninstall-updater` — unload and remove only the owned updater plist.

Commands never print bearer tokens or read/write the official Codex login.

## Failure injection and acceptance

Focused automated checks must prove:

- mutable/draft/prerelease/non-immutable releases and changed tag/SHA/digests are refused;
- malicious archive paths and an unlocked/incomplete wheelhouse are refused;
- active inbox work, pending callbacks/approvals, and ambiguous execution markers defer without
  stopping the worker;
- a live guard blocks pulls while durable local work and callbacks continue;
- staging never changes `current`;
- symlink switch is atomic and last-known-good is retained until exact heartbeat success;
- injected stage doctor, launchd start/status, current doctor, and heartbeat failures roll back;
- rollback verification failure remains an explicit failed state;
- plist and state/log fixtures contain no administrator, enrollment, or Codex credentials; and
- a fresh isolated managed install exposes the documented CLI and survives a simulated reboot.

Final production proof is separate from local acceptance. It requires a published immutable
release self-applied by the shared Mac, reboot readback showing both LaunchAgents alive, the new
version heartbeat, and one real Codex -> Appium -> callback canary.

## Deferred decisions

- A hosted update UI or self-drain control-plane API reopens only if operator bootstrap or local
  drain visibility proves insufficient.
- Automatic old-release garbage collection reopens after storage growth is measured.
- Intel macOS bundles reopen only when an enrolled Intel worker is required.
- Codex/Appium/Xcode/Trace application upgrades remain separately owned even if their versions later
  become doctor inputs.
