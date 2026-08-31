# Cloudflare Marketing Loop

Status: Active
Last reviewed: 2026-08-31

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

The image becomes `image_awaiting_review`; approval reaches `submitted`. PNG/manifest checks prove
request binding, not aesthetics. Human review is the only visual semantic approval. The same D1
batch freezes the candidate revision, selected Threads profile, caption, image digest, timezone, and
strictly-next morning/evening slot. Manual slots create no publication. Auto-publish OFF records a
terminal `canceled/auto_publish_disabled` decision that ON never resurrects.

Cloudflare owns OAuth token encryption, quota preflight, signed private-R2 media delivery, the
container and publish state machine, the irreversible CAS barrier, authoritative post-ID readback,
and bounded engagement polling. OFF or disconnect before the barrier produces zero publish POSTs.
After the barrier an ambiguous response becomes `unknown_side_effect`; automatic publish retry and
caption/time matching are forbidden. Confirmed posts keep collecting metrics and top-level replies
while auto-publish is OFF. Replies expire after 30 days, metric snapshots after 365 days, and neither
is injected into generation. Notion and every channel other than Threads remain non-publishing.

Use `trace-marketing worker run` or `trace-marketing worker install-service`. The migration-only
names `trace-agent` and `trace-ads`, including their historic plists, are not production paths.
