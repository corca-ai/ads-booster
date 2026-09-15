# Production reconciliation repair review

Date: 2026-09-15
Scope: #186 candidate, including integration with PR #197.

The review follows invalid request → host plan → worker → saved result → user response.

1. Authority and compatibility: host derives only new obligation identity/source/revision. Existing
   canonical contracts remain unchanged; historical receipts decode unchanged. Denied tools and broken
   host schemas still fail. No production auth/config, MCP, network or filesystem scope is broadened.
2. Budget and effect integrity: invalid-input correction happens before dispatch and charges each model
   call. Consecutive invalid outputs stop at no-progress policy. Unknown image effects are never replayed.
   Existing updater drain/rollback controls remain unchanged, so restart overlap is not called a cause.
3. Concurrency: SQLite contention is handled only while claiming untouched pending ingress. Sink and
   receipt errors retain prior semantics. Empty polling avoids unnecessary writes. This does not explain
   the production competing writer or certify every worker against every operational error.
4. Diagnostics: only metadata/counters and allowlisted codes reach private JSON/journal/final Slack text.
   Provider text, command contents, image bytes and HTTP bodies/headers are excluded. Interrupted paths
   default to provider_interrupted, never completed. Atomic file replacement prevents partial readback.
5. Evidence risk: actual macOS Luna success refutes model-wide failure but does not prove Linux. The
   new native-tool instruction bridge is a compatibility improvement, not historical root proof.
   Final deployed image/file acceptance remains open and must stay visible in PR and issue.

Disposition: bounded fixes are reviewable for integration; do not claim the complete production
incident resolved until exact-SHA activation and a supported Slack image delivery are observed.
No unrelated user files or old diagnostic pointers are changed. No blocking code finding remains
in this scoped review; live verification is explicitly pending.
