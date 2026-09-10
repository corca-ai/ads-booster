# Adaptive marketing skills: comparison and implementation

Status: Active
Research date: 2026-09-09
Owning issue: [#152](https://github.com/corca-ai/ads-booster/issues/152)

## Problem and decision

Trace already owns a source-bound skill repository, protected built-ins, experience learning,
current-source checks, canonical tool receipts and a live capability registry. Replacing these
with a second runtime would lose useful boundaries. The immediate gap is using the existing
pieces together: the prompt sends discovery to built-ins, catalog reads are unbounded, and the
prepared catalog is rejected as a whole when it exceeds context capacity.

This change makes reusable procedures easier to find, learn and use. It does not add arbitrary
Python/shell execution, external plugin installation or a new agent runtime.

## Primary sources and observed behavior

| Source | Useful structure | Application to Trace | Evidence limit |
| --- | --- | --- | --- |
| [Hermes skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/) | Compact discovery, on-demand bodies, agent-maintained procedural memory | Search/read exact revisions and guide grounded creation/update | Documentation describes upstream behavior; Hermes was not installed or benchmarked here |
| [Hermes tool reference](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/tools-reference.md) | Separate skill management and actual execution tools | A reusable procedure composes existing capabilities; saving it does not create an executor | Moving main documentation, not a pinned release comparison |
| [OpenClaw skills](https://docs.openclaw.ai/tools/skills) | Scoped catalogs, precedence, immutable revisions, environment dependency checks | Prefer effective scoped revisions; report current snapshot gaps and retain protected fallback | Descriptive docs, not a live OpenClaw runtime test |
| [OpenClaw Skill Workshop](https://docs.openclaw.ai/tools/skill-workshop) | Proposal/apply separation, target-hash binding, rollback and rooted review | Retain Trace's existing source/CAS owner; use these criteria for executable extension design | Page explicitly says “Status: proposal”; do not claim released support |
| [Ceal Slack discussion](https://corcaai.slack.com/archives/C09EGR5P422/p1782356872004939) | User corrections to reusable guidance; explicit entry versus scoped continuation | Keep latest-request and private-session boundaries; no global workflow lock from a keyword | Observed conversation and bot self-report only; no Ceal implementation or write readback |
| Installed `ceal --help` and `capabilities --help` | Discover actual contracts; effect, evidence, result schema and recovery; bounded targets | Current capability snapshot governs dispatch; missing prerequisites are not permission | Live discovery failed `session_renewal_unavailable`; no live Gateway capability claim |

Upstream reports also inform the failure cases: [Hermes #74249](https://github.com/NousResearch/hermes-agent/issues/74249)
reports authoring guidance when its tool is absent, and [#20273](https://github.com/NousResearch/hermes-agent/issues/20273)
reports background edits to bundled skills. These are issue reports, not independently reproduced
upstream defects. Trace's prompt gating and protected publication checks address the analogous
boundaries without copying those implementations.

## Acceptance and implemented behavior

1. `skills.list` and scoped `skill_list` accept bounded Unicode keyword queries and offset pages.
   Exact IDs rank first; metadata token overlap follows; ties keep catalog order. No procedure
   body enters search results. These are lexical results, not semantic retrieval or quality scores.
2. Scoped `skill_list` includes current built-ins, valid overrides and learned revisions. The
   provider prefers that catalog and reads the returned exact revision. The built-in-only route
   remains usable when knowledge is disabled or unavailable.
3. Context selection ranks the current request before spending a separate skill share, capped at
   2,400 conservative byte-based token-budget units and half of remaining capacity. Independent
   metadata records fit individually; a large catalog no longer eliminates every skill. Required
   evidence retains its existing group semantics and budget priority.
4. Metadata reports required capabilities absent from the filtered snapshot. This is a current
   availability gap, not proof of missing installation. Reading the procedure grants nothing.
5. `marketing.skill_learning` describes grounded drafts, applicability, observed pitfalls,
   verification, duplicate avoidance, CAS updates and readback. The prompt advertises writes
   only with `skill_apply`. Private scope filters every knowledge tool through the canonical
   read allowlist; it no longer misses `skill_apply` because of its different prefix.
6. Existing canonical history, source invalidation, protected overrides, receipts, approval and
   exact-dispatch checks remain the owners. No extra agent or hidden execution path is added.

## Improvement passes

- Initial change: bounded discovery and exact-revision retrieval for the existing two catalogs.
  The added built-in query test failed before implementation because query/limit were rejected.
- Additional pass 1: the context regression selected the first built-in instead of the requested
  learned skill. Inspection also found all-or-nothing catalog budgeting. Rank before admission,
  admit independent metadata records, reserve evidence capacity and report capability gaps.
- Additional pass 2: align provider instructions with available scoped tools and add the reusable
  authoring procedure. The private-ingress regression exposed `skill_apply` in a DM snapshot;
  fix the owner to use enum membership instead of tool-name prefixes. The publication layer
  already rejected private writes; this closes misleading planner exposure.

See [testing](../development/testing.md) for focused and installed checks. Local unit tests,
installed fixture checks, actual model behavior and live deployed behavior are distinct evidence.

## Executable tool creation: next boundary

Status: Draft — not implemented by this PR.

A model can now discover and author procedures that compose existing tools. It still cannot
install a new executable tool from a chat. The next independently reviewable increment should
support a generated tool bundle with input/output schemas, exact artifact digest, declared
effects, required capabilities, fixtures and review state. Use the existing artifact root and
canonical registration bundle; keep the official Codex reasoning path.

Before activation, run examples in a worker with explicit filesystem, network, secret and cost
limits. Human review must bind the exact bundle digest. Activation must atomically expose the
descriptor and its matching adapter, and survive restart without changing an admitted invocation's
executor identity. Revocation/rollback must remove future availability while preserving receipts
and historical artifacts. Test unknown side-effect recovery before allowing external writes.

A skill proposal, a code artifact, sandbox verification, reviewed activation and actual execution
must have different states. Generated code cannot grant itself permissions by naming a capability.
This is the next tool-creation acceptance contract, not a claim that Hermes, OpenClaw and Ceal
have been fully merged or that unattended self-modification is ready.

## Verification record (before channel-memory integration)

Final local wheel: `0.6.0`, SHA-256
`aaed4e4eeffe29064d00e40252cb5425ddc83bcd0d2796bd6114fa97482613dd`.
All eight changed production Python files matched the fresh non-editable installation byte for
byte. Installed `trace-marketing service run --help` succeeded. PATH's older installation was
observed separately and was not updated.

The final focused source selection passed 43 tests; scoped Ruff and BasedPyright passed. The
real-model minimal reuse canary passed 11 checks, using `gpt-6-astra` and four foreground calls.
It verified a source-bound write, unchanged head after restart, exact U2 revision/source selection,
`skill_get` receipt bindings and the exact synthetic response. The U1 write-only fixture correctly
reported that readback was unavailable there; U2 performed the read. No background model,
research or real Slack calls occurred. The initial candidate passed the same bounded scenario.

Final model evidence is retained locally at
`/private/tmp/trace-adaptive-152/final-model-reuse.json`, SHA-256
`dc0f3fd457bdfd3bcdc5677a51199fa202bd82b45d2b13ef0aafddca7aed2083`.
These paths are local evidence, not remotely published artifacts or CI results.

The broader installed fixture initially failed its new-thread selection assertion: its generic
“launch rule” query had no metadata overlap with its “Attempt to shadow an installed builtin”
fixture. Previously it relied on automatic inclusion of every skill. Its exact-provenance scenario
now requests `learned.fixture-receipt-procedure` explicitly; revision/source assertions are retained.
This fixture correction does not prove semantic retrieval. Early attempts also encountered its
working-directory guard and sandbox loopback restrictions; neither was treated as product failure.

The corrected final installed fixture passed all six cases: source-linked learning, restart/new
member, shared/private isolation, built-in protection, conflict/unknown-send recovery and normal
learning silence. Evidence: `/private/tmp/trace-adaptive-152/fixture-exact.json`. It uses fixed
provider output and captured Slack sends, separately from the real-model canary above.

## Current scope after main integration

Main 0.7.0 admits Slack source evidence at channel scope. Such evidence can support
channel-owned learning memory, but cannot publish workspace-wide skills. Workspace skill authoring
requires workspace-scoped authenticated API ingress. Older Slack skill-writing fixture/model
results above describe the preceding 0.6.0 authority contract, not current Slack permissions.
See [marketing colleague](marketing-colleague.md) and PR #153 for integration verification.
