const MAX_REFERENCE_BYTES = 1024 * 1024;
const MAX_REDIRECTS = 5;
const FETCH_TIMEOUT_MS = 10_000;
const ALLOWED_CONTENT_TYPES = new Set([
  "application/json",
  "application/pdf",
  "text/html",
  "text/plain",
]);

export async function verifyReferenceSources(
  snapshot,
  snapshotSha256,
  fetcher = globalThis.fetch,
  verifiedAt = new Date().toISOString(),
) {
  if (typeof fetcher !== "function") {
    throw new ReferenceVerificationError("reference fetcher is unavailable");
  }
  const sources = requireArray(snapshot?.sources, "reference sources", 2, 16);
  const sourceHosts = new Set();
  const finalHosts = new Set();
  const receipts = [];
  for (const source of sources) {
    const requested = safePublicHttpsUrl(source?.url);
    sourceHosts.add(normalizedHost(requested.hostname));
    const { response, finalUrl } = await fetchPublicSource(fetcher, requested);
    finalHosts.add(normalizedHost(finalUrl.hostname));
    const contentType = normalizedContentType(response.headers?.get?.("content-type"));
    if (!ALLOWED_CONTENT_TYPES.has(contentType)) {
      throw new ReferenceVerificationError("reference source content type is unsupported");
    }
    const declaredLength = Number(response.headers?.get?.("content-length"));
    if (Number.isFinite(declaredLength) && declaredLength > MAX_REFERENCE_BYTES) {
      throw new ReferenceVerificationError("reference source is too large");
    }
    const content = await readBoundedBody(response);
    if (content.byteLength === 0) {
      throw new ReferenceVerificationError("reference source body size is invalid");
    }
    const contentSha256 = await sha256Bytes(content);
    const sourceId = requiredId(source?.source_id, "reference source ID");
    receipts.push({
      schema_version: "trace.reference-source-receipt.v1",
      receipt_id: `source-receipt-${(await sha256Text(`${sourceId}:${contentSha256}`)).slice(0, 48)}`,
      source_id: sourceId,
      requested_url: requested.href,
      final_url: finalUrl.href,
      http_status: response.status,
      content_type: contentType,
      content_sha256: contentSha256,
      byte_length: content.byteLength,
      fetched_at: verifiedAt,
    });
  }
  if (sourceHosts.size < 2 || finalHosts.size < 2) {
    throw new ReferenceVerificationError("reference research needs two independent hosts");
  }
  return {
    schema_version: "trace.reference-verification.v1",
    snapshot_id: requiredId(snapshot?.snapshot_id, "reference snapshot ID"),
    snapshot_sha256: requiredSha256(snapshotSha256, "reference snapshot SHA"),
    receipts,
    verified_at: verifiedAt,
  };
}

export class ReferenceVerificationError extends Error {}

async function fetchPublicSource(fetcher, requested) {
  let current = requested;
  for (let redirectCount = 0; redirectCount <= MAX_REDIRECTS; redirectCount += 1) {
    let response;
    try {
      const options = {
        method: "GET",
        redirect: "manual",
        headers: { accept: "text/html,text/plain,application/json,application/pdf;q=0.8" },
      };
      if (typeof globalThis.AbortSignal?.timeout === "function") {
        options.signal = globalThis.AbortSignal.timeout(FETCH_TIMEOUT_MS);
      }
      response = await fetcher(current.href, options);
    } catch {
      throw new ReferenceVerificationError("reference source could not be fetched");
    }
    if (!response) {
      throw new ReferenceVerificationError("reference source did not return a response");
    }
    if ([301, 302, 303, 307, 308].includes(response.status)) {
      if (redirectCount === MAX_REDIRECTS) {
        throw new ReferenceVerificationError("reference source redirected too many times");
      }
      const location = response.headers?.get?.("location");
      if (!location) {
        throw new ReferenceVerificationError("reference source redirect is invalid");
      }
      current = safePublicHttpsUrl(new URL(location, current).href);
      continue;
    }
    if (response.ok !== true) {
      throw new ReferenceVerificationError("reference source did not return a successful response");
    }
    const responseUrl = response.url ? safePublicHttpsUrl(response.url) : current;
    return { response, finalUrl: responseUrl };
  }
  throw new ReferenceVerificationError("reference source redirect is invalid");
}

async function readBoundedBody(response) {
  const reader = response.body?.getReader?.();
  if (!reader) {
    const content = new Uint8Array(await response.arrayBuffer());
    if (content.byteLength > MAX_REFERENCE_BYTES) {
      throw new ReferenceVerificationError("reference source body size is invalid");
    }
    return content;
  }
  const chunks = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const chunk = value instanceof Uint8Array ? value : new Uint8Array(value);
    total += chunk.byteLength;
    if (total > MAX_REFERENCE_BYTES) {
      await reader.cancel();
      throw new ReferenceVerificationError("reference source body size is invalid");
    }
    chunks.push(chunk);
  }
  const content = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    content.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return content;
}

function safePublicHttpsUrl(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new ReferenceVerificationError("reference source URL is invalid");
  }
  if (
    url.protocol !== "https:"
    || url.username
    || url.password
    || (url.port && url.port !== "443")
    || unsafeHostname(url.hostname)
  ) {
    throw new ReferenceVerificationError("reference source URL is not public HTTPS");
  }
  url.hash = "";
  return url;
}

function unsafeHostname(value) {
  const hostname = value.toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
  if (!hostname || hostname === "localhost" || hostname.endsWith(".localhost")) return true;
  if (hostname.includes(":")) return true;
  const match = hostname.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (!match) return false;
  const octets = match.slice(1).map(Number);
  if (octets.some((octet) => octet > 255)) return true;
  return octets[0] === 10
    || octets[0] === 127
    || (octets[0] === 100 && octets[1] >= 64 && octets[1] <= 127)
    || (octets[0] === 169 && octets[1] === 254)
    || (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31)
    || (octets[0] === 192 && octets[1] === 0 && [0, 2].includes(octets[2]))
    || (octets[0] === 192 && octets[1] === 168)
    || (octets[0] === 198 && [18, 19].includes(octets[1]))
    || (octets[0] === 198 && octets[1] === 51 && octets[2] === 100)
    || (octets[0] === 203 && octets[1] === 0 && octets[2] === 113)
    || octets[0] === 0
    || octets[0] >= 224;
}

function normalizedHost(value) {
  return value.toLowerCase().replace(/^www\./, "").replace(/\.$/, "");
}

function normalizedContentType(value) {
  if (typeof value !== "string") return "";
  return value.split(";", 1)[0].trim().toLowerCase();
}

function requireArray(value, name, minimum, maximum) {
  if (!Array.isArray(value) || value.length < minimum || value.length > maximum) {
    throw new ReferenceVerificationError(`${name} is invalid`);
  }
  return value;
}

function requiredId(value, name) {
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value)) {
    throw new ReferenceVerificationError(`${name} is invalid`);
  }
  return value;
}

function requiredSha256(value, name) {
  if (typeof value !== "string" || !/^[a-f0-9]{64}$/.test(value)) {
    throw new ReferenceVerificationError(`${name} is invalid`);
  }
  return value;
}

async function sha256Text(value) {
  return sha256Bytes(new TextEncoder().encode(value));
}

async function sha256Bytes(value) {
  const digest = await crypto.subtle.digest("SHA-256", value);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
