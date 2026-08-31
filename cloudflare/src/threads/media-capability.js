const CAPABILITY_TTL_MS = 10 * 60_000;
const DIGEST_PATTERN = /^[0-9a-f]{64}$/u;
const SIGNATURE_PATTERN = /^[0-9a-f]{64}$/u;

export class ThreadsMediaCapabilityError extends Error {
  constructor(code, status, message) {
    super(message);
    this.name = "ThreadsMediaCapabilityError";
    this.code = code;
    this.status = status;
  }
}

const configuredKey = (env) => {
  const value = env?.THREADS_MEDIA_SIGNING_KEY;
  if (typeof value !== "string" || new TextEncoder().encode(value).byteLength < 32) {
    throw new ThreadsMediaCapabilityError(
      "THREADS_MEDIA_CONFIG_INVALID",
      503,
      "Threads media signing key is missing or invalid",
    );
  }
  return value;
};

const canonical = (capability) => [
  "v1",
  capability.accountId,
  capability.publicationId,
  capability.digest,
  String(capability.expires),
].join("\n");

const signature = async (keyText, value) => {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(keyText),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const bytes = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(value));
  return [...new Uint8Array(bytes)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
};

const constantTimeEqual = (left, right) => {
  if (left.length !== right.length) return false;
  let mismatch = 0;
  for (let index = 0; index < left.length; index += 1) {
    mismatch |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return mismatch === 0;
};

const requireOrigin = (env) => {
  try {
    const origin = new URL(env?.THREADS_PUBLIC_ORIGIN);
    if (origin.protocol !== "https:") throw new Error();
    return origin.origin;
  } catch {
    throw new ThreadsMediaCapabilityError(
      "THREADS_MEDIA_CONFIG_INVALID",
      503,
      "Threads public origin is missing or invalid",
    );
  }
};

export async function createThreadsMediaUrl(env, publication, options = {}) {
  if (!DIGEST_PATTERN.test(publication.image_sha256_snapshot)) {
    throw new ThreadsMediaCapabilityError(
      "THREADS_MEDIA_DIGEST_INVALID",
      500,
      "Threads publication image digest is invalid",
    );
  }
  const now = (options.now ?? Date.now)();
  const capability = {
    accountId: publication.account_id,
    publicationId: publication.publication_id,
    digest: publication.image_sha256_snapshot,
    expires: now + CAPABILITY_TTL_MS,
  };
  const url = new URL(
    `/api/threads/media/${encodeURIComponent(capability.publicationId)}`,
    requireOrigin(env),
  );
  url.searchParams.set("account_id", capability.accountId);
  url.searchParams.set("digest", capability.digest);
  url.searchParams.set("expires", String(capability.expires));
  url.searchParams.set("signature", await signature(configuredKey(env), canonical(capability)));
  return url.toString();
}

const sha256 = async (bytes) => {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
};

export async function handleThreadsMediaRequest(request, env, options = {}) {
  const url = new URL(request.url);
  const route = url.pathname.match(/^\/api\/threads\/media\/([^/]+)$/u);
  if (!route) return null;
  try {
    if (request.method !== "GET") {
      throw new ThreadsMediaCapabilityError("THREADS_MEDIA_METHOD_INVALID", 405, "method not allowed");
    }
    const publicationId = decodeURIComponent(route[1]);
    const accountId = url.searchParams.get("account_id") ?? "";
    const digest = url.searchParams.get("digest") ?? "";
    const expires = Number(url.searchParams.get("expires"));
    const suppliedSignature = url.searchParams.get("signature") ?? "";
    const now = (options.now ?? Date.now)();
    if (
      !accountId
      || !DIGEST_PATTERN.test(digest)
      || !Number.isSafeInteger(expires)
      || expires <= now
      || expires > now + CAPABILITY_TTL_MS
      || !SIGNATURE_PATTERN.test(suppliedSignature)
    ) {
      throw new ThreadsMediaCapabilityError("THREADS_MEDIA_CAPABILITY_INVALID", 403, "media capability is invalid");
    }
    const capability = { accountId, publicationId, digest, expires };
    const expected = await signature(configuredKey(env), canonical(capability));
    if (!constantTimeEqual(expected, suppliedSignature)) {
      throw new ThreadsMediaCapabilityError("THREADS_MEDIA_CAPABILITY_INVALID", 403, "media capability is invalid");
    }
    const publication = await env.DB.prepare(
      `SELECT image_key_snapshot, image_sha256_snapshot
       FROM hosted_threads_publications WHERE account_id = ? AND publication_id = ?`,
    ).bind(accountId, publicationId).first();
    if (!publication || publication.image_sha256_snapshot !== digest) {
      throw new ThreadsMediaCapabilityError("THREADS_MEDIA_NOT_FOUND", 404, "media not found");
    }
    const object = await env.ARTIFACTS.get(publication.image_key_snapshot);
    if (!object) throw new ThreadsMediaCapabilityError("THREADS_MEDIA_NOT_FOUND", 404, "media not found");
    const bytes = await object.arrayBuffer();
    if (await sha256(bytes) !== digest) {
      throw new ThreadsMediaCapabilityError("THREADS_MEDIA_DIGEST_MISMATCH", 409, "media digest mismatch");
    }
    return new Response(bytes, {
      headers: {
        "cache-control": "private, no-store",
        "content-type": "image/png",
        etag: digest,
      },
    });
  } catch (error) {
    const failure = error instanceof ThreadsMediaCapabilityError
      ? error
      : new ThreadsMediaCapabilityError("THREADS_MEDIA_INTERNAL_ERROR", 500, "media request failed");
    return Response.json(
      { error: failure.code },
      { status: failure.status, headers: { "cache-control": "no-store" } },
    );
  }
}
