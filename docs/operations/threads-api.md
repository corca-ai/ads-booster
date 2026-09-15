# Threads API operations

Status: Draft

The optional integration uses `https://graph.threads.net` and the official Threads OAuth flow. This
source implementation is not a claim that a Meta app has passed review, that a production account is
connected, or that current provider limits have been accepted on the installed service.

## Capability map

| Trace capability | Provider operation | Candidate scope |
| --- | --- | --- |
| connect and refresh | authorization code, long-lived exchange, token debug and refresh | `threads_basic` plus requested feature scopes |
| own posts and post read | `/me/threads`, `/{post-id}` | `threads_basic` |
| keyword search | `/keyword_search` with `TOP` or `RECENT` | `threads_keyword_search` |
| replies and conversation | `/{post-id}/replies`, `/{post-id}/conversation` | `threads_read_replies` |
| carousel publish and reply | IMAGE children, CAROUSEL or TEXT container, `/me/threads_publish` | `threads_content_publish` |
| post and account insights | `/{post-id}/insights`, `/me/threads_insights` | `threads_manage_insights` |

Granted scopes are read from the provider and stored per connection. Dispatch checks the required
scope and current token expiry; a missing scope, revoked account or expired credential is unavailable,
not an empty result. Refresh and reconnect preserve the same workspace owner. The same provider
account cannot be silently transferred to another member.

## Publication safety

The owner approves one exact publication invocation containing the draft revision and all selected
item IDs. That single approval covers the batch; no second draft-state approval is required. Only
then can the service issue short-lived HMAC media
URLs for the referenced asset IDs, revisions and digests. A publication receipt binds the canonical
tool invocation, connection, batch, item, draft revision, ordered asset digests, reply target,
container IDs, published ID and permalink. The service records a pending step before every provider
POST and records each returned ID immediately. If the process stops while a POST is pending, the
receipt becomes `uncertain`; operators must reconcile it instead of retrying the write.
One approved tool invocation may name several stable draft item IDs. Items are dispatched in order
and retain separate receipts; an uncertain item stops later items so an unknown partial batch cannot
expand its external effects.
When a published ID is already known, the service's bounded reconciliation worker performs only the
post readback and settles the original Run. If no published ID was returned, automatic reconciliation
does not issue a replacement publish request.

## Operations

On a managed server, run `trace-marketing server threads-setup`. It reads the existing public origin,
stores the Meta app ID and secret in the private service environment, generates a separate media URL
signing secret and prints three exact URLs. Register each URL in its matching Meta app field:

| Meta app field | Trace path |
| --- | --- |
| OAuth redirect URI | `/integrations/threads/callback` |
| Deauthorize callback URL | `/integrations/threads/deauthorize` |
| Data deletion callback URL | `/integrations/threads/data-deletion` |

Restart the service after changing the app configuration. Individual members then request account
connection in a shared Slack conversation; each completed OAuth callback creates an independent
owner-bound connection.

Keep the app secret and media signing secret outside logs and database JSON. Back up the service
SQLite database and the service `secrets/threads` directory together. Restoring only one side leaves
account metadata or token references incomplete. Disconnect revokes the local connection and removes
its token file.

## Provider privacy callbacks

Meta posts both privacy callbacks as `application/x-www-form-urlencoded` bodies containing one
`signed_request`. Trace accepts the request only when its HMAC-SHA256 signature matches the
configured app secret and its payload has the expected algorithm, issue time and provider user ID.
Invalid requests receive a generic rejection that does not reveal signature details.

The deauthorization callback revokes every owner-bound connection for the provider user ID and
deletes each token file. It returns success after local revocation, including a replay after the
connections have already gone.

The data deletion callback first creates or reuses one opaque receipt for the provider user ID. It
then deletes the account metadata and tokens, affected draft batches and revisions, media grants,
publication receipts and events, and provider metric snapshots. A draft batch containing any item
for the deleted connection is removed as a whole, because its immutable revisions preserve the
combined batch. The callback returns the receipt's confirmation code and a public status URL under
`/integrations/threads/data-deletion/{confirmation-code}`. A completed replay returns the same
receipt. Trace keys the receipt to the authenticated callback request, so a later callback after
fresh OAuth consent creates a new receipt and deletes the new connection lifecycle. A failed
deletion remains pending so Meta can retry safely.

OAuth completion, provider publication and privacy deletion share one process fence. Deletion waits
for a publication that already entered its provider write section, then removes its local records.
The same transaction writes connection tombstones before removing publication ledgers. Any stale
worker that resumes after deletion cannot recreate those ledgers. Fresh OAuth consent clears the
tombstone only after the new account record succeeds.

The receipt stores only a keyed request digest, status and timestamps; it never stores the raw
provider user ID. Keep the status URL public because Meta and the account owner must be able to read
it without Trace authentication. See Meta's current
[data deletion callback](https://developers.facebook.com/docs/development/create-an-app/app-dashboard/data-deletion-callback/)
and [deauthorization callback](https://developers.facebook.com/docs/facebook-login/manually-build-a-login-flow#deauth-callback)
requirements before production registration.

Before enabling production, verify the current Meta documentation, app-review requirements, supported
metrics, media formats, rate limits and token lifetime against a read-only connected account. Run an
authorized write canary only with an explicitly approved test account and exact content. Until those
checks are recorded, treat this document and the implementation as a candidate integration.
