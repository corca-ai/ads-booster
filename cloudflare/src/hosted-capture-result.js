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
