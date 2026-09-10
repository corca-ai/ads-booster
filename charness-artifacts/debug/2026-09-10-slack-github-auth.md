# Slack GitHub Authentication Debug Review
Date: 2026-09-10

## Problem
Slack reports: "현재 capability_snapshot에 GitHub 이슈 생성 도구가 없어 이슈를 직접 올릴 수 없습니다."

## Correct Behavior
Given a service-owned GitHub credential, a shared Slack issue request must reach exact-payload approval and return a confirmed issue URL after creation/readback. No authentication must not imply write capability.

## Observed Facts
The source already implements fixed-repository creation, approval and readback. Service composition calls token_from_env(os.environ), but the resolver only read a dedicated file. Local default CLI is obsolete; SSH timed out, so neither establishes production state. Prior latest.md concerns D1 migration and is unrelated; preserve that user-owned file.

## Reproduction
The existing signed Slack test, changed to resolve fixture GH_TOKEN instead of injecting a token directly, failed before repair: COMPLETED instead of AWAITING_APPROVAL. No GitHub request occurred.

## Candidate Causes
- Missing dedicated token despite service environment/CLI authentication: reproduced resolver gap.
- Old service release: possible on the reported host, unproven.
- Private-DM policy exclusion: intentional; actual incident channel unknown.

## Hypothesis
Resolve service authentication before registration; the identical signed Slack fixture should enter approval, POST once, GET once and return its confirmed URL. The pre-fix failure disconfirms that existing token-file support already covers environment authentication.

## Pattern Ladder
Observed missing capability follows absent file -> None -> skipped registration -> no model tool. Tests injected config credentials, bypassing the producer. Slack/Notion also condition registration on credentials, but already consume service environment directly. No generalized missing-credential bug established beyond GitHub.

## Verification
Initial source selection: 28 passed. Final installed wheel outside checkout with lockfile dependencies: 31 passed, including PATH hardening. Changed-file type checks and formatting passed; scoped Ruff excludes pre-existing EM101/D107 findings. No actual GitHub write, real model or live Slack verification claimed.

## Root Cause
Credential-source coverage was narrower than service authentication: a dedicated file was treated as the only way to enable the tool. The old tests bypassed startup credential resolution.

## Invariant Proof
- Invariant: when startup resolves a credential, the Slack consumer must receive the registered issue capability and still require exact payload approval.
- Producer Proof: file/environment/CLI precedence tests and disabled/missing/invalid input controls.
- Final-Consumer Proof: signed Slack fixture approval, POST/GET receipt, URL rendering and duplicate suppression against a fresh wheel.
- Interface-Shape Sibling Scan: Slack/Notion configuration and CLI composition inspected; no duplicate GitHub resolver.
- Non-Claims: deployed credentials, model decisions and actual provider write permission remain unverified.

## Detection Gap
The signed Slack fixture injected AgentServiceIntegrationConfig.github_token directly. Replacing that injection with startup resolution makes the old implementation fail. CI already selects these test directories.

## Sibling Search
- Mental model: presence of an implementation and injected credentials proves operator discoverability.
- Same layer: GitHub credential resolver; decision: same bug, fix now; proof: local payload proof.
- Abstraction up / cross-file: bootstrap/integrations.py and cli/marketing.py; decision: same class, diagnostic-only for this slice; proof: static scan only. Existing composition already passes environment; no second resolver needed.
- Specialization down: default dangling symlink and missing PATH; decision: same bug, fix now; proof: regression tests.
- Mental-model sibling: updater clone access versus issue write permission; decision: intentional plain-text or non-rendering boundary; proof: docs/static scan. Clone access does not prove write access.

## Seam Risk
- Interrupt ID: slack-github-auth
- Risk Class: none
- Seam: bounded credential-source contract; live host state unknown.
- Disproving Observation: source regression, no host-disproves-local evidence.
- What Local Reasoning Cannot Prove: reported server authentication or actual Slack delivery.
- Generalization Pressure: none

## Interrupt Decision
- Resolution: resolved
- Critique Required: yes
- Next Step: PR
- Handoff Artifact: GitHub issue #161 and this PR's code/doc diff.

## Prevention
Two independent read-only reviews found no blocking approval/security or service-composition defect. Bundle: reject empty PATH lookup and document service PATH. Over-worry: no extra updater wiring or CI list needed. Valid but defer: live activation/permission checks remain explicit non-claims; issue #161 stays open pending delivery. Preserve file precedence, explicit disable, fixed host/repository and no blind mutation retries.
