import assert from "node:assert/strict";
import test from "node:test";

import { handleHostedThreadsProfiles } from "../src/threads/profiles-api.js";
import { threadsOAuthCallbackResponse } from "../src/threads/oauth-callback.js";
import { createThreadsProfileFixture } from "./threads-profile-fixture.js";

const NOW = Date.parse("2026-08-31T00:00:00.000Z");

const environment = (fixture) => ({
  DB: fixture.DB,
  HOSTED_WORKSPACE_ACCOUNT_ID: "account-a",
  CONTROL_PLANE_TOKEN: "control-placeholder",
  THREADS_APP_ID: "app-placeholder",
  THREADS_APP_SECRET: "credential-placeholder",
  THREADS_REDIRECT_URI: "https://workspace.example/api/threads/oauth/callback",
  THREADS_GRAPH_API_VERSION: "v1.0",
});

const request = (path, { method = "GET", body, authorized = true } = {}) => new Request(
  `https://workspace.example${path}`,
  {
    method,
    headers: {
      accept: "application/json",
      ...(authorized ? { authorization: "Bearer control-placeholder" } : {}),
      ...(body ? { "content-type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  },
);

const options = (fixture, overrides = {}) => ({
  graphClient: fixture.graphClient,
  tokenVault: fixture.tokenVault,
  storeFactory: () => fixture.store,
  now: () => NOW,
  randomBytes: (size) => new Uint8Array(size).fill(7),
  activateTool: async () => {},
  ...overrides,
});

const call = (fixture, path, init, overrides) => handleHostedThreadsProfiles(
  request(path, init),
  environment(fixture),
  options(fixture, overrides),
);

const startAndCallback = async (fixture, profile, startPath = "/api/threads/oauth/start") => {
  fixture.validatedProfiles.push(profile);
  const started = await call(fixture, startPath, { method: "POST", body: {} });
  assert.equal(started.status, 201);
  const authorization = new URL((await started.json()).authorization_url);
  assert.equal(authorization.origin, "https://threads.net");
  assert.deepEqual(authorization.searchParams.get("scope")?.split(","), fixture.requiredScopes);
  const state = authorization.searchParams.get("state");
  const callback = await call(
    fixture,
    `/api/threads/oauth/callback?code=opaque&state=${encodeURIComponent(state)}`,
    { authorized: false },
  );
  return { callback, state };
};

test("privileged routes reject missing control authorization", async () => {
  const fixture = createThreadsProfileFixture();
  const response = await call(
    fixture,
    "/api/threads/oauth/start",
    { method: "POST", body: {}, authorized: false },
  );
  assert.equal(response.status, 401);
  assert.equal((await response.json()).error, "THREADS_UNAUTHORIZED");
  assert.equal(fixture.states.size, 0);
});

test("browser callback posts only an origin-bound non-secret completion message", async () => {
  const response = threadsOAuthCallbackResponse(
    new Request("https://workspace.example/api/threads/oauth/callback"),
    "https://workspace.example/api/threads/oauth/callback",
    { profile_id: "profile-a", username: "trace_a" },
  );
  const html = await response.text();
  assert.match(html, /threads-oauth-complete/u);
  assert.match(html, /"https:\/\/workspace\.example"/u);
  assert.doesNotMatch(html, /access_token|authorization_code|oauth_state/u);
  assert.equal(response.headers.get("cache-control"), "no-store");
});

test("connects two profiles, switches the default, and enables with revision CAS", async () => {
  const fixture = createThreadsProfileFixture();
  const profileA = {
    id: "external-a",
    username: "trace_a",
    scopes: fixture.requiredScopes,
    expiresAt: 2_000_000_000,
  };
  const profileB = {
    id: "external-b",
    username: "trace_b",
    scopes: fixture.requiredScopes,
    expiresAt: 2_000_000_000,
  };
  const first = await startAndCallback(fixture, profileA);
  const firstBody = await first.callback.json();
  assert.equal(first.callback.status, 200);
  assert.equal(firstBody.profile.threads_user_id, "external-a");
  assert.equal(JSON.stringify(firstBody).includes("long:"), false);
  const replay = await call(
    fixture,
    `/api/threads/oauth/callback?code=opaque&state=${encodeURIComponent(first.state)}`,
    { authorized: false },
  );
  assert.equal(replay.status, 409);

  const second = await startAndCallback(fixture, profileB);
  const secondBody = await second.callback.json();
  assert.equal(second.callback.status, 200);
  assert.equal(fixture.profiles.size, 2);

  const selected = await call(
    fixture,
    `/api/threads/profiles/${secondBody.profile.profile_id}/default`,
    { method: "POST", body: { expected_revision: 1 } },
  );
  assert.equal(selected.status, 200);
  assert.equal((await selected.json()).revision, 2);
  const enabled = await call(
    fixture,
    "/api/threads/settings",
    { method: "PATCH", body: { enabled: true, expected_revision: 2 } },
  );
  assert.equal(enabled.status, 200);
  assert.equal((await enabled.json()).threads_auto_publish_enabled, true);

  const listed = await call(fixture, "/api/threads/profiles");
  const serialized = JSON.stringify(await listed.json());
  assert.equal(serialized.includes("ciphertext"), false);
  assert.equal(serialized.includes("long:"), false);
  assert.equal(fixture.encryptedInputs.length, 2);
});

test("rejects missing scopes, stale revision, duplicates, and cross-user reconnect", async () => {
  const fixture = createThreadsProfileFixture();
  const limited = {
    id: "external-a",
    username: "trace_a",
    scopes: ["threads_basic"],
    expiresAt: 2_000_000_000,
  };
  const connected = await startAndCallback(fixture, limited);
  const profile = (await connected.callback.json()).profile;
  const stale = await call(
    fixture,
    `/api/threads/profiles/${profile.profile_id}/default`,
    { method: "POST", body: { expected_revision: 99 } },
  );
  assert.equal(stale.status, 409);
  const selected = await call(
    fixture,
    `/api/threads/profiles/${profile.profile_id}/default`,
    { method: "POST", body: { expected_revision: 1 } },
  );
  assert.equal(selected.status, 200);
  const enabled = await call(
    fixture,
    "/api/threads/settings",
    { method: "PATCH", body: { enabled: true, expected_revision: 2 } },
  );
  assert.equal(enabled.status, 409);
  assert.equal(fixture.account.threads_auto_publish_enabled, false);

  const duplicate = await startAndCallback(fixture, limited);
  assert.equal(duplicate.callback.status, 409);
  const staleDisconnect = await call(
    fixture,
    `/api/threads/profiles/${profile.profile_id}/disconnect`,
    { method: "POST", body: { expected_revision: 99 } },
  );
  assert.equal(staleDisconnect.status, 409);
  assert.equal(fixture.profiles.get(profile.profile_id).state, "active");
  const disconnected = await call(
    fixture,
    `/api/threads/profiles/${profile.profile_id}/disconnect`,
    { method: "POST", body: { expected_revision: 2 } },
  );
  assert.equal(disconnected.status, 200);
  assert.equal(fixture.profiles.get(profile.profile_id).token_ciphertext, null);

  const mismatch = await startAndCallback(
    fixture,
    { ...limited, id: "external-other" },
    `/api/threads/profiles/${profile.profile_id}/reconnect`,
  );
  assert.equal(mismatch.callback.status, 409);
  assert.equal(fixture.profiles.get(profile.profile_id).state, "disconnected");
});

test("expired and unknown OAuth states are one-time account-bound failures", async () => {
  const fixture = createThreadsProfileFixture();
  const started = await call(
    fixture,
    "/api/threads/oauth/start",
    { method: "POST", body: {} },
  );
  const authorization = new URL((await started.json()).authorization_url);
  const state = authorization.searchParams.get("state");
  const stored = [...fixture.states.values()][0];
  stored.expires_at = "2026-08-30T00:00:00.000Z";
  const expired = await call(
    fixture,
    `/api/threads/oauth/callback?code=opaque&state=${encodeURIComponent(state)}`,
    { authorized: false },
  );
  assert.equal(expired.status, 409);
  const unknown = await call(
    fixture,
    "/api/threads/oauth/callback?code=opaque&state=unknown",
    { authorized: false },
  );
  assert.equal(unknown.status, 409);
});
