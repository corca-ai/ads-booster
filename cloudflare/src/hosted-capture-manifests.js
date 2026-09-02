import { HttpError } from "./http-error.js";
import {
  assertCurrentCapabilityBinding,
  MarketingCapabilityError,
} from "./marketing-adapter-capabilities.js";

export async function prepareMarketingCaptureManifests(
  env,
  task,
  imageKey,
  imageDigest,
  output,
) {
  const result = await env.DB.prepare(
    `SELECT assignment.assignment_id, assignment.campaign_id, assignment.treatment_id,
            request.request_id, request.capability_id, request.request_json,
            request.request_sha256, request.capability_binding_sha256,
            request.state AS request_state
     FROM hosted_workspace_candidates AS candidate
     LEFT JOIN hosted_marketing_post_assignments AS assignment
       ON assignment.assignment_id = candidate.marketing_assignment_id
     LEFT JOIN hosted_marketing_artifact_requests AS request
       ON request.treatment_id = assignment.treatment_id
      AND request.capability_id = 'capture.native_png'
     WHERE candidate.account_id = ? AND candidate.candidate_id = ?
       AND candidate.capture_task_id = ?`,
  ).bind(task.account_id, task.candidate_id, task.task_id).all();
  const provenance = captureArtifactProvenance(output, imageDigest);
  const manifests = [];
  for (const row of result.results) {
    if (!row.assignment_id) continue;
    if (!row.request_id || !["approved", "succeeded"].includes(row.request_state)) {
      throw new MarketingCapabilityError(
        "marketing candidate has no approved native capture request",
      );
    }
    await assertCurrentCapabilityBinding(
      env.DB,
      task.account_id,
      row.capability_id,
      row.capability_binding_sha256,
    );
    const request = JSON.parse(row.request_json);
    const manifestId = `capture-${(
      await sha256(`${task.task_id}:${row.request_id}`)
    ).slice(0, 48)}`;
    manifests.push({
      schema_version: "trace.artifact-manifest.v1",
      manifest_id: manifestId,
      campaign_id: row.campaign_id,
      assignment_id: row.assignment_id,
      treatment_id: row.treatment_id,
      request_id: row.request_id,
      capability_id: row.capability_id,
      capability_binding_sha256: row.capability_binding_sha256,
      artifact_uri: `r2:${imageKey}`,
      artifact_sha256: imageDigest,
      input_sha256: row.request_sha256,
      execution_id: task.task_id,
      claim_ids: request.claim_ids ?? [],
      evidence_ids: [],
      capture_provenance: provenance,
      created_at: task.created_at,
    });
  }
  return manifests;
}

export async function recordMarketingCaptureManifests(env, manifests) {
  for (const manifest of manifests) {
    const manifestJson = canonicalJson(manifest);
    const manifestSha256 = await sha256(manifestJson);
    const existing = await env.DB.prepare(
      `SELECT manifest_sha256 FROM hosted_marketing_artifact_manifests WHERE manifest_id = ?`,
    ).bind(manifest.manifest_id).first();
    if (existing && existing.manifest_sha256 !== manifestSha256) {
      throw new HttpError(409, "capture artifact manifest changed after creation");
    }
    if (!existing) {
      await env.DB.batch([
        env.DB.prepare(
          `INSERT INTO hosted_marketing_artifact_manifests
            (manifest_id, campaign_id, assignment_id, treatment_id, request_id, schema_version,
             manifest_json, manifest_sha256, artifact_uri, artifact_sha256,
             input_sha256, capability_binding_sha256, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        ).bind(
          manifest.manifest_id,
          manifest.campaign_id,
          manifest.assignment_id,
          manifest.treatment_id,
          manifest.request_id,
          manifest.schema_version,
          manifestJson,
          manifestSha256,
          manifest.artifact_uri,
          manifest.artifact_sha256,
          manifest.input_sha256,
          manifest.capability_binding_sha256,
          manifest.created_at,
        ),
        env.DB.prepare(
          `UPDATE hosted_marketing_artifact_requests
           SET state = 'succeeded', updated_at = ?
           WHERE request_id = ? AND state IN ('approved', 'executing', 'succeeded')`,
        ).bind(manifest.created_at, manifest.request_id),
      ]);
    }
  }
}

function captureArtifactProvenance(output, imageDigest) {
  if (
    output.capture_source === "native_appium"
    && output.artifact_role === "trace_wallpaper"
    && output.image_postprocess_source === "none"
    && output.native_image_sha256 === imageDigest
  ) {
    return {
      schema_version: "trace.capture-artifact-provenance.v1",
      capture_source: "native_appium",
      artifact_role: "trace_wallpaper",
      source_trace_artifact_sha256: imageDigest,
    };
  }
  if (
    output.capture_source === "imagen_ios_ui"
    && output.artifact_role === "imagen_ios_ui"
    && output.image_postprocess_source === "imagen_ios_ui"
    && output.imagegen_ui_layer_verified === true
    && typeof output.source_trace_artifact_sha256 === "string"
    && /^[a-f0-9]{64}$/.test(output.source_trace_artifact_sha256)
    && output.native_image_sha256 === output.source_trace_artifact_sha256
  ) {
    return {
      schema_version: "trace.capture-artifact-provenance.v1",
      capture_source: "imagen_ios_ui",
      artifact_role: "imagen_ios_ui",
      source_trace_artifact_sha256: output.source_trace_artifact_sha256,
    };
  }
  throw new HttpError(409, "hosted capture provenance is invalid for marketing artifact");
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) =>
      `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

async function sha256(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}
