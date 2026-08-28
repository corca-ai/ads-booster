# Shared Mac Worker Auto-Update Contract

Status: Candidate until the first merge-triggered release and public readback succeed. Each version
becomes operational only after its draft artifacts pass workflow-bound attestation verification;
shared Macs remain independently enrolled consumers.

Last reviewed: 2026-08-28

Issue: [#45](https://github.com/corca-ai/ads-booster/issues/45)

## Goal

After one operator-drained bootstrap, a shared Mac keeps its installed `trace-marketing` worker on
the latest stable, provenance-verified GitHub Release without CI-to-SSH push. The update sequence is:

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

On 2026-08-27, the repository immutable-releases API returned `404 Not Found`; the repository's
runtime identity has write but not administration permission. Repository-level immutable releases
are therefore not a prerequisite. Safety instead comes from the existing GitHub Actions SLSA
provenance for every release artifact, bound during verification to this repository, the exact
release workflow, `refs/heads/main`, the manifest commit SHA, and a GitHub-hosted runner. The exact
tag/commit, three-asset envelope, GitHub and manifest digests, local digests, versioned staging and
installed receipt remain independent checks. No administrator token or repository setting is
needed on a Mac or in Actions.

## Fixed decisions

### Verified release envelope

Only GitHub's public `releases/latest` response is eligible. The updater fails closed unless:

- `draft=false` and `prerelease=false`;
- the tag is exactly `v<semver>` and equals the manifest tag;
- the manifest version equals the tag version and installed package version;
- the manifest records the exact 40-character release commit SHA;
- the Git tag resolves to that commit and `target_commitish` equals it;
- the release contains exactly the manifest, macOS arm64 bundle, and bootstrap assets;
- GitHub's asset `digest`, manifest metadata, and locally computed SHA-256 agree; and
- each downloaded asset has valid SLSA provenance from
  `corca-ai/ads-booster/.github/workflows/release-mac-worker.yml`, sourced from `refs/heads/main`
  at the manifest's exact commit SHA on a non-self-hosted runner; and
- platform is `macos-arm64` and Python is the supported `3.14` line.

`trace-marketing-release.json` uses `trace.marketing-release.v1` and names a
`trace-marketing-macos-arm64-v<version>.tar.gz` bundle. The bundle contains the project wheel and a
locked wheelhouse so the Mac installs with `--no-index`; update time never resolves mutable PyPI
content. The isolated virtual environment is created as relocatable before its completed directory
is promoted from staging, so installed console-script shebangs remain valid at the versioned path.

The release workflow runs its build, focused tests, and fresh offline install on pull requests with
read-only repository permission. The build backend and release tooling are pinned. On `main`, the
same check job builds and fresh-installs the envelope once, then transfers those exact three files to
the write-scoped publication job. That job creates an annotated tag bound to the exact merge SHA,
uploads all assets to a draft, attests them, verifies the exact envelope and all provenance before
publication, and then performs authenticated plus unauthenticated stable readback. The tag message
and release body carry exact version/SHA ownership markers.
A shared state resolver uses the authenticated, paginated release listing so workflow-owned drafts
are visible, distinguishes only HTTP 404 from tag-ref absence, classifies `new`, managed draft
`repair`, and exact stable `resume`, and retries transport or server errors. It removes only a marked
unpublished draft, including the lost-POST-response case. A published stable release is never
deleted or moved; full and failed-job reruns recover its published bytes and resume digest,
attestation, and public verification. The checked artifact ID crosses the job boundary, so a
failed-job rerun does not guess from its newer run-attempt number. The merge itself is the release
authorization—there is no version input or follow-up dispatch.

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
- Worker and updater plists contain only executable/state paths, the pinned official Codex, uv and
  GitHub CLI
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
3. operator downloads a specific versioned release and verifies all three artifacts with
   `gh attestation verify`, pinned to the repository, signer workflow, `main` ref and manifest
   commit SHA;
4. the release bootstrap stages the managed installation, installs both LaunchAgents, and starts
   them when enrollment already exists; and
5. after doctor and exact-version heartbeat succeed, bootstrap removes only strictly owned legacy
   plists. A fresh unenrolled Mac stops after verified product installation so the operator can
   consume the workspace's one-time code and run the displayed `finish-bootstrap` command.

The protected workspace manager emits one copyable block that resolves the latest stable tag,
downloads the manifest, bootstrap, and bundle, verifies all three workflow-bound attestations before
executing the bootstrap, enrolls the Mac without exposing the administrator token, and starts both
services. The block is one fail-fast Bash execution unit, so it cannot consume the enrollment code
after a failed lookup, install, or doctor. Nothing in the block hardcodes a worker ID or Simulator
UDID. CI never SSHes to or pre-registers a Mac.

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

- draft/prerelease releases, invalid or missing provenance, and changed tag/SHA/digests are refused;
- malicious archive paths and an unlocked/incomplete wheelhouse are refused;
- active inbox work, pending callbacks/approvals, and ambiguous execution markers defer without
  stopping the worker;
- a live guard blocks pulls while durable local work and callbacks continue;
- staging never changes `current`;
- symlink switch is atomic and last-known-good is retained until exact heartbeat success;
- injected stage doctor, launchd start/status, current doctor, and heartbeat failures roll back;
- rollback verification failure remains an explicit failed state;
- plist and state/log fixtures contain no administrator, enrollment, or Codex credentials; and
- a fresh isolated managed bootstrap creates `current` plus a release receipt and exposes the
  documented CLI; a separately enrolled Mac must survive a real reboot.

Merge completion proves the CI-owned GitHub Release and Cloudflare deployment surfaces. Each Mac is
an independently registered dynamic consumer. After registration, reboot readback showing both
LaunchAgents alive, the exact-version heartbeat, and one real Codex -> Appium -> callback canary are
that machine's operational acceptance; they are not a CI-to-SSH release step or a prerequisite for
registering another Mac.

## Deferred decisions

- A hosted update UI or self-drain control-plane API reopens only if operator bootstrap or local
  drain visibility proves insufficient.
- Automatic old-release garbage collection reopens after storage growth is measured.
- Intel macOS bundles reopen only when an enrolled Intel worker is required.
- Codex/Appium/Xcode/Trace application upgrades remain separately owned even if their versions later
  become doctor inputs.
