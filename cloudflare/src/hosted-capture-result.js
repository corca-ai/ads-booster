export const MAX_HOSTED_CAPTURE_CALLBACK_BYTES = 24 * 1024 * 1024;
export const MAX_HOSTED_CAPTURE_IMAGE_BYTES = 16 * 1024 * 1024;

export async function prepareHostedCaptureResult(result) {
  const status = result?.status;
  if (!["succeeded", "failed", "unknown_side_effect"].includes(status)) {
    throw new HostedCaptureResultError(400, "invalid hosted capture result status");
  }
  if (status !== "succeeded") {
    return { status, image: null, image_digest: null, stored_result: result };
  }
  const output = result?.output;
  if (
    output?.content_type !== "image/png" ||
    output.capture_source !== "native_appium" ||
    output.native_export_binding_verified !== true ||
    typeof output.image_base64 !== "string" ||
    typeof output.image_sha256 !== "string" ||
    !/^[0-9a-f]{64}$/.test(output.image_sha256)
  ) {
    throw new HostedCaptureResultError(
      400,
      "successful hosted capture requires a verified digest-backed PNG",
    );
  }
  let image;
  try {
    image = Uint8Array.from(atob(output.image_base64), (character) => character.charCodeAt(0));
  } catch (error) {
    throw new HostedCaptureResultError(400, "hosted capture image is not valid base64");
  }
  if (image.byteLength < 1 || image.byteLength > MAX_HOSTED_CAPTURE_IMAGE_BYTES) {
    throw new HostedCaptureResultError(
      413,
      `hosted capture image exceeds ${MAX_HOSTED_CAPTURE_IMAGE_BYTES} bytes`,
    );
  }
  const imageDigest = await sha256Bytes(image);
  if (imageDigest !== output.image_sha256) {
    throw new HostedCaptureResultError(409, "hosted capture image digest does not match callback");
  }
  if (output.generation_provenance !== undefined) {
    await validateGenerationProvenance(output.generation_provenance);
  }
  return {
    status,
    image,
    image_digest: imageDigest,
    stored_result: {
      ...result,
      output: {
        ...output,
        image_base64: undefined,
        byte_size: image.byteLength,
      },
    },
  };
}

async function validateGenerationProvenance(provenance) {
  const digest = (value) => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
  if (
    !provenance || typeof provenance !== "object" || Array.isArray(provenance) ||
    provenance.schema_version !== "trace.hosted-generation-provenance.v1" ||
    !digest(provenance.plan_sha256) || !digest(provenance.background_sha256) ||
    !provenance.plan || typeof provenance.plan !== "object" || Array.isArray(provenance.plan) ||
    provenance.plan.schema_version !== "trace.wallpaper-plan.v1" ||
    typeof provenance.plan.request_id !== "string" ||
    typeof provenance.plan.time_zone !== "string" ||
    typeof provenance.plan.background_query !== "string" ||
    !Array.isArray(provenance.plan.reference_ids) ||
    !provenance.plan.style || typeof provenance.plan.style !== "object" ||
    !Array.isArray(provenance.plan.rows) || provenance.plan.rows.length < 1 ||
    !provenance.background || typeof provenance.background !== "object" ||
    Array.isArray(provenance.background) ||
    !sameKeys(provenance.background, [
      "artifact_sha256", "image_url", "provider", "query", "schema_version", "source_url",
    ]) ||
    provenance.background.schema_version !== "trace.background-search.v1" ||
    !digest(provenance.background.artifact_sha256) ||
    !isHttpUrl(provenance.background.image_url) ||
    !isHttpUrl(provenance.background.source_url) ||
    typeof provenance.background.provider !== "string" || !provenance.background.provider ||
    typeof provenance.background.query !== "string" || !provenance.background.query
  ) {
    throw new HostedCaptureResultError(400, "hosted capture generation provenance is invalid");
  }
  const [planDigest, backgroundDigest] = await Promise.all([
    sha256Bytes(new TextEncoder().encode(canonicalJson(provenance.plan))),
    sha256Bytes(new TextEncoder().encode(canonicalJson(provenance.background))),
  ]);
  if (planDigest !== provenance.plan_sha256 || backgroundDigest !== provenance.background_sha256) {
    throw new HostedCaptureResultError(409, "hosted capture generation provenance digest changed");
  }
}

export function assertHostedCaptureLinkage(output, task) {
  const provenanceRequestId = output?.generation_provenance?.plan?.request_id;
  if (
    output?.pipeline !== "hosted_workspace_capture_v1" ||
    output.candidate_id !== task?.candidate_id ||
    output.candidate_revision !== task?.candidate_revision ||
    (provenanceRequestId !== undefined && provenanceRequestId !== task?.task_id)
  ) {
    throw new HostedCaptureResultError(409, "hosted capture result does not match candidate task");
  }
}

function sameKeys(value, expected) {
  return Object.keys(value).sort().join("\u0000") === expected.join("\u0000");
}

function isHttpUrl(value) {
  if (typeof value !== "string" || !value) return false;
  try {
    return ["http:", "https:"].includes(new URL(value).protocol);
  } catch (error) {
    return false;
  }
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map(
      (key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`,
    ).join(",")}}`;
  }
  return JSON.stringify(value);
}

export class HostedCaptureResultError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function sha256Bytes(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((item) => item.toString(16).padStart(2, "0"))
    .join("");
}
