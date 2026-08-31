import assert from "node:assert/strict";
import test from "node:test";

import {
  createThreadsGraphClient,
  ThreadsGraphClient,
  ThreadsGraphError,
} from "../src/threads/client.js";
import {
  createThreadsTokenVaultFromEnv,
  ThreadsTokenVaultError,
  createThreadsTokenVault,
} from "../src/threads/crypto.js";

const VERSION = "v1.0";
const REQUIRED_SCOPES = [
  "threads_basic",
  "threads_content_publish",
  "threads_manage_insights",
  "threads_read_replies",
];

const jsonResponse = (body, status = 200, headers = {}) => new Response(
  JSON.stringify(body),
  { status, headers: { "content-type": "application/json", ...headers } },
);

const clientConfig = (overrides = {}) => ({
  appId: "app-id",
  appSecret: "app-secret",
  redirectUri: "https://workspace.test/threads/callback",
  apiVersion: VERSION,
  ...overrides,
});

const createClient = (fetchImpl, overrides = {}) => new ThreadsGraphClient({
  ...clientConfig(),
  fetchImpl,
  ...overrides,
});

test("AES-GCM vault round-trips ciphertext without persisting plaintext", async () => {
  const rawKey = new Uint8Array(32).fill(7);
  const key = `v7:${Buffer.from(rawKey).toString("base64")}`;
  const vault = createThreadsTokenVaultFromEnv(
    { THREADS_TOKEN_ENCRYPTION_KEY: key },
    { randomBytes: (size) => new Uint8Array(size).fill(3) },
  );
  const record = await vault.encrypt("sentinel-access-token");

  assert.deepEqual(Object.keys(record).sort(), ["ciphertext", "key_version", "nonce"]);
  assert.equal(record.key_version, "v7");
  assert.equal(JSON.stringify(record).includes("sentinel-access-token"), false);
  assert.equal(await vault.decrypt(record), "sentinel-access-token");

  const wrongVersion = createThreadsTokenVault(`v8:${Buffer.from(rawKey).toString("base64")}`);
  await assert.rejects(
    wrongVersion.decrypt(record),
    (error) => error instanceof ThreadsTokenVaultError && error.code === "THREADS_TOKEN_KEY_VERSION_MISMATCH",
  );
});

test("vault rejects malformed keys and ciphertext records with typed errors", async () => {
  assert.throws(
    () => createThreadsTokenVault("not-versioned"),
    (error) => error instanceof ThreadsTokenVaultError && error.code === "THREADS_TOKEN_KEY_INVALID",
  );
  assert.throws(
    () => createThreadsTokenVault(`v1:${Buffer.from(new Uint8Array(16)).toString("base64")}`),
    (error) => error instanceof ThreadsTokenVaultError && error.code === "THREADS_TOKEN_KEY_INVALID",
  );

  const rawKey = new Uint8Array(32).fill(4);
  const vault = createThreadsTokenVault(`v4:${Buffer.from(rawKey).toString("base64")}`);
  const record = await vault.encrypt("fixture-credential");
  await assert.rejects(
    vault.decrypt({ ...record, plaintext: "fixture-credential" }),
    (error) => error instanceof ThreadsTokenVaultError && error.code === "THREADS_TOKEN_RECORD_INVALID",
  );
});

test("client requires complete pinned configuration", () => {
  for (const field of ["appId", "appSecret", "redirectUri", "apiVersion"]) {
    assert.throws(
      () => new ThreadsGraphClient({ ...clientConfig(), [field]: "", fetchImpl: async () => jsonResponse({}) }),
      (error) => error instanceof ThreadsGraphError && error.code === "THREADS_CONFIG_INVALID",
    );
  }
  assert.throws(
    () => new ThreadsGraphClient({ ...clientConfig({ apiVersion: "latest" }), fetchImpl: async () => jsonResponse({}) }),
    (error) => error instanceof ThreadsGraphError && error.code === "THREADS_CONFIG_INVALID",
  );

  assert.ok(createThreadsGraphClient({
    THREADS_APP_ID: "app-id",
    THREADS_APP_SECRET: "app-secret",
    THREADS_REDIRECT_URI: "https://workspace.test/threads/callback",
    THREADS_GRAPH_API_VERSION: VERSION,
  }, { fetchImpl: async () => jsonResponse({}) }) instanceof ThreadsGraphClient);
});

test("exchanges authorization and long-lived tokens then refreshes without leaking secrets", async () => {
  const seen = [];
  const fetchImpl = async (input, init = {}) => {
    const url = new URL(input);
    seen.push({ method: init.method ?? "GET", pathname: url.pathname, params: Object.fromEntries(url.searchParams) });
    if (url.pathname.endsWith("/oauth/access_token")) {
      return jsonResponse({ access_token: "short-token", user_id: "user-1" });
    }
    if (url.pathname.endsWith("/access_token")) {
      return jsonResponse({ access_token: "long-token", token_type: "bearer", expires_in: 5184000 });
    }
    return jsonResponse({ access_token: "refreshed-token", token_type: "bearer", expires_in: 5184000 });
  };
  const client = createClient(fetchImpl);

  assert.deepEqual(await client.exchangeAuthorizationCode("authorization-code"), {
    accessToken: "short-token",
    userId: "user-1",
  });
  assert.deepEqual(await client.exchangeLongLivedToken("short-token"), {
    accessToken: "long-token",
    tokenType: "bearer",
    expiresIn: 5184000,
  });
  assert.deepEqual(await client.refreshLongLivedToken("long-token"), {
    accessToken: "refreshed-token",
    tokenType: "bearer",
    expiresIn: 5184000,
  });
  assert.deepEqual(seen.map(({ method, pathname }) => [method, pathname]), [
    ["POST", "/oauth/access_token"],
    ["GET", "/access_token"],
    ["GET", "/refresh_access_token"],
  ]);
  assert.equal(seen.every(({ pathname }) => !pathname.startsWith(`/${VERSION}/`)), true);
  assert.equal(seen[0].params.client_secret, "app-secret");
  assert.equal(seen[0].params.code, "authorization-code");
});

test("validates profile identity, token expiry, app, and required scopes", async () => {
  const fetchImpl = async (input) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/debug_token")) {
      return jsonResponse({ data: {
        app_id: "app-id",
        user_id: "user-1",
        is_valid: true,
        expires_at: 2_000_000_000,
        scopes: REQUIRED_SCOPES,
      } });
    }
    return jsonResponse({ id: "user-1", username: "trace_profile" });
  };
  const client = createClient(fetchImpl, { now: () => 1_900_000_000_000 });
  assert.deepEqual(await client.getValidatedProfile("profile-token", REQUIRED_SCOPES), {
    id: "user-1",
    username: "trace_profile",
    scopes: REQUIRED_SCOPES,
    expiresAt: 2_000_000_000,
  });

  const missingScope = createClient(async (input) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/debug_token")) {
      return jsonResponse({ data: {
        app_id: "app-id", user_id: "user-1", is_valid: true,
        expires_at: 2_000_000_000, scopes: ["threads_basic"],
      } });
    }
    return jsonResponse({ id: "user-1", username: "trace_profile" });
  }, { now: () => 1_900_000_000_000 });
  await assert.rejects(
    missingScope.getValidatedProfile("profile-token", REQUIRED_SCOPES),
    (error) => error instanceof ThreadsGraphError
      && error.code === "THREADS_REQUIRED_SCOPES_MISSING"
      && error.details.missingScopes.includes("threads_content_publish"),
  );

  const expired = createClient(async (input) => {
    const url = new URL(input);
    assert.equal(url.pathname.endsWith("/debug_token"), true);
    return jsonResponse({ data: {
      app_id: "app-id", user_id: "user-1", is_valid: true,
      expires_at: 1_800_000_000, scopes: REQUIRED_SCOPES,
    } });
  }, { now: () => 1_900_000_000_000 });
  await assert.rejects(
    expired.getValidatedProfile("profile-token"),
    (error) => error instanceof ThreadsGraphError && error.code === "THREADS_REAUTH_REQUIRED",
  );
});

test("GET retries a bounded 429 Retry-After with injected deterministic sleep", async () => {
  const sleeps = [];
  let calls = 0;
  const client = createClient(async () => {
    calls += 1;
    if (calls === 1) return jsonResponse({ error: { message: "rate limited" } }, 429, { "retry-after": "2" });
    return jsonResponse({ data: [{ quota_usage: 3, config: { quota_total: 250, quota_duration: 86400 } }] });
  }, { sleeper: async (milliseconds) => sleeps.push(milliseconds), maxGetAttempts: 2 });

  assert.deepEqual(await client.getPublishingLimit("profile-token"), {
    quotaUsage: 3,
    quotaTotal: 250,
    quotaDuration: 86400,
  });
  assert.equal(calls, 2);
  assert.deepEqual(sleeps, [2000]);
});

test("GET bounds 5xx retries and exposes stable upstream state", async () => {
  const sleeps = [];
  let calls = 0;
  const client = createClient(async () => {
    calls += 1;
    return jsonResponse({ error: { message: "unavailable" } }, 503);
  }, { sleeper: async (milliseconds) => sleeps.push(milliseconds), maxGetAttempts: 3 });

  await assert.rejects(
    client.getPublishingLimit("profile-token"),
    (error) => error instanceof ThreadsGraphError
      && error.code === "THREADS_UPSTREAM_UNAVAILABLE"
      && error.status === 503,
  );
  assert.equal(calls, 3);
  assert.deepEqual(sleeps, [250, 500]);
});

test("GET aborts a hung request within the configured bound", async () => {
  let observedSignal = null;
  const client = createClient(async (_input, init = {}) => {
    observedSignal = init.signal ?? null;
    if (!observedSignal) throw new Error("missing abort signal");
    return new Promise((_resolve, reject) => {
      observedSignal.addEventListener("abort", () => reject(observedSignal.reason), { once: true });
    });
  }, { maxGetAttempts: 1, requestTimeoutMs: 5 });

  await assert.rejects(
    client.getPublishingLimit("profile-token"),
    (error) => error instanceof ThreadsGraphError && error.code === "THREADS_REQUEST_TIMEOUT",
  );
  assert.equal(observedSignal?.aborted, true);
});

test("malformed Graph JSON and authorization failures use stable typed codes", async () => {
  const malformed = createClient(async () => new Response("not json", { status: 200 }));
  await assert.rejects(
    malformed.getPost("post-1", "token"),
    (error) => error instanceof ThreadsGraphError && error.code === "THREADS_RESPONSE_MALFORMED",
  );

  const unauthorized = createClient(async () => jsonResponse({ error: { message: "bad token", code: 190 } }, 401));
  await assert.rejects(
    unauthorized.getPublishingLimit("secret-token"),
    (error) => error instanceof ThreadsGraphError
      && error.code === "THREADS_REAUTH_REQUIRED"
      && !String(error).includes("secret-token"),
  );

  const forbidden = createClient(async () => jsonResponse({ error: { message: "scope" } }, 403));
  await assert.rejects(
    forbidden.getPublishingLimit("secret-token"),
    (error) => error instanceof ThreadsGraphError && error.code === "THREADS_REAUTH_REQUIRED",
  );

  let malformedHttpCalls = 0;
  const malformedHttp = createClient(async () => {
    malformedHttpCalls += 1;
    return {};
  });
  await assert.rejects(
    malformedHttp.getPublishingLimit("profile-token"),
    (error) => error instanceof ThreadsGraphError && error.code === "THREADS_RESPONSE_MALFORMED",
  );
  assert.equal(malformedHttpCalls, 1);
});

test("creates one IMAGE container, publishes once, and reads back an authoritative post", async () => {
  const requests = [];
  const fetchImpl = async (input, init = {}) => {
    const url = new URL(input);
    requests.push({ method: init.method ?? "GET", pathname: url.pathname, params: Object.fromEntries(url.searchParams) });
    if (url.pathname.endsWith("/threads")) return jsonResponse({ id: "container-1" });
    if (url.pathname.endsWith("/threads_publish")) return jsonResponse({ id: "post-1" });
    return jsonResponse({ id: "post-1", permalink: "https://www.threads.net/@trace/post/one", media_type: "IMAGE", timestamp: "2026-08-31T02:00:00+0000" });
  };
  const client = createClient(fetchImpl);
  assert.deepEqual(await client.createImageContainer({
    accessToken: "profile-token",
    imageUrl: "https://media.test/signed.png?expires=123&signature=safe",
    text: "caption",
    altText: "preview",
  }), { containerId: "container-1" });
  assert.deepEqual(await client.publishContainer("container-1", "profile-token"), { postId: "post-1" });
  assert.deepEqual(await client.getPost("post-1", "profile-token"), {
    id: "post-1",
    permalink: "https://www.threads.net/@trace/post/one",
    mediaType: "IMAGE",
    timestamp: "2026-08-31T02:00:00+0000",
  });
  assert.deepEqual(requests.map(({ method, pathname }) => [method, pathname]), [
    ["POST", `/${VERSION}/me/threads`],
    ["POST", `/${VERSION}/me/threads_publish`],
    ["GET", `/${VERSION}/post-1`],
  ]);
  assert.equal(requests.every(({ pathname }) => pathname.startsWith(`/${VERSION}/`)), true);
  assert.equal(requests[0].params.media_type, "IMAGE");
  assert.equal(requests[0].params.image_url.startsWith("https://media.test/"), true);
});

test("lost publish response is ambiguous and never retried", async () => {
  let publishCalls = 0;
  const client = createClient(async (input) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/threads_publish")) {
      publishCalls += 1;
      throw new TypeError("connection reset");
    }
    throw new Error("unexpected request");
  });

  await assert.rejects(
    client.publishContainer("container-1", "profile-token"),
    (error) => error instanceof ThreadsGraphError && error.code === "THREADS_PUBLISH_AMBIGUOUS",
  );
  assert.equal(publishCalls, 1);
});

test("truncated successful publish response is ambiguous and never retried", async () => {
  let publishCalls = 0;
  const client = createClient(async () => {
    publishCalls += 1;
    return new Response("truncated", { status: 200 });
  });

  await assert.rejects(
    client.publishContainer("container-1", "profile-token"),
    (error) => error instanceof ThreadsGraphError && error.code === "THREADS_PUBLISH_AMBIGUOUS",
  );
  assert.equal(publishCalls, 1);
});

test("publish 401, 403, and 5xx failures are typed without retry", async () => {
  const scenarios = [
    [401, "THREADS_REAUTH_REQUIRED"],
    [403, "THREADS_REAUTH_REQUIRED"],
    [503, "THREADS_PUBLISH_AMBIGUOUS"],
  ];
  for (const [status, expectedCode] of scenarios) {
    let publishCalls = 0;
    const client = createClient(async () => {
      publishCalls += 1;
      return jsonResponse({ error: { message: "sanitized" } }, status);
    });
    await assert.rejects(
      client.publishContainer("container-1", "profile-token"),
      (error) => error instanceof ThreadsGraphError && error.code === expectedCode,
    );
    assert.equal(publishCalls, 1);
  }
});

test("strictly parses all requested post insight metrics", async () => {
  const client = createClient(async () => jsonResponse({ data: [
    { name: "views", period: "lifetime", values: [{ value: 101 }] },
    { name: "likes", period: "lifetime", values: [{ value: 9 }] },
    { name: "replies", period: "lifetime", values: [{ value: 4 }] },
    { name: "reposts", period: "lifetime", values: [{ value: 3 }] },
    { name: "quotes", period: "lifetime", values: [{ value: 2 }] },
    { name: "shares", period: "lifetime", values: [{ value: 1 }] },
  ] }));
  assert.deepEqual(await client.getPostInsights("post-1", "profile-token"), {
    views: 101, likes: 9, replies: 4, reposts: 3, quotes: 2, shares: 1,
  });

  const duplicateMetric = createClient(async () => jsonResponse({ data: [
    { name: "views", period: "lifetime", values: [{ value: 101 }] },
    { name: "likes", period: "lifetime", values: [{ value: 9 }] },
    { name: "replies", period: "lifetime", values: [{ value: 4 }] },
    { name: "reposts", period: "lifetime", values: [{ value: 3 }] },
    { name: "quotes", period: "lifetime", values: [{ value: 2 }] },
    { name: "shares", period: "lifetime", values: [{ value: 1 }] },
    { name: "shares", period: "lifetime", values: [{ value: 999 }] },
  ] }));
  await assert.rejects(
    duplicateMetric.getPostInsights("post-1", "profile-token"),
    (error) => error instanceof ThreadsGraphError && error.code === "THREADS_RESPONSE_MALFORMED",
  );
});

test("collects bounded top-level reply pages and preserves untrusted text as data", async () => {
  const cursors = [];
  const client = createClient(async (input) => {
    const url = new URL(input);
    cursors.push(url.searchParams.get("after"));
    if (!url.searchParams.has("after")) {
      return jsonResponse({
        data: [{ id: "reply-1", text: "<script>not interpreted</script>", timestamp: "2026-08-31T03:00:00+0000" }],
        paging: { cursors: { after: "cursor-2" }, next: "https://graph.threads.net/opaque" },
      });
    }
    return jsonResponse({
      data: [{ id: "reply-2", text: "second", timestamp: "2026-08-31T03:01:00+0000" }],
      paging: { cursors: {} },
    });
  });

  assert.deepEqual(await client.listTopLevelReplies("post-1", "profile-token", { maxPages: 2, limit: 25 }), {
    replies: [
      { id: "reply-1", text: "<script>not interpreted</script>", timestamp: "2026-08-31T03:00:00+0000" },
      { id: "reply-2", text: "second", timestamp: "2026-08-31T03:01:00+0000" },
    ],
    nextCursor: null,
    pagesRead: 2,
  });
  assert.deepEqual(cursors, [null, "cursor-2"]);

  const malformedPaging = createClient(async () => jsonResponse({ data: [], paging: [] }));
  await assert.rejects(
    malformedPaging.listTopLevelReplies("post-1", "profile-token"),
    (error) => error instanceof ThreadsGraphError && error.code === "THREADS_RESPONSE_MALFORMED",
  );
});
