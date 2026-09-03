// The background asset pool: ingestion, review, and serving.
//
// The pool replaces the per-capture web search as the default source of wallpaper
// backgrounds. A person curates seeds on a Pinterest board; a collector expands each seed
// into its related pins and posts the lot here. Seeds arrive approved — saving the pin was
// the review. Expanded pins arrive pending and pass a yes/no gate in the workspace before
// any capture can be assigned one.
//
// This module is self-contained the way the threads modules are: it defines its own small
// helpers and returns Response objects rather than throwing across the module boundary,
// except for the persona lookup, which the caller provides and which speaks the
// workspace's own error language.

const MAX_ITEMS_PER_INGEST = 25;
const MAX_URL_LENGTH = 2048;
const MAX_NOTE_LENGTH = 2000;
const MAX_LIST_ROWS = 200;
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;

// Where an ingested image may be fetched from. The collector hands this API bare image
// URLs, and a worker fetching arbitrary URLs on request is an open proxy — so the host has
// to be one we chose. Pinterest serves every pin's file from i.pinimg.com; more hosts can
// be added with ASSET_IMAGE_HOSTS (comma-separated) without a deploy of this file.
const DEFAULT_IMAGE_HOSTS = ["i.pinimg.com"];

const IMAGE_CONTENT_TYPES = new Map([
  ["image/jpeg", "jpg"],
  ["image/png", "png"],
  ["image/webp", "webp"],
]);

const json = (payload, status = 200) => Response.json(payload, {
  status,
  headers: { "cache-control": "no-store" },
});

const failure = (status, detail) => json({ detail }, status);

/**
 * Handle a background-asset route, or return null when the request is something else.
 *
 * `context.workspaceId` scopes every row; `context.requirePersona` is the workspace's own
 * persona lookup, so a pool can only ever hang off a persona that exists.
 */
export async function handleBackgroundAssetRequest(request, env, context) {
  const url = new URL(request.url);

  const ingestRoute = url.pathname.match(/^\/api\/personas\/([^/]+)\/background-assets$/);
  if (ingestRoute && request.method === "POST") {
    const personaId = decodeURIComponent(ingestRoute[1]);
    await context.requirePersona(personaId);
    return ingestAssets(request, env, context.workspaceId, personaId);
  }
  if (ingestRoute && request.method === "GET") {
    const personaId = decodeURIComponent(ingestRoute[1]);
    await context.requirePersona(personaId);
    return listAssets(env, context.workspaceId, personaId, url.searchParams.get("status"));
  }

  const imageRoute = url.pathname.match(/^\/api\/background-assets\/([^/]+)\/image$/);
  if (imageRoute && request.method === "GET") {
    return serveAssetImage(env, context.workspaceId, decodeURIComponent(imageRoute[1]));
  }

  const reviewRoute = url.pathname.match(/^\/api\/background-assets\/([^/]+)\/review$/);
  if (reviewRoute && request.method === "POST") {
    return reviewAsset(request, env, context.workspaceId, decodeURIComponent(reviewRoute[1]));
  }

  return null;
}

async function ingestAssets(request, env, workspaceId, personaId) {
  let body;
  try {
    body = await request.json();
  } catch {
    return failure(400, "JSON 요청 본문이 올바르지 않습니다.");
  }
  const items = Array.isArray(body?.items) ? body.items : null;
  if (!items || items.length === 0 || items.length > MAX_ITEMS_PER_INGEST) {
    return failure(400, `items는 1~${MAX_ITEMS_PER_INGEST}개의 배열이어야 합니다.`);
  }

  const allowedHosts = imageHosts(env);
  const now = new Date().toISOString();
  const outcomes = [];
  // Sequential on purpose: the batch is small, the fetches hit one CDN, and a burst of
  // parallel fetches from a worker is how a collector gets rate-limited mid-ingest.
  for (const item of items) {
    outcomes.push(await ingestOne(env, workspaceId, personaId, item, allowedHosts, now));
  }
  const stored = outcomes.filter((outcome) => outcome.state === "stored").length;
  const duplicates = outcomes.filter((outcome) => outcome.state === "duplicate").length;
  return json(
    { persona_id: personaId, stored, duplicates, failed: items.length - stored - duplicates, outcomes },
    201,
  );
}

async function ingestOne(env, workspaceId, personaId, item, allowedHosts, now) {
  const imageUrl = assetUrl(item?.image_url);
  const sourceUrl = assetUrl(item?.source_url);
  const origin = item?.origin === "seed" ? "seed" : item?.origin === "related" ? "related" : null;
  if (!imageUrl || !sourceUrl || !origin) {
    return { state: "failed", image_url: item?.image_url ?? null, detail: "image_url, source_url, origin(seed|related)이 필요합니다." };
  }
  if (!allowedHosts.has(new URL(imageUrl).hostname)) {
    return { state: "failed", image_url: imageUrl, detail: "허용되지 않은 이미지 호스트입니다." };
  }

  let fetched;
  try {
    fetched = await fetch(imageUrl, { redirect: "follow" });
  } catch {
    return { state: "failed", image_url: imageUrl, detail: "이미지를 가져오지 못했습니다." };
  }
  if (!fetched.ok) {
    return { state: "failed", image_url: imageUrl, detail: `이미지 응답이 ${fetched.status}입니다.` };
  }
  // A redirect may land anywhere; the allowlist has to hold for where the bytes actually
  // came from, not only for where we asked.
  if (!allowedHosts.has(new URL(fetched.url || imageUrl).hostname)) {
    return { state: "failed", image_url: imageUrl, detail: "허용되지 않은 호스트로 리디렉션되었습니다." };
  }
  const contentType = (fetched.headers.get("content-type") || "").split(";")[0].trim();
  if (!IMAGE_CONTENT_TYPES.has(contentType)) {
    return { state: "failed", image_url: imageUrl, detail: `이미지 형식이 아닙니다: ${contentType || "없음"}` };
  }
  const bytes = await fetched.arrayBuffer();
  if (bytes.byteLength === 0 || bytes.byteLength > MAX_IMAGE_BYTES) {
    return { state: "failed", image_url: imageUrl, detail: "이미지 크기가 허용 범위를 벗어났습니다." };
  }

  const digest = await sha256Hex(bytes);
  const assetId = crypto.randomUUID();
  const r2Key = `background-assets/${workspaceId}/${personaId}/${digest}.${IMAGE_CONTENT_TYPES.get(contentType)}`;

  // Row first, object second, and the insert is what detects a duplicate: the unique index
  // on (workspace, persona, sha256) makes the second collection of the same picture a
  // no-op instead of a second asset the rotation would show twice.
  const inserted = await env.DB.prepare(
    `INSERT OR IGNORE INTO persona_background_assets
       (asset_id, workspace_id, persona_id, source_url, image_url, r2_key, sha256,
        content_type, byte_size, origin, status, review_note, used_count, last_used_at,
        created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, NULL, ?, ?)`,
  )
    .bind(
      assetId,
      workspaceId,
      personaId,
      sourceUrl,
      imageUrl,
      r2Key,
      digest,
      contentType,
      bytes.byteLength,
      origin,
      origin === "seed" ? "approved" : "pending",
      now,
      now,
    )
    .run();
  if (inserted.meta.changes !== 1) {
    return { state: "duplicate", image_url: imageUrl, sha256: digest };
  }
  try {
    await env.ARTIFACTS.put(r2Key, bytes, { httpMetadata: { contentType } });
  } catch {
    // The object failed to land, so the row must not stand: an asset whose image cannot be
    // served would be assigned to a capture and fail it much later, far from the cause.
    await env.DB.prepare(
      "DELETE FROM persona_background_assets WHERE workspace_id = ? AND asset_id = ?",
    )
      .bind(workspaceId, assetId)
      .run();
    return { state: "failed", image_url: imageUrl, detail: "이미지 저장에 실패했습니다." };
  }
  return { state: "stored", image_url: imageUrl, asset_id: assetId, sha256: digest, status: origin === "seed" ? "approved" : "pending" };
}

async function listAssets(env, workspaceId, personaId, requestedStatus) {
  const status = requestedStatus === null || requestedStatus === "" ? null : requestedStatus;
  if (status !== null && !["pending", "approved", "rejected"].includes(status)) {
    return failure(400, "status는 pending, approved, rejected 중 하나여야 합니다.");
  }
  const statement = status
    ? env.DB.prepare(
        `SELECT * FROM persona_background_assets
         WHERE workspace_id = ? AND persona_id = ? AND status = ?
         ORDER BY created_at LIMIT ?`,
      ).bind(workspaceId, personaId, status, MAX_LIST_ROWS)
    : env.DB.prepare(
        `SELECT * FROM persona_background_assets
         WHERE workspace_id = ? AND persona_id = ?
         ORDER BY created_at LIMIT ?`,
      ).bind(workspaceId, personaId, MAX_LIST_ROWS);
  const result = await statement.all();
  return json({ persona_id: personaId, assets: result.results.map(assetFromRow) });
}

async function serveAssetImage(env, workspaceId, assetId) {
  const row = await env.DB.prepare(
    "SELECT r2_key, content_type FROM persona_background_assets WHERE workspace_id = ? AND asset_id = ?",
  )
    .bind(workspaceId, assetId)
    .first();
  if (!row) return failure(404, "배경 자산을 찾을 수 없습니다.");
  const object = await env.ARTIFACTS.get(row.r2_key);
  if (!object) return failure(404, "배경 자산 이미지가 저장소에 없습니다.");
  return new Response(object.body, {
    headers: {
      "content-type": row.content_type,
      // The image is content-addressed by its key, so it never changes under a URL.
      "cache-control": "private, max-age=3600",
    },
  });
}

async function reviewAsset(request, env, workspaceId, assetId) {
  let body;
  try {
    body = await request.json();
  } catch {
    return failure(400, "JSON 요청 본문이 올바르지 않습니다.");
  }
  if (typeof body?.accepted !== "boolean") {
    return failure(400, "accepted(boolean)가 필요합니다.");
  }
  const note = typeof body?.note === "string" && body.note.trim()
    ? body.note.trim().slice(0, MAX_NOTE_LENGTH)
    : null;
  // Re-review is allowed on purpose. A reviewer who fat-fingers a no has to be able to
  // turn it back into a yes without anyone touching the database.
  const updated = await env.DB.prepare(
    `UPDATE persona_background_assets
     SET status = ?, review_note = ?, updated_at = ?
     WHERE workspace_id = ? AND asset_id = ?`,
  )
    .bind(body.accepted ? "approved" : "rejected", note, new Date().toISOString(), workspaceId, assetId)
    .run();
  if (updated.meta.changes !== 1) return failure(404, "배경 자산을 찾을 수 없습니다.");
  return json({ asset_id: assetId, status: body.accepted ? "approved" : "rejected" });
}

function assetFromRow(row) {
  return {
    asset_id: row.asset_id,
    persona_id: row.persona_id,
    source_url: row.source_url,
    image_url: row.image_url,
    sha256: row.sha256,
    content_type: row.content_type,
    byte_size: row.byte_size,
    origin: row.origin,
    status: row.status,
    review_note: row.review_note,
    used_count: row.used_count,
    last_used_at: row.last_used_at,
    created_at: row.created_at,
    image_path: `/api/background-assets/${encodeURIComponent(row.asset_id)}/image`,
  };
}

function assetUrl(value) {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > MAX_URL_LENGTH) return null;
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== "https:") return null;
    return parsed.toString();
  } catch {
    return null;
  }
}

function imageHosts(env) {
  const configured = typeof env.ASSET_IMAGE_HOSTS === "string" ? env.ASSET_IMAGE_HOSTS : "";
  const hosts = configured
    .split(",")
    .map((host) => host.trim().toLowerCase())
    .filter(Boolean);
  return new Set([...DEFAULT_IMAGE_HOSTS, ...hosts]);
}

async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
