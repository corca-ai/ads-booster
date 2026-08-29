# Cloudflare Marketing Loop

Status: Active
Last reviewed: 2026-08-28

The hosted workspace owns candidate/review state, D1 worker registration and leases, callback
acceptance, and R2 image storage. Workers AI may create hosted candidates. A caption-approved
candidate creates `hosted_workspace_capture_v1` for an enrolled Mac.

```text
candidate approval -> D1 task/lease -> Mac callback -> R2/D1 image -> human review
```

The immutable task includes candidate ID/revision, account/workspace/run identity, approved context,
Trace items, and `background_intent`. The Mac acknowledges only after durable inbox insertion. It
records the D1 execution barrier immediately before native work. A pre-barrier lease can move; a
post-barrier task is never automatically sent to another worker.

The callback has stable identity. Cloudflare accepts an identical retry but rejects changed content
for that identity and validates candidate revision, PNG type/size/digest, native provenance, and
current D1 owner. The Mac outbox retries delivery only; it does not rerun Codex/Appium. Post-barrier
failure remains `unknown_side_effect`.

The image becomes `image_awaiting_review`; approval reaches `submitted`. PNG/manifest checks
prove request binding, not aesthetics. Human review is the only visual semantic approval. The runtime
does not auto-post to Threads, Notion, or another external channel and does not collect live metrics.

Use `trace-marketing worker run` or `trace-marketing worker install-service`. The migration-only
names `trace-agent` and `trace-ads`, including their historic plists, are not production paths.
