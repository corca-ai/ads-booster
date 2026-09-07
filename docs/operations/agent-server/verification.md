# Candidate verification — 2026-09-07

This is local candidate evidence, not an installed on-premises production claim.

- Initial onboarding: 63 focused tests passed: agent_service, channels, Codex reasoning adapter and CLI compatibility.
- Changed Python source/tests passed Ruff, formatter and BasedPyright (zero errors/warnings).
- Isolated wheel installation under /private/tmp/trace-agent-onboarding-venv exposes the installed
  version and service doctor commands; reasoning prerequisites were ready without Appium.
- Browser QA against the installed package with a fixture reasoning provider exercised creation,
  a question, input submission to the same Run, and completed result display. No console errors.
- Real Codex CLI 0.153.4 with the logged-in ChatGPT session rejected the old gpt-5.4 example.
- The currently configured gpt-6-astra initially exposed a provider schema rejection:
  "object schema missing properties". A failing regression was added before implementing
  the strict tool_input_json provider projection.
- After reinstalling the corrected wheel, the real service resumed the original interrupted Run
  and persisted a Korean request for target-customer input.
- A second real installed service Run invoked research.search once for a generic public query,
  persisted its no-effect receipt and source output digest, then completed with URLs and an
  explicit unverified-snippet caveat. No Slack or other publication tool was invoked.
- Before Tunnel setup the hostname did not resolve. Later on 2026-09-07, Cloudflare UI confirmed
  the marketing-agent-onprem route and marketing-agent.borca.ai DNS were saved. The connector was
  inactive then; the server subsequently reported Ubuntu 22.04 x86_64 and an unrelated running
  cloudflared-ear.service, which must remain untouched.

## Slack-only and automatic-main-update candidate

- 77 focused tests passed: the onboarding owners above plus tests/cli/test_agent_server_update.py.
- Added real SQLite transaction tests for passive-start failure rollback, interruption recovery,
  post-commit no-rewind, busy-work no-stop, CI rejection and incompatible-main quarantine.
- Maintenance HTTP tests prove existing work drains while new work is refused. Signed Slack requests
  work without OAuth; forged signatures fail and web/bearer routes stay closed. Exact invocation
  review is available inside Slack and bad review pages are rejected.
- The standalone installer must parse under Python 3.10 (Ubuntu 22.04 system Python). A regression
  prevents the Python 3.14 formatter from removing required exception tuple parentheses.
- Final wheel installed with agent-manager.py in a fresh managed root under
  /private/tmp/trace-agent-autoupdate-final-installed; installed doctor found Codex without Appium.
- A real installed service process with fixture Slack configuration passed passive SHA/active health,
  maintenance 503, web/API 404, forged-signature 403 and correctly signed /trace help 200.
  This made no external Slack sends or provider calls. The test server was stopped afterward.
- Changed Python paths passed scoped Ruff, formatter and BasedPyright checks.

Still required: actual Ubuntu systemd/linger and dedicated marketing Tunnel connection; real Slack
installation/request/approval/result roundtrip and daily delivery; this branch merged to main with
successful dedicated CI; a live change of installed SHA observed through the timer; reboot behavior.
Local tests substitute systemctl and health for update recovery and are not live Linux rollout proof.
The supplied manager source and wheel are unreleased candidates. The public v0.4.21 does not contain
these changes. At that checkpoint the dedicated CI workflow had not run remotely yet; see the later results below.
Company OAuth is not required for Slack-only deployment. Optional Cloudflare email-login web
integration remains unimplemented and is not a requirement for the Slack-only completion claim.

## Conversational Slack candidate

- 96 focused tests passed across agent_service, channels, Codex reasoning, CLI compatibility and
  updater recovery. The 19 new conversation cases cover mention/thread continuation, same-Run input,
  queued close/reopen, signature/app/team checks, private history/tool/member separation, current-hash
  and reviewer approval, removed-user suppression, ambiguous sends and interrupted-plan recovery.
- All 29 changed Python source/test/operator files passed scoped Ruff, formatter and BasedPyright.
- Fresh wheel installed by agent-manager.py under /private/tmp/trace-agent-conversation-installed.
  The installed CLI served signed Events URL verification, rejected forged signatures, refused Events
  during maintenance, and kept web/API routes closed without a company IdP. No external sends.
- A separate proof using that installed package (not worktree imports) exercised mention admission,
  same-thread follow-up after adapter restart, duplicate suppression and isolated DM, with fixture
  reasoning and Slack sender. Three reasoning requests and six routed acknowledgement/answer payloads.
- Bootstrap/full manifests and the launch guide cover app creation before server startup, followed
  by Event URL verification and permissions installation. The updater probe now requires Events support.
- At the initial packaging checkpoint, GitHub readback found no PR for this branch. The user later
  authorized commit/push and PR creation. Consult the branch PR for publication and current CI status.

Still unverified: a real Corca Slack mention/thread/DM/reviewer roundtrip, actual Ubuntu systemd and
Cloudflare connector health, Linux reboot, and a real main SHA transition.
The earlier real Codex/search evidence above predates the conversation adapter and does not establish
live Slack completion. Ceal's file access, workspace search, buttons, streaming and AI sidebar are not
implemented by this candidate. Private DM tools are deliberately limited to public search.

## Independent server admission and Mac release policy

- PR #132's first Ubuntu run passed 96 tests and fresh-wheel installation. Its Mac job failed
  before functional tests because unchanged v0.4.21 collided with an existing release.
- Added failing regressions before changing admission: unrelated failed/pending Mac checks must
  not stop server candidate protocol validation. The required agent check still rejects missing,
  wrong-app, failed, skipped and incomplete results. Tool-adapter contracts now run in the server CI.
- The expanded server CI selection passed 115 tests locally. Mac policy/builder/release-state
  selection passed 20 tests. Policy tests execute the workflow identity shell: an unchanged version
  bypasses a conflicting release fixture; version changes and explicit dispatch retain the guard.
- All five changed Python files passed scoped Ruff, formatter and BasedPyright (zero errors/warnings).
- The updated manager installed the previously validated conversation wheel into a new isolated root
  `/private/tmp/trace-agent-ci-separated-installed`; installed version and service doctor passed.
  Application source and wheel are unchanged; manager/CI/operating guidance changed.
- Use `trace-agent-server-setup-20260907-ci-separated.zip` for initial installation so the bootstrap
  manager already uses the independent server CI gate. The older ZIP contains the old gate.
- Remote checks for the final PR head are recorded in GitHub, not inferred from local results.
  Actual Ubuntu lifecycle, Slack acceptance and a live main SHA transition still require operator QA.

## Repository installer and installed server CLI candidate

- Linux `install-server.sh` replaces manual ZIP transfer for new installations after merge.
  `agent-manager.py bootstrap` uses the exact-main CI and installed-protocol gate before selecting
  current. It refuses an existing managed installation; CLI collisions are preserved.
- `trace-marketing server` provides manifest, setup, start, stop, status, doctor and update.
  Setup requires an interactive terminal before reading secrets, validates Slack auth.test,
  writes 600-permission configuration, discovers tool PATHs and creates a dedicated optional
  token-file Cloudflare connector. It preserves existing settings and unrelated/unowned services.
- 128 selected server/channel/provider/CLI/updater/tool-adapter tests passed locally.
  Six changed Python files passed Ruff, formatter and BasedPyright; installer bash syntax passed.
- The final wheel installed into `/private/tmp/trace-server-onboarding-final-installed`.
  Installed CLI manifest export used wheel-packaged resources. A proof importing that installed
  package exercised setup, domain substitution, private permissions and dedicated token-file units
  with fixture Slack identity/systemd; no real credentials, Slack sends or service activation.
- The public URL is not a verified install path until this change is merged and executed from the
  actual remote ref in a fresh Linux environment. Actual Ubuntu systemd/linger, Cloudflare route,
  Corca Slack roundtrips and live main SHA transitions remain operator acceptance work.
- Mac direct on-prem enrollment/lifecycle migration is outside this installer change and remains
  unimplemented. Existing Mac worker commands and compatibility workflow remain in place.
