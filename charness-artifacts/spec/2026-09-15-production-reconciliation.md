# Production reconciliation repair

Status: Draft — implementation and deployed image proof pending.

Issue: #186. The production report separates invalid tool input, conflicting actor
obligations, and an incomplete image event stream. Do not treat them as one retryable failure.

The host preserves admitted obligations and derives identities/source references for new
actor additions. The provider wire format must not ask the model to copy or revise existing
host-owned requirements. Canonical validation remains mandatory.

Tool input must match the selected current descriptor before dispatch. Diagnose the exact
invalid shape without logging user values. Image progress and failure must distinguish
observed native events from local files and incomplete generation from successful delivery.
Do not relax the seven-call workflow or replay uncertain image operations to obtain a pass.

Proof requires failing-first provider/consumer regressions, a fresh non-editable install,
and a production Slack image receipt with six delivered files. Existing CI and deployed SHA
alone do not prove image generation. Preserve historical failed operations and user files.
