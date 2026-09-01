import { HttpError } from "./http-error.js";
import { assertHostedCallbackTransport, reserveWorkerTaskCallback } from "./mac-workers.js";
import { MARKETING_JUDGMENT_PIPELINE } from "./marketing-agent.js";

export async function receiveHostedCandidateMaterializationCallback(
  env,
  task,
  callback,
  worker = null,
) {
  assertHostedCallbackTransport(task, worker);
  if (
    task.run_id !== callback.run_id
    || task.account_id !== callback.account_id
    || callback.kind !== "marketing_judgment"
    || callback.callback_id !== `${callback.task_id}:completed`
  ) {
    throw new HttpError(409, "candidate materialization callback scope is invalid");
  }
  const status = callback.result?.status;
  if (!["succeeded", "failed", "unknown_side_effect"].includes(status)) {
    throw new HttpError(400, "candidate materialization status is invalid");
  }
  const storedResultJson = JSON.stringify(callback.result);
  if (task.callback_id) {
    if (task.callback_id !== callback.callback_id || task.result_json !== storedResultJson) {
      throw new HttpError(409, "candidate materialization callback changed");
    }
    return { accepted: true, duplicate: true };
  }
  const payload = publishedPayload(task);
  const reservation = await env.DB.prepare(
    `SELECT reservation.assignment_id, reservation.campaign_id, reservation.experiment_id,
            reservation.hypothesis_id, reservation.treatment_id,
            reservation.eligible_block_id, reservation.state,
            campaign.account_id, campaign.mode, campaign.state AS campaign_state,
            campaign.projection_revision,
            packet.packet_json, packet.packet_sha256,
            brief.brief_sha256, plan.plan_sha256, plan.state AS plan_state,
            treatment.treatment_json, treatment.treatment_sha256
     FROM hosted_marketing_materialization_reservations AS reservation
     JOIN hosted_marketing_campaigns AS campaign
       ON campaign.campaign_id = reservation.campaign_id
     JOIN hosted_marketing_feature_packets AS packet
       ON packet.packet_id = campaign.feature_packet_id
      AND packet.packet_sha256 = campaign.feature_packet_sha256
     JOIN hosted_marketing_product_truth_approvals AS truth
       ON truth.packet_id = packet.packet_id AND truth.packet_sha256 = packet.packet_sha256
      AND truth.decision = 'approved'
     JOIN hosted_marketing_strategy_briefs AS brief ON brief.campaign_id = campaign.campaign_id
     JOIN hosted_marketing_media_plans AS plan ON plan.campaign_id = campaign.campaign_id
     JOIN hosted_marketing_creative_treatments AS treatment
       ON treatment.treatment_id = reservation.treatment_id AND treatment.plan_id = plan.plan_id
     WHERE reservation.task_id = ? AND campaign.account_id = ?
       AND EXISTS (
         SELECT 1 FROM hosted_marketing_approval_grants AS grant
         WHERE grant.campaign_id = campaign.campaign_id
           AND grant.scope = 'creative' AND grant.target_kind = 'media_plan'
           AND grant.target_id = plan.plan_id AND grant.target_sha256 = plan.plan_sha256
           AND grant.decision = 'approved'
       )`,
  ).bind(task.task_id, task.account_id).first();
  if (
    !reservation
    || reservation.state !== "queued"
    || reservation.mode !== "assisted"
    || !["creative_planned", "awaiting_review"].includes(reservation.campaign_state)
    || reservation.plan_state !== "approved"
    || payload.campaign_id !== reservation.campaign_id
    || payload.assignment_id !== reservation.assignment_id
    || payload.eligible_block_id !== reservation.eligible_block_id
    || payload.feature_packet_sha256 !== reservation.packet_sha256
    || payload.strategy_brief_sha256 !== reservation.brief_sha256
    || payload.media_plan_sha256 !== reservation.plan_sha256
    || payload.treatment_sha256 !== reservation.treatment_sha256
  ) {
    throw new HttpError(409, "candidate materialization reservation is stale or invalid");
  }
  const now = new Date().toISOString();
  if (status !== "succeeded") {
    if (worker) {
      const claimed = await reserveWorkerTaskCallback(
        env.DB,
        worker,
        task,
        callback.callback_id,
        storedResultJson,
      );
      if (claimed.duplicate) return { accepted: true, duplicate: true };
    }
    const results = await env.DB.batch([
      env.DB.prepare(
        `UPDATE hosted_marketing_materialization_reservations
         SET state = 'failed', updated_at = ?
         WHERE task_id = ? AND state = 'queued'`,
      ).bind(now, task.task_id),
      completionStatement(env, task, callback, worker, status, storedResultJson, now),
    ]);
    if (results.some((result) => result?.meta?.changes !== 1)) {
      throw new HttpError(409, "candidate materialization failure lost its state race");
    }
    return { accepted: true, duplicate: false, state: "failed" };
  }
  const output = requireObject(callback.result?.output, "candidate materialization output");
  const candidate = normalizeCandidate(output.candidate);
  const receipt = requireObject(output.context_receipt, "candidate context receipt");
  const candidateSha256 = await canonicalSha256(candidate);
  const receiptSha256 = await canonicalSha256(receipt);
  const treatment = JSON.parse(reservation.treatment_json);
  if (
    output.pipeline !== MARKETING_JUDGMENT_PIPELINE
    || output.judgment !== "candidate_materialization"
    || output.campaign_id !== reservation.campaign_id
    || output.assignment_id !== reservation.assignment_id
    || output.eligible_block_id !== reservation.eligible_block_id
    || output.treatment_id !== reservation.treatment_id
    || output.tool_actions_created !== 0
    || output.candidate_sha256 !== candidateSha256
    || output.context_receipt_sha256 !== receiptSha256
    || receipt.receipt_id !== task.task_id
    || receipt.campaign_id !== reservation.campaign_id
    || receipt.feature_packet_sha256 !== reservation.packet_sha256
    || candidate.country !== payload.account?.country
    || candidate.image_inputs.language !== payload.account?.language
    || !sameSet(candidate.claim_ids, treatment.claim_ids)
  ) {
    throw new HttpError(409, "candidate materialization output binding is invalid");
  }
  const candidateId = `marketing-${candidateSha256.slice(0, 48)}`;
  const candidateContent = {
    caption: candidate.caption,
    hypothesis: candidate.hypothesis,
    appium_prompt: candidate.appium_prompt,
    image_inputs: candidate.image_inputs,
    context_snapshot: null,
    persona_id: null,
  };
  const candidateContentSha256 = await canonicalSha256(candidateContent);
  const assignment = {
    assignment_id: reservation.assignment_id,
    campaign_id: reservation.campaign_id,
    experiment_id: reservation.experiment_id,
    hypothesis_id: reservation.hypothesis_id,
    treatment_id: reservation.treatment_id,
    candidate_id: candidateId,
    candidate_revision: 1,
    candidate_content_sha256: candidateContentSha256,
    eligible_block_id: reservation.eligible_block_id,
    media_plan_sha256: reservation.plan_sha256,
    assigned_at: now,
  };
  const assignmentSha256 = await canonicalSha256(assignment);
  const requests = await env.DB.prepare(
    `SELECT request_id, capability_id, request_json, request_sha256
     FROM hosted_marketing_artifact_requests
     WHERE campaign_id = ? AND treatment_id = ?`,
  ).bind(reservation.campaign_id, reservation.treatment_id).all();
  if (worker) {
    const claimed = await reserveWorkerTaskCallback(
      env.DB,
      worker,
      task,
      callback.callback_id,
      storedResultJson,
    );
    if (claimed.duplicate) return { accepted: true, duplicate: true };
  }
  const statements = [
    env.DB.prepare(
      `INSERT INTO hosted_workspace_candidates
        (candidate_id, account_id, source, country, topic, caption, hypothesis,
         refs_json, principles_json, appium_prompt, image_inputs_json, ai_verdict,
         context_profile_id, context_snapshot_json, posting_slot, generation_batch_id,
         generation_prompt_version, generation_prompt_sha256, generation_model,
         feedback_rules_json, persona_id, generation_provenance_json,
         threads_profile_id, status, revision, created_at, updated_at)
       VALUES (?, ?, 'auto', ?, ?, ?, ?, '[]', '[]', ?, ?, ?, NULL, NULL, ?, ?,
               'trace.evidence-bound-candidate.v1', ?, 'codex_cli', '[]', NULL, ?, NULL,
               'awaiting_review', 1, ?, ?)`,
    ).bind(
      candidateId,
      reservation.account_id,
      candidate.country,
      candidate.topic,
      candidate.caption,
      candidate.hypothesis,
      candidate.appium_prompt,
      canonicalJson(candidate.image_inputs),
      "기계 검수 통과 · approved treatment/claim/locale binding",
      candidate.posting_slot,
      task.task_id,
      receipt.prompt_sha256,
      canonicalJson({
        schema_version: "trace.marketing-candidate-provenance.v1",
        campaign_id: reservation.campaign_id,
        treatment_id: reservation.treatment_id,
        candidate_sha256: candidateSha256,
      }),
      Date.now() / 1000,
      Date.now() / 1000,
    ),
    env.DB.prepare(
      `INSERT INTO hosted_marketing_context_receipts
        (receipt_id, campaign_id, schema_version, receipt_json, receipt_sha256,
         feature_packet_sha256, knowledge_snapshot_sha256, capability_snapshot_sha256,
         prompt_sha256, output_schema_sha256, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      receipt.receipt_id,
      reservation.campaign_id,
      receipt.schema_version,
      canonicalJson(receipt),
      receiptSha256,
      receipt.feature_packet_sha256,
      receipt.knowledge_snapshot_sha256,
      receipt.capability_snapshot_sha256,
      receipt.prompt_sha256,
      receipt.output_schema_sha256,
      receipt.created_at,
    ),
    env.DB.prepare(
      `INSERT INTO hosted_marketing_post_assignments
        (assignment_id, campaign_id, experiment_id, hypothesis_id, treatment_id,
         candidate_id, candidate_revision, candidate_content_sha256, eligible_block_id,
         assignment_json, assignment_sha256, assigned_at)
       VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)`,
    ).bind(
      reservation.assignment_id,
      reservation.campaign_id,
      reservation.experiment_id,
      reservation.hypothesis_id,
      reservation.treatment_id,
      candidateId,
      candidateContentSha256,
      reservation.eligible_block_id,
      canonicalJson(assignment),
      assignmentSha256,
      now,
    ),
    env.DB.prepare(
      `UPDATE hosted_workspace_candidates
       SET marketing_campaign_id = ?, marketing_experiment_id = ?,
           marketing_hypothesis_id = ?, marketing_treatment_id = ?,
           marketing_assignment_id = ?, marketing_assignment_sha256 = ?,
           revision = 2, updated_at = ?
       WHERE candidate_id = ? AND account_id = ? AND revision = 1
         AND marketing_assignment_id IS NULL`,
    ).bind(
      reservation.campaign_id,
      reservation.experiment_id,
      reservation.hypothesis_id,
      reservation.treatment_id,
      reservation.assignment_id,
      assignmentSha256,
      Date.now() / 1000,
      candidateId,
      reservation.account_id,
    ),
  ];
  for (const request of requests.results) {
    if (request.capability_id !== "copy.text") continue;
    const requestValue = JSON.parse(request.request_json);
    const requestIdDigest = await canonicalSha256({ request_id: request.request_id });
    const manifest = {
      schema_version: "trace.artifact-manifest.v1",
      manifest_id: `copy-${candidateSha256.slice(0, 48)}-${requestIdDigest.slice(0, 16)}`,
      campaign_id: reservation.campaign_id,
      assignment_id: reservation.assignment_id,
      treatment_id: reservation.treatment_id,
      request_id: request.request_id,
      capability_id: request.capability_id,
      artifact_uri: `artifact:candidate/${candidateId}`,
      artifact_sha256: candidateSha256,
      input_sha256: request.request_sha256,
      execution_id: task.task_id,
      claim_ids: requestValue.claim_ids ?? [],
      evidence_ids: [],
      created_at: now,
    };
    statements.push(
      env.DB.prepare(
        `INSERT INTO hosted_marketing_artifact_manifests
          (manifest_id, campaign_id, assignment_id, treatment_id, request_id, schema_version,
           manifest_json, manifest_sha256, artifact_uri, artifact_sha256, input_sha256, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ).bind(
        manifest.manifest_id,
        reservation.campaign_id,
        reservation.assignment_id,
        reservation.treatment_id,
        request.request_id,
        manifest.schema_version,
        canonicalJson(manifest),
        await canonicalSha256(manifest),
        manifest.artifact_uri,
        manifest.artifact_sha256,
        manifest.input_sha256,
        now,
      ),
      env.DB.prepare(
        `UPDATE hosted_marketing_artifact_requests
         SET state = 'succeeded', updated_at = ?
         WHERE request_id = ? AND state IN ('approved', 'succeeded')`,
      ).bind(now, request.request_id),
    );
  }
  const nextRevision = Number(reservation.projection_revision) + 1;
  statements.push(
    env.DB.prepare(
      `UPDATE hosted_marketing_materialization_reservations
       SET state = 'completed', updated_at = ? WHERE task_id = ? AND state = 'queued'`,
    ).bind(now, task.task_id),
    env.DB.prepare(
      `UPDATE hosted_marketing_campaigns
       SET state = 'awaiting_review', projection_revision = ?, updated_at = ?
       WHERE campaign_id = ? AND account_id = ? AND projection_revision = ?
         AND state IN ('creative_planned', 'awaiting_review')`,
    ).bind(
      nextRevision,
      now,
      reservation.campaign_id,
      reservation.account_id,
      reservation.projection_revision,
    ),
    env.DB.prepare(
      `INSERT INTO hosted_marketing_run_events
        (event_id, campaign_id, sequence, prior_revision, resulting_revision, event_type,
         event_json, event_sha256, idempotency_key, causation_id, correlation_id,
         event_time, observed_at, actor_type)
       VALUES (?, ?, ?, ?, ?, 'candidate_materialized', ?, ?, ?, ?, ?, ?, ?, 'codex')`,
    ).bind(
      crypto.randomUUID(),
      reservation.campaign_id,
      nextRevision,
      reservation.projection_revision,
      nextRevision,
      canonicalJson({
        campaign_id: reservation.campaign_id,
        assignment_id: reservation.assignment_id,
        candidate_id: candidateId,
        candidate_sha256: candidateSha256,
      }),
      await canonicalSha256({
        campaign_id: reservation.campaign_id,
        assignment_id: reservation.assignment_id,
        candidate_id: candidateId,
        candidate_sha256: candidateSha256,
      }),
      `campaign:${reservation.campaign_id}:candidate:${candidateSha256}`,
      task.task_id,
      reservation.campaign_id,
      now,
      now,
    ),
    completionStatement(env, task, callback, worker, "succeeded", storedResultJson, now),
  );
  const results = await env.DB.batch(statements);
  const failedStatement = results.findIndex((result) => result?.meta?.changes !== 1);
  if (failedStatement >= 0) {
    throw new HttpError(
      409,
      `candidate materialization batch lost its state race at statement ${failedStatement}`,
    );
  }
  return {
    accepted: true,
    duplicate: false,
    campaign_id: reservation.campaign_id,
    assignment_id: reservation.assignment_id,
    candidate_id: candidateId,
  };
}

function completionStatement(env, task, callback, worker, status, resultJson, now) {
  return worker
    ? env.DB.prepare(
      `UPDATE hosted_workspace_capture_tasks
       SET state = ?, result_json = ?, callback_id = ?, updated_at = ?
       WHERE task_id = ? AND callback_id IS NULL AND worker_id = ? AND lease_id = ?
         AND callback_reservation_id = ?`,
    ).bind(
      status,
      resultJson,
      callback.callback_id,
      now,
      task.task_id,
      worker.worker_id,
      task.lease_id,
      callback.callback_id,
    )
    : env.DB.prepare(
      `UPDATE hosted_workspace_capture_tasks
       SET state = ?, result_json = ?, callback_id = ?, updated_at = ?
       WHERE task_id = ? AND callback_id IS NULL`,
    ).bind(status, resultJson, callback.callback_id, now, task.task_id);
}

function publishedPayload(task) {
  try {
    return requireObject(JSON.parse(task.task_json)?.payload, "candidate task payload");
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(409, "candidate task payload is invalid");
  }
}

function normalizeCandidate(value) {
  const candidate = requireObject(value, "materialized candidate");
  const image = requireObject(candidate.image_inputs, "candidate image inputs");
  const traceItems = requireArray(image.trace_items, "candidate trace items", 5, 8);
  if (traceItems.some((item) => typeof item !== "string" || !/^(?:[01]\d|2[0-3]):[0-5]\d\s+.+$/.test(item))) {
    throw new HttpError(400, "candidate trace items are invalid");
  }
  if (!["trace.candidate-materialization.v1"].includes(candidate.schema_version)) {
    throw new HttpError(400, "candidate schema is invalid");
  }
  if (!["morning", "evening", "manual"].includes(candidate.posting_slot)) {
    throw new HttpError(400, "candidate posting slot is invalid");
  }
  return {
    schema_version: candidate.schema_version,
    topic: requiredString(candidate.topic, "candidate topic", 200),
    country: requiredString(candidate.country, "candidate country", 2),
    caption: requiredString(candidate.caption, "candidate caption", 10_000),
    hypothesis: requiredString(candidate.hypothesis, "candidate hypothesis", 2_000),
    posting_slot: candidate.posting_slot,
    appium_prompt: typeof candidate.appium_prompt === "string" ? candidate.appium_prompt : "",
    image_inputs: {
      trace_items: traceItems,
      device_time: requiredString(image.device_time, "device_time", 5),
      background_subject: requiredString(image.background_subject, "background_subject", 40),
      background_mood: requiredString(image.background_mood, "background_mood", 40),
      background_search_query: image.background_search_query == null
        ? null
        : requiredString(image.background_search_query, "background_search_query", 200),
      language: requiredString(image.language, "image language", 40),
    },
    claim_ids: requireArray(candidate.claim_ids, "candidate claim_ids", 1, 16),
  };
}

function requireObject(value, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new HttpError(400, `${name} is invalid`);
  }
  return value;
}

function requireArray(value, name, minimum, maximum) {
  if (!Array.isArray(value) || value.length < minimum || value.length > maximum) {
    throw new HttpError(400, `${name} is invalid`);
  }
  return value;
}

function requiredString(value, name, maximum) {
  if (typeof value !== "string" || !value.trim() || value.length > maximum) {
    throw new HttpError(400, `${name} is invalid`);
  }
  return value.trim();
}

function sameSet(left, right) {
  return Array.isArray(left)
    && Array.isArray(right)
    && left.length === new Set(left).size
    && right.length === new Set(right).size
    && left.length === right.length
    && left.every((item) => right.includes(item));
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) =>
      `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

async function canonicalSha256(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonicalJson(value)));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
