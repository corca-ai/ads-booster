# First Trace Marketing Run

Status: Active
Last reviewed: 2026-09-03

This runbook is the first-use contract after the PR is merged, released, and deployed. The integrated
product path is the hosted workspace plus one managed Mac worker. The on-premises Agent Service is a
separate canonical-run transition surface; its production tool-registry cutover is not required for
this first hosted run and must not be enabled as a second effect owner.

## Completion definition

The first run is complete only when all of these are observed:

1. the deployed health endpoint reports the expected release and migrations through `0041`;
2. one installed managed worker reports the same release and an eligible marketing capability set;
3. one account-scoped marketing-agent request reaches a terminal shadow decision;
4. one approved candidate reaches the existing native Appium capture owner;
5. the returned PNG and manifest are visible for human image and caption review;
6. with auto-publish still OFF, the candidate records the expected terminal cancellation;
7. after a separate explicit live-post authorization, one non-production profile publishes exactly
   once and returns an authoritative post ID and permalink readback.

Fake Graph, fake channel, source tests, `uv run`, a doctor result, or an Appium manifest alone do not
satisfy this completion definition.

## 1. Verify the release before installation

The merge commit must have a new annotated tag and GitHub Release. The release workflow must have
deployed the matching Cloudflare revision before publishing the stable worker manifest. Follow the
verified bootstrap in the repository README and then check:

```bash
trace-marketing version --json
trace-marketing service doctor
trace-marketing worker doctor
```

`service doctor` does not require Appium. `worker doctor` must report the Xcode, Simulator, Trace,
Appium/XCUITest, and official Codex CLI prerequisites that the image path actually needs.

## 2. Configure the hosted control plane

Apply D1 migrations in order through `0041`. Configure `CONTROL_PLANE_TOKEN` and an exact
`MARKETING_AGENT_MODEL`. Keep Threads optional configuration absent until the workspace and worker
path are healthy. Confirm the deployed `/health` response before enrolling a Mac.

Do not place control-plane, Threads, Slack, or Codex credentials in source, D1 request bodies, worker
tasks, screenshots, or proof artifacts.

## 3. Enroll the dedicated Mac

Run as the same macOS user whose official Codex CLI session and LaunchAgent will own the worker:

```bash
codex login status
gh auth status
trace-marketing worker create-enrollment --url https://workspace.borca.ai --name 'Trace Marketing Mac'
trace-marketing worker enroll --url https://workspace.borca.ai --code '<one-time-code>'
trace-marketing worker install-service
trace-marketing worker status
```

Open the hosted workspace and confirm the Mac is recent, ready, and advertises the capabilities
required by the requested task. Do not infer readiness from a running LaunchAgent alone.

## 4. Run the marketing-agent shadow path

In the hosted workspace, open **마케팅 에이전트** and provide:

- the exact product repository, path, and immutable ref;
- the feature or verified format to promote;
- the desired business outcome;
- the current control or existing successful post;
- product-truth and market-evidence scopes;
- an optional approved marketing-context snapshot.

Use **Codex에 준비 요청 복사** to prepare the immutable request from a session that can inspect the
named product source. Paste only the resulting `trace.feature-launch-run-request.v1` into
**검증된 실행 요청 JSON**, then choose **에이전트 실행 접수**. The selected workspace account must
match the request account. Enter the control-plane token only in the protected workspace control; it
must never be copied into the request.

If the run requests customer evidence, approve and freeze the relevant customer signals, then resume
the exact run once with that account-owned snapshot. Review the resulting strategy, creative plan,
and next experiment from their server-projected packets.

## 5. Exercise generation and Appium with publishing OFF

Use one known successful Threads format and two initial countries. Generate the candidates, approve
one candidate for native capture, and verify:

- the requested locale, language, persona, week, and to-do data;
- wallpaper provenance and the final PNG digest;
- no broken or mixed-language text;
- no editor chrome or unintended system UI;
- the caption and image review decisions bind the displayed versions.

Leave account auto-publish OFF. Approve the final image and confirm that the existing publication
owner records the OFF cancellation without calling Threads. This proves the integrated product path
without creating a public post.

## 6. Enable one authorized Threads canary

Only after the OFF-path proof passes, configure the Threads variables and secrets documented in the
README, connect one non-production profile through OAuth, make it the account default, and keep
auto-publish OFF until a human explicitly authorizes the canary. Then enable it for that bounded test,
approve one candidate, and record:

- candidate and approved artifact digests;
- scheduled slot and immutable publication identity;
- authoritative Threads post ID and permalink readback;
- at least one metric snapshot;
- no duplicate post after polling, worker restart, or request replay.

Turn auto-publish OFF again after the canary unless the team separately approves ongoing operation.
An ambiguous publication must remain `unknown_side_effect`; never retry it as a new post.

## 7. Deploy the Agent Service with OAuth

Place the service behind an HTTPS ingress on the chosen on-premises or cloud server. Register an
OAuth confidential client with a token-introspection endpoint, then inject its secret from the
server secret store:

```bash
export TRACE_MARKETING_OAUTH_INTROSPECTION_URL='https://identity.example/oauth/introspect'
export TRACE_MARKETING_OAUTH_CLIENT_ID='trace-marketing-agent'
export TRACE_MARKETING_OAUTH_CLIENT_SECRET='<secret-store-reference>'
export TRACE_MARKETING_OAUTH_AUDIENCE='trace-marketing-agent'
export TRACE_MARKETING_OAUTH_TENANT_CLAIM='workspace_id'
trace-marketing service run --model '<approved-codex-model>' --host 0.0.0.0 --port 8765
```

Open the HTTPS service URL, create an Appium-independent reasoning Run, and retain its
`/runs/<run-id>` URL across a restart. A provider outage must return retryable HTTP `503` while the
Run remains durable. Verify that an inactive token, wrong audience, or missing workspace claim is
rejected and that one workspace cannot read another workspace's Run. Until the production registry
cutover is separately completed, use the hosted workspace for candidate, Appium, and Threads effects.

## 8. Let the agent register supported tools

The hosted agent reads `GET /api/marketing-agent/tools` and calls
`POST /api/marketing-agent/tools/install` with a catalog capability such as `publish.threads`.
Installation does not grant effect authority. For Threads it returns `/api/threads/oauth/start`; the
agent initiates that flow and presents the authorization URL, the operator completes Meta consent,
and the verified callback activates the capability. The existing default-OFF and human-review gates
remain in force. `deliver.slack` may be registered in the same way but remains a non-executable
reference until a live Slack delivery adapter ships.
It reads `GET /api/marketing-agent/skills` to choose a coherent procedure. A skill becoming visible
does not make it executable: every listed blocker must be cleared by an active tool registration.

## External preparation owned by the operator

- Cloudflare account access, deployed Worker/D1/R2 resources, domain, and `CONTROL_PLANE_TOKEN`;
- an approved exact Codex model and a logged-in official Codex CLI session for the service user;
- dedicated Mac hardware, full Xcode, an available iPhone Simulator, Appium/XCUITest, and the Trace
  debug app build;
- one known-good successful format and its source evidence;
- two initial countries, their languages, personas, and reviewer-approved account mapping;
- a Meta developer app, Threads configuration/secrets, App Review scopes, and one non-production
  test profile; the agent performs registration and starts OAuth, while the operator performs the
  provider-side app setup and consent;
- a named human reviewer and explicit authorization for the one live Threads canary;
- Slack App creation, scopes/App Review, redirect URL, signing secret/bot token issuance, workspace
  admin installation consent, destination channel invitation, and member mapping. The agent should
  register and validate the adapter once that live owner exists; this PR deliberately cannot
  activate or send Slack because only fake channel contracts exist today.
