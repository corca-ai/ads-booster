import { createThreadsGraphClient, THREADS_REQUIRED_SCOPES, ThreadsGraphError } from "./client.js";
import { createThreadsTokenVaultFromEnv, ThreadsTokenVaultError } from "./crypto.js";
import { threadsOAuthCallbackResponse } from "./oauth-callback.js";
import { createThreadsProfilesStore, ThreadsProfilesStoreError } from "./profiles-store.js";

const AUTHORIZATION_ENDPOINT = "https://threads.net/oauth/authorize";
const STATE_TTL_MS = 10 * 60_000;

const json = (payload, status = 200) => Response.json(payload, {
  status,
  headers: { "cache-control": "no-store" },
});

const readJson = async (request) => {
  try {
    const value = await request.json();
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error();
    return value;
  } catch {
    throw new ThreadsProfilesStoreError(
      "THREADS_REQUEST_INVALID",
      400,
      "JSON 요청 본문이 올바르지 않습니다.",
    );
  }
};

const requiredString = (value, field) => {
  if (typeof value !== "string" || value.length === 0) {
    throw new ThreadsProfilesStoreError(
      "THREADS_REQUEST_INVALID",
      400,
      `${field} 값이 필요합니다.`,
    );
  }
  return value;
};

const expectedRevision = (value) => {
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new ThreadsProfilesStoreError(
      "THREADS_REQUEST_INVALID",
      400,
      "expected_revision 값이 필요합니다.",
    );
  }
  return value;
};

const accountId = (env) => requiredString(env.HOSTED_WORKSPACE_ACCOUNT_ID, "account_id");

const authorize = (request, env) => {
  if (!env.CONTROL_PLANE_TOKEN || request.headers.get("authorization") !== `Bearer ${env.CONTROL_PLANE_TOKEN}`) {
    throw new ThreadsProfilesStoreError("THREADS_UNAUTHORIZED", 401, "unauthorized");
  }
};

const encodeState = (bytes) => {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
};

const sha256 = async (value) => {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
};

const newState = (randomBytes) => {
  const bytes = randomBytes(32);
  if (!(bytes instanceof Uint8Array) || bytes.byteLength !== 32) {
    throw new ThreadsProfilesStoreError(
      "THREADS_RANDOM_INVALID",
      500,
      "OAuth state 생성에 실패했습니다.",
    );
  }
  return encodeState(bytes);
};

const authorizationUrl = (env, state) => {
  const url = new URL(AUTHORIZATION_ENDPOINT);
  url.searchParams.set("client_id", requiredString(env.THREADS_APP_ID, "THREADS_APP_ID"));
  url.searchParams.set("redirect_uri", requiredString(env.THREADS_REDIRECT_URI, "THREADS_REDIRECT_URI"));
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", THREADS_REQUIRED_SCOPES.join(","));
  url.searchParams.set("state", state);
  return url.toString();
};

const mapError = (error) => {
  if (error instanceof ThreadsProfilesStoreError) return error;
  if (error instanceof ThreadsTokenVaultError) {
    return new ThreadsProfilesStoreError(error.code, 503, "Threads token 보관 설정을 확인해 주세요.");
  }
  if (error instanceof ThreadsGraphError) {
    const status = error.code === "THREADS_RATE_LIMITED"
      ? 429
      : error.code === "THREADS_CONFIG_INVALID"
        ? 503
        : ["THREADS_REAUTH_REQUIRED", "THREADS_REQUIRED_SCOPES_MISSING"].includes(error.code)
          ? 409
          : error.code === "THREADS_INPUT_INVALID"
            ? 400
            : 502;
    return new ThreadsProfilesStoreError(error.code, status, error.message);
  }
  return new ThreadsProfilesStoreError(
    "THREADS_INTERNAL_ERROR",
    500,
    "Threads 요청을 처리하지 못했습니다.",
  );
};

const profilePayload = async (validated, encrypted, now, expiresIn, profileId) => ({
  profile_id: profileId,
  threads_user_id: validated.id,
  username: validated.username,
  display_name: null,
  scopes: validated.scopes,
  token_ciphertext: encrypted.ciphertext,
  token_nonce: encrypted.nonce,
  token_key_version: encrypted.key_version,
  token_expires_at: new Date(now.getTime() + expiresIn * 1000).toISOString(),
  now: now.toISOString(),
});

export async function handleHostedThreadsProfiles(request, env, options = {}) {
  const url = new URL(request.url);
  if (!url.pathname.startsWith("/api/threads/")) return null;
  if (url.pathname.startsWith("/api/threads/media/")) return null;
  const now = () => new Date((options.now ?? Date.now)());
  const randomBytes = options.randomBytes ?? ((size) => crypto.getRandomValues(new Uint8Array(size)));

  try {
    if (request.method === "GET" && url.pathname === "/api/threads/oauth/callback") {
      const graph = options.graphClient ?? createThreadsGraphClient(env);
      const vault = options.tokenVault ?? createThreadsTokenVaultFromEnv(env);
      const code = requiredString(url.searchParams.get("code"), "code");
      const state = requiredString(url.searchParams.get("state"), "state");
      const stateSha256 = await sha256(state);
      const stateOwner = await env.DB.prepare(
        "SELECT account_id FROM hosted_threads_oauth_states WHERE state_sha256 = ?",
      ).bind(stateSha256).first();
      if (!stateOwner) {
        throw new ThreadsProfilesStoreError(
          "THREADS_OAUTH_STATE_INVALID",
          409,
          "OAuth 인증 요청이 만료되었거나 이미 사용되었습니다.",
        );
      }
      const store = options.storeFactory?.(stateOwner.account_id)
        ?? createThreadsProfilesStore(env.DB, stateOwner.account_id);
      const consumed = await store.consumeOAuthState(stateSha256, now().toISOString());
      const shortLived = await graph.exchangeAuthorizationCode(code);
      const longLived = await graph.exchangeLongLivedToken(shortLived.accessToken);
      const validated = await graph.getValidatedProfile(longLived.accessToken);
      const encrypted = await vault.encrypt(longLived.accessToken);
      const profileId = consumed.reconnect_profile_id ?? `threads_${crypto.randomUUID()}`;
      const payload = await profilePayload(
        validated,
        encrypted,
        now(),
        longLived.expiresIn,
        profileId,
      );
      const profile = consumed.reconnect_profile_id
        ? await store.reconnectProfile(consumed.reconnect_profile_id, payload)
        : await store.connectProfile(payload);
      return threadsOAuthCallbackResponse(request, consumed.redirect_uri, profile);
    }

    authorize(request, env);
    const store = options.storeFactory?.(accountId(env))
      ?? createThreadsProfilesStore(env.DB, accountId(env));
    if (request.method === "GET" && url.pathname === "/api/threads/profiles") {
      return json({ profiles: await store.listProfiles() });
    }
    if (request.method === "GET" && url.pathname === "/api/threads/settings") {
      return json(await store.settings());
    }
    if (request.method === "POST" && url.pathname === "/api/threads/oauth/start") {
      const body = await readJson(request);
      const state = newState(randomBytes);
      const createdAt = now();
      await store.createOAuthState({
        oauth_state_id: crypto.randomUUID(),
        state_sha256: await sha256(state),
        reconnect_profile_id: body.reconnect_profile_id ?? null,
        redirect_uri: requiredString(env.THREADS_REDIRECT_URI, "THREADS_REDIRECT_URI"),
        created_at: createdAt.toISOString(),
        expires_at: new Date(createdAt.getTime() + STATE_TTL_MS).toISOString(),
      });
      return json({ authorization_url: authorizationUrl(env, state) }, 201);
    }
    const reconnect = url.pathname.match(/^\/api\/threads\/profiles\/([^/]+)\/reconnect$/u);
    if (request.method === "POST" && reconnect) {
      const state = newState(randomBytes);
      const createdAt = now();
      const profileId = decodeURIComponent(reconnect[1]);
      await store.createOAuthState({
        oauth_state_id: crypto.randomUUID(),
        state_sha256: await sha256(state),
        reconnect_profile_id: profileId,
        redirect_uri: requiredString(env.THREADS_REDIRECT_URI, "THREADS_REDIRECT_URI"),
        created_at: createdAt.toISOString(),
        expires_at: new Date(createdAt.getTime() + STATE_TTL_MS).toISOString(),
      });
      return json({ authorization_url: authorizationUrl(env, state) }, 201);
    }
    const setDefault = url.pathname.match(/^\/api\/threads\/profiles\/([^/]+)\/default$/u);
    if (request.method === "POST" && setDefault) {
      const body = await readJson(request);
      return json(await store.setDefault(
        decodeURIComponent(setDefault[1]),
        expectedRevision(body.expected_revision),
        now().toISOString(),
      ));
    }
    const disconnect = url.pathname.match(/^\/api\/threads\/profiles\/([^/]+)\/disconnect$/u);
    if (request.method === "POST" && disconnect) {
      const body = await readJson(request);
      return json(await store.disconnectProfile(
        decodeURIComponent(disconnect[1]),
        expectedRevision(body.expected_revision),
        now().toISOString(),
      ));
    }
    if (request.method === "PATCH" && url.pathname === "/api/threads/settings") {
      const body = await readJson(request);
      if (typeof body.enabled !== "boolean") {
        throw new ThreadsProfilesStoreError(
          "THREADS_REQUEST_INVALID",
          400,
          "enabled 값이 필요합니다.",
        );
      }
      return json(await store.updateSettings(
        body.enabled,
        expectedRevision(body.expected_revision),
        THREADS_REQUIRED_SCOPES,
        now().toISOString(),
      ));
    }
    return null;
  } catch (error) {
    const mapped = mapError(error);
    return json({ error: mapped.code, detail: mapped.message }, mapped.status);
  }
}
