import assert from "node:assert/strict";
import test from "node:test";

import { handleBackgroundAssetRequest } from "../src/background-assets.js";

const WORKSPACE = "cloudflare:trace_demo_kr";
const PERSONA = "persona-chic";
const PIN = "https://www.pinterest.com/pin/1";

// A deliberately small stand-in for the one table this module touches, plus the R2 bucket
// and the fetches an ingest performs. Real D1 statements go through prepare/bind the same
// way, so recognizing the SQL is enough to exercise every branch.
function harness({ fetchResponses = {} } = {}) {
  const rows = new Map();
  const objects = new Map();
  const fetched = [];

  globalThis.fetch = async (url) => {
    fetched.push(url);
    const respond = fetchResponses[url];
    if (!respond) throw new Error(`unexpected fetch: ${url}`);
    return respond();
  };

  const DB = {
    prepare(sql) {
      return {
        bind(...values) {
          return {
            async run() {
              if (sql.startsWith("INSERT OR IGNORE INTO persona_background_assets")) {
                const [assetId, workspaceId, personaId, sourceUrl, imageUrl, r2Key, sha256,
                  contentType, byteSize, origin, status, createdAt, updatedAt] = values;
                const duplicate = [...rows.values()].some(
                  (row) => row.workspace_id === workspaceId
                    && row.persona_id === personaId && row.sha256 === sha256,
                );
                if (duplicate) return { meta: { changes: 0 } };
                rows.set(assetId, {
                  asset_id: assetId, workspace_id: workspaceId, persona_id: personaId,
                  source_url: sourceUrl, image_url: imageUrl, r2_key: r2Key, sha256,
                  content_type: contentType, byte_size: byteSize, origin, status,
                  review_note: null, used_count: 0, last_used_at: null,
                  created_at: createdAt, updated_at: updatedAt,
                });
                return { meta: { changes: 1 } };
              }
              if (sql.startsWith("DELETE FROM persona_background_assets")) {
                const [, assetId] = values;
                return { meta: { changes: rows.delete(assetId) ? 1 : 0 } };
              }
              if (sql.startsWith("UPDATE persona_background_assets")) {
                const [status, note, , , assetId] = values;
                const row = rows.get(assetId);
                if (!row) return { meta: { changes: 0 } };
                row.status = status;
                row.review_note = note;
                return { meta: { changes: 1 } };
              }
              throw new Error(`unexpected run SQL: ${sql}`);
            },
            async first() {
              if (sql.includes("SELECT r2_key, content_type")) {
                const [, assetId] = values;
                const row = rows.get(assetId);
                return row ? { r2_key: row.r2_key, content_type: row.content_type } : null;
              }
              throw new Error(`unexpected first SQL: ${sql}`);
            },
            async all() {
              if (sql.includes("FROM persona_background_assets")) {
                const [workspaceId, personaId, maybeStatus] = values;
                const results = [...rows.values()].filter(
                  (row) => row.workspace_id === workspaceId
                    && row.persona_id === personaId
                    && (sql.includes("AND status = ?") ? row.status === maybeStatus : true),
                );
                return { results };
              }
              throw new Error(`unexpected all SQL: ${sql}`);
            },
          };
        },
      };
    },
  };

  const env = {
    DB,
    ARTIFACTS: {
      async put(key, bytes) { objects.set(key, bytes); },
      async get(key) {
        return objects.has(key) ? { body: objects.get(key) } : null;
      },
    },
  };
  const context = {
    workspaceId: WORKSPACE,
    async requirePersona(personaId) {
      if (personaId !== PERSONA) throw new Error("unknown persona");
    },
  };
  return { env, context, rows, objects, fetched };
}

const imageResponse = (bytes, contentType = "image/jpeg") => () => new Response(bytes, {
  status: 200,
  headers: { "content-type": contentType },
});

const ingest = (env, context, items) => handleBackgroundAssetRequest(
  new Request(`https://x/api/personas/${PERSONA}/background-assets`, {
    method: "POST",
    body: JSON.stringify({ items }),
  }),
  env,
  context,
);

test("a seed enters approved and a related pin enters pending", async () => {
  const seedUrl = "https://i.pinimg.com/originals/aa.jpg";
  const relatedUrl = "https://i.pinimg.com/originals/bb.jpg";
  const { env, context, rows } = harness({
    fetchResponses: {
      [seedUrl]: imageResponse(new Uint8Array([1, 2, 3]).buffer),
      [relatedUrl]: imageResponse(new Uint8Array([4, 5, 6]).buffer),
    },
  });

  const response = await ingest(env, context, [
    { image_url: seedUrl, source_url: PIN, origin: "seed" },
    { image_url: relatedUrl, source_url: PIN, origin: "related" },
  ]);
  const payload = await response.json();

  assert.equal(response.status, 201);
  assert.equal(payload.stored, 2);
  const statuses = [...rows.values()].map((row) => [row.origin, row.status]);
  // Saving the pin to the board was the human review, so the seed skips the queue; the
  // machine-expanded pin does not.
  assert.deepEqual(statuses.sort(), [["related", "pending"], ["seed", "approved"]]);
});

test("collecting the same picture twice yields one asset", async () => {
  const url = "https://i.pinimg.com/originals/cc.jpg";
  const { env, context, rows } = harness({
    fetchResponses: { [url]: imageResponse(new Uint8Array([7, 7, 7]).buffer) },
  });

  await ingest(env, context, [{ image_url: url, source_url: PIN, origin: "related" }]);
  const second = await ingest(env, context, [{ image_url: url, source_url: PIN, origin: "related" }]);
  const payload = await second.json();

  assert.equal(payload.duplicates, 1);
  assert.equal(rows.size, 1);
});

test("an image host outside the allowlist is refused without being fetched", async () => {
  const { env, context, fetched } = harness();

  const response = await ingest(env, context, [
    { image_url: "https://evil.example.com/x.jpg", source_url: PIN, origin: "related" },
  ]);
  const payload = await response.json();

  assert.equal(payload.failed, 1);
  assert.match(payload.outcomes[0].detail, /허용되지 않은 이미지 호스트/u);
  // The worker must not act as an open proxy: a disallowed URL is never requested at all.
  assert.deepEqual(fetched, []);
});

test("a failed R2 write leaves no row behind", async () => {
  const url = "https://i.pinimg.com/originals/dd.jpg";
  const { env, context, rows } = harness({
    fetchResponses: { [url]: imageResponse(new Uint8Array([9]).buffer) },
  });
  env.ARTIFACTS.put = async () => { throw new Error("R2 unavailable"); };

  const response = await ingest(env, context, [{ image_url: url, source_url: PIN, origin: "seed" }]);
  const payload = await response.json();

  // An asset whose image cannot be served would fail a capture much later, far from the
  // cause, so the row is rolled back with the object.
  assert.equal(payload.failed, 1);
  assert.equal(rows.size, 0);
});

test("review moves a pending asset and can reverse itself", async () => {
  const url = "https://i.pinimg.com/originals/ee.jpg";
  const { env, context, rows } = harness({
    fetchResponses: { [url]: imageResponse(new Uint8Array([1]).buffer) },
  });
  await ingest(env, context, [{ image_url: url, source_url: PIN, origin: "related" }]);
  const assetId = [...rows.keys()][0];

  const review = (accepted) => handleBackgroundAssetRequest(
    new Request(`https://x/api/background-assets/${assetId}/review`, {
      method: "POST",
      body: JSON.stringify({ accepted, note: accepted ? null : "위젯 자리가 시끄러움" }),
    }),
    env,
    context,
  );

  assert.equal((await (await review(false)).json()).status, "rejected");
  assert.equal(rows.get(assetId).review_note, "위젯 자리가 시끄러움");
  // A fat-fingered no has to be reversible without anyone touching the database.
  assert.equal((await (await review(true)).json()).status, "approved");
});

test("the listing filters by status and serves each image by its path", async () => {
  const url = "https://i.pinimg.com/originals/ff.png";
  const { env, context } = harness({
    fetchResponses: { [url]: imageResponse(new Uint8Array([5, 5]).buffer, "image/png") },
  });
  await ingest(env, context, [{ image_url: url, source_url: PIN, origin: "seed" }]);

  const listed = await handleBackgroundAssetRequest(
    new Request(`https://x/api/personas/${PERSONA}/background-assets?status=approved`),
    env,
    context,
  );
  const { assets } = await listed.json();
  assert.equal(assets.length, 1);
  assert.equal(assets[0].origin, "seed");
  assert.equal(assets[0].source_url, PIN);

  const image = await handleBackgroundAssetRequest(
    new Request(`https://x${assets[0].image_path}`),
    env,
    context,
  );
  assert.equal(image.status, 200);
  assert.equal(image.headers.get("content-type"), "image/png");
});

test("unrelated routes are left to the rest of the workspace", async () => {
  const { env, context } = harness();
  const response = await handleBackgroundAssetRequest(
    new Request("https://x/api/candidates"),
    env,
    context,
  );
  assert.equal(response, null);
});
