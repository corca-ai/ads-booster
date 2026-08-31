# Trace Marketing Pipeline

`ads-booster` runs the hosted Trace marketing workspace and its replaceable macOS capture worker.
The only installed command is `trace-marketing`. It uses the same macOS user's official Codex CLI
login to operate the Trace debug app through Appium. The Mac remains a request-bound image worker;
default-OFF Threads publishing and engagement polling run only in the hosted Cloudflare boundary.

## Current request path

```text
hosted candidate -> D1 lease -> durable inbox -> safe preparation -> local admission
-> D1 execution barrier -> one Codex/Appium job -> independent PNG/manifest validation
-> durable callback -> R2/D1 -> caption and image review
-> next-slot Threads decision -> Cloudflare publish barrier -> readback -> engagement polling
```

1. A teammate can request an automatic candidate batch. The hosted workspace writes an immutable
   `generate_candidates` task, and a compatible Mac runs one structured official Codex CLI turn to
   return drafts. The worker never restores a plan object or custom Agent runtime; Cloudflare stores
   the idempotent callback result for human candidate review. Repeated strong rejections from three
   distinct candidates can add a scoped caption rule to the task; the callback must return the
   selected feedback digest before Cloudflare accepts it.
2. An approved hosted candidate creates an immutable task with its marketing context, Trace items,
   candidate revision, and `background_intent`. An image retry also receives the exact preceding
   rejection for that candidate and any promoted image rules. This feedback input is additive;
   candidate fields, PNG/manifest output, R2 storage, and review-state transitions keep their
   existing contracts.
3. D1 leases it to a ready, enrolled Mac. The worker writes the task to its SQLite inbox before it
   acknowledges the lease.
4. Before capture side effects, the worker resolves an iPhone Simulator, validates locale/time zone, fetches
   the allowlisted background, records provenance and SHA-256, creates a private request directory,
   and checks Appium readiness.
5. It commits local admission, then records `execution_started` in D1. Codex cannot start when
   that barrier fails.
6. It starts exactly one ephemeral `codex exec` with user/project configuration disabled and the
   `trace-appium` permission profile. Model-generated commands can read and write only the request
   workspace and can reach only the loopback Appium endpoint; home credentials and external network
   destinations stay blocked. The non-secret
   `trace.codex-appium-job.v2` contract binds context, background, device, digest, nonce,
   locale/time zone, and request-owned calendar namespace. After the D1 barrier, the worker asks the
   DEBUG Trace EventKit helper to seed and verify those events. Codex then observes and operates only
   the real Trace UI, choosing its layout and settings without a prescribed click order. The worker
   removes only the recorded request calendar after collection.
   Before Save, Codex publishes the active Trace wallpaper editor state; the worker independently
   confirms the editor identity and requested titles, clears any earlier export, and only then
   acknowledges Save. This binds collection to the final Save generation rather than an earlier
   lifecycle export from the same request.
7. The worker independently verifies PNG size/SHA-256, request digest, nonce, bundle ID, Simulator
   UDID, dimensions, and `native_appium` provenance from the native manifest. Trace renders the
   same complete `wallpaperPreview` shown in its lock-screen settings screen, including the
   configured Trace content and lock-screen UI, into that bound PNG. The worker returns this native
   preview unchanged; it does not ask ImageGen to redraw iPhone UI or splice fixed image bands. It
   then queues the final callback durably and retries callback delivery without rerunning the job.
8. Cloudflare writes the accepted image to R2 and state to D1. Caption and image review remain
   mandatory. Final image approval reaches `submitted` and atomically records either a strictly-next
   morning/evening publication or a terminal OFF cancellation; manual-slot candidates are excluded.
9. When the account's default-OFF Threads setting is enabled, Cloudflare alone decrypts the selected
   profile token, checks quota, gives Meta a short-lived digest-bound PNG URL, rechecks the setting and
   profile at the irreversible D1 barrier, calls publish once, and requires post-ID readback before
   `published`. Ambiguous results become `unknown_side_effect` and are never blindly retried.
10. Confirmed posts collect bounded lifetime metrics and top-level replies independently of the
    auto-publish toggle. Reply bodies expire after 30 days and metric snapshots after 365 days; none
    of this data is fed back into candidate generation automatically.

A manifest proves request-bound native export, not visual or semantic fidelity. Human review is the
visual approval boundary.

The workspace's 생성 근거 panel shows whether feedback was selected and whether the Mac returned
the matching consumption receipt. This is transport provenance, not a claim that the generated
content followed every instruction. A token-authorized control-plane request can disable a promoted
rule while retaining its underlying review evidence.

## Bootstrap a verified Mac worker release

```bash
bash -euo pipefail <<'TRACE_MAC_BOOTSTRAP'
repository="corca-ai/ads-booster"
release="$(gh release view --repo "$repository" --json tagName,isDraft,isPrerelease \
  --jq 'select(.isDraft == false and .isPrerelease == false) | .tagName')"
release_dir="$(mktemp -d "${TMPDIR:-/tmp}/trace-marketing-bootstrap.XXXXXX")"
trap 'rm -rf -- "$release_dir"' EXIT
gh release download "$release" --repo "$repository" --dir "$release_dir" \
  --pattern trace-marketing-release.json --pattern trace-marketing-bootstrap.py
manifest="$release_dir/trace-marketing-release.json"
bootstrap="$release_dir/trace-marketing-bootstrap.py"
bundle_name="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["bundle"]["name"])' "$manifest")"
commit_sha="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["commit_sha"])' "$manifest")"
[[ "$bundle_name" =~ ^trace-marketing-macos-arm64-v[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz$ ]]
[[ "$commit_sha" =~ ^[0-9a-f]{40}$ ]]
gh release download "$release" --repo "$repository" --dir "$release_dir" --pattern "$bundle_name"
for asset in "$manifest" "$bootstrap" "$release_dir/$bundle_name"; do
  gh attestation verify "$asset" --repo "$repository" \
    --signer-workflow "$repository/.github/workflows/release-mac-worker.yml" \
    --source-ref refs/heads/main --source-digest "$commit_sha" --deny-self-hosted-runners
done
python3 "$bootstrap" --manifest "$manifest" --bundle "$release_dir/$bundle_name" \
  --uv "$(command -v uv)" --gh "$(command -v gh)"
export PATH="$HOME/.local/share/trace-marketing/current/bin:$PATH"
trace-marketing version --json
TRACE_MAC_BOOTSTRAP
```

Run this as the service-owning macOS user after `gh auth status`. It verifies release assets and
workflow provenance before it executes the downloaded bootstrap, then makes one versioned offline
wheelhouse install under `~/.local/share/trace-marketing/releases/<version>`.

## Mac prerequisites

Run as the same macOS user that owns the LaunchAgent:

```bash
codex login
codex login status
gh auth status
appium driver install xcuitest # only when missing
trace-marketing worker doctor
```

The Mac needs Xcode, an available iPhone Simulator, Appium with XCUITest, and Trace debug build
`com.corca.Trace`. `worker doctor` proves local prerequisites, not an enrolled or completed task.

## Enrollment and operation

```bash
trace-marketing worker create-enrollment --url https://workspace.borca.ai --name 'Studio Mac'
trace-marketing worker enroll --url https://workspace.borca.ai --code '...'
trace-marketing worker install-service
trace-marketing worker status
trace-marketing worker run --once
trace-marketing worker set-state --state draining
trace-marketing worker update --dry-run
trace-marketing worker updater-status
```

The administrator creates the enrollment code. The Mac stores its distinct revocable machine
credential under `~/.trace-agent`; the LaunchAgent pins the selected `codex` executable but stores
neither machine nor Codex credentials.

## Threads Cloudflare configuration

Config generation requires `THREADS_APP_ID`, `THREADS_REDIRECT_URI`,
`THREADS_GRAPH_API_VERSION`, and `THREADS_PUBLIC_ORIGIN` alongside `CF_D1_DATABASE_ID`. The redirect
and public origin must use HTTPS and the Graph version must be pinned as `vN.N`.

Store secret values only with Wrangler; do not put them in the generated config or repository:

```bash
cd cloudflare
wrangler secret put THREADS_APP_SECRET
wrangler secret put THREADS_TOKEN_ENCRYPTION_KEY
wrangler secret put THREADS_MEDIA_SIGNING_KEY
```

`THREADS_TOKEN_ENCRYPTION_KEY` is a versioned 256-bit AES key such as `v1:<base64>`. The media-signing
key is at least 32 random bytes. `CONTROL_PLANE_TOKEN` continues to protect OAuth start, profile
mutation, reply content, and unknown-outcome resolution. Deploy D1 migration `0016_hosted_threads.sql`
before enabling the feature, complete Meta App Review for the four documented scopes, connect a test
profile, keep auto-publish OFF, then run one explicitly authorized non-production post/readback and
engagement canary. Source or fake-Graph success is not live Meta proof.

## Managed releases and compatibility

Managed releases live under `~/.local/share/trace-marketing` and switch `current` atomically.
The default `~/.trace-agent` state home, including credentials, inbox/outbox, artifacts, and
legacy `codex-runs`, remains intact. An `executing` legacy marker without `result.json` only
makes the updater defer; it is read-only compatibility input and is never resumed or rewritten.

`com.corca.trace-agent` and `com.corca.trace-ads` are migration-only legacy plist names: inspect
and drain them separately. The current labels are
`com.corca.trace-marketing-worker` and `com.corca.trace-marketing-updater`.

## Proof boundaries

- A checkout or `uv run` proves source behavior, not a managed installation.
- A doctor report proves local prerequisites, not Cloudflare state or image output.
- PNG/manifest checks prove export bindings, not visual quality.
- Human image approval is the final creative gate. With Threads auto-publish ON it creates a frozen
  next-slot publication decision; only authoritative Cloudflare post-ID readback proves publication.

See [system architecture](docs/architecture/system.md),
[code architecture](docs/architecture/code.md), [dynamic workers](docs/contracts/dynamic-mac-workers.md),
and [testing](docs/development/testing.md).
