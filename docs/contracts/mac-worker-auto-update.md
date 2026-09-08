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

## Managed release publication

This is the canonical operator procedure for Mac release publication. The
[release workflow](../../.github/workflows/release-mac-worker.yml) and its
[release policy](../../scripts/mac-release-policy.py) define the executable conditions:

- A package version change on `main` requests a new release. Shared package changes with an
  unchanged version still run the applicable Mac compatibility checks without publishing.
- An explicitly authorized workflow dispatch on `main` can publish or resume the current version,
  subject to the exact SHA and ownership checks. Pull Requests never publish.
- On-prem server updates track verified `main` SHAs independently; they do not require a Mac tag.
  See the [server operation guide](../operations/agent-server/slack-launch-guide.md).

When a new Mac version is required, update `pyproject.toml` and its lockfile in the work branch before
merging. The workflow builds and checks the release bytes, creates its owned annotated tag and Draft
Release, uploads the bundle, manifest and bootstrap, attests and verifies all three assets, then
publishes and reads back the stable release. Control-plane changes must pass the matching deployed
health check before publication. The stable-release signal below follows public readback.

Do not manually create or edit tags, release bodies or assets. The
[release-state guard](../../scripts/github-release-state.py) requires workflow ownership markers and
rejects conflicting or unowned state. Never move a published tag to a newer `main` SHA. Reuse an
existing version only when the workflow accepts its exact target SHA and owned state; otherwise
prepare a new version through the normal work-branch flow.

For an explicitly requested release or resume that needs a manual trigger:

```bash
gh workflow run release-mac-worker.yml --repo corca-ai/ads-booster --ref main
```

Identify the resulting run and verify its exact target rather than assuming the latest run belongs
to this request:

```bash
gh run list --repo corca-ai/ads-booster --workflow release-mac-worker.yml --branch main
gh run view <run-id> --repo corca-ai/ads-booster --json headSha,event,conclusion,url
gh release view v<version> --repo corca-ai/ads-booster --json tagName,targetCommitish,isDraft,isPrerelease,assets,url
git ls-remote --tags origin refs/tags/v<version> 'refs/tags/v<version>^{}'
```

Require the publication job's success, a stable non-Draft release with all three expected assets,
and the tag's peeled SHA matching that run's `headSha`. A compatibility-only run is not a release.
Follow the [verified bootstrap](../../README.md#bootstrap-a-verified-mac-worker-release) to verify
attestations and install. Confirm `trace-marketing version --json` and `trace-marketing worker doctor`
on the installed Mac before claiming activation; GitHub publication alone is not installed-product
proof.

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
