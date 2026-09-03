import { HttpError } from "./http-error.js";
import { assertHostedCallbackTransport, reserveWorkerTaskCallback } from "./mac-workers.js";
import {
  agentRunLineageFromRow,
  MARKETING_JUDGMENT_PIPELINE,
  resolveMarketingContextProjection,
} from "./marketing-agent.js";
import {
  hasOnlineMarketingWorker,
  marketingJudgmentCapability,
  marketingJudgmentCapabilityMatches,
} from "./marketing-worker-capabilities.js";
import {
  ReferenceVerificationError,
  verifyReferenceSources,
} from "./reference-source-verification.js";

const MAX_TASK_BYTES = 64 * 1024;

export async function receiveHostedReferenceResearchCallback(
  env,
  task,
  callback,
  worker = null,
  sourceFetcher = globalThis.fetch,
) {
  assertHostedCallbackTransport(task, worker);
  if (
    task.account_id !== callback.account_id
    || callback.kind !== "marketing_judgment"
    || callback.callback_id !== `${callback.task_id}:completed`
  ) {
    throw new HttpError(409, "callback scope does not match reference research task");
  }
  if (!marketingJudgmentCapabilityMatches(task, "market_research")) {
    throw new HttpError(409, "research callback capability does not match its task");
  }
  const status = callback.result?.status;
  if (!["succeeded", "failed", "unknown_side_effect"].includes(status)) {
    throw new HttpError(400, "invalid reference research result status");
  }
  if (status === "succeeded" && !(await hasOnlineMarketingWorker(env.DB, "shadow_strategy"))) {
    throw new HttpError(503, "no online worker can run the research follow-up strategy");
  }
  const storedResultJson = JSON.stringify(callback.result);
  if (task.callback_id) {
    if (task.callback_id !== callback.callback_id || task.result_json !== storedResultJson) {
      throw new HttpError(409, "reference research callback changed");
    }
    return { accepted: true, duplicate: true };
  }
  const payload = publishedPayload(task);
  const campaign = await env.DB.prepare(
    `SELECT campaign_id, account_id, feature_packet_id, feature_packet_sha256,
            mode, state, projection_revision, marketing_context_snapshot_id,
            marketing_context_snapshot_sha256, agent_run_id, research_session_id,
            research_input_sha256, research_trace_sha256, research_continuation_sha256
     FROM hosted_marketing_campaigns WHERE campaign_id = ? AND account_id = ?`,
  ).bind(payload.campaign_id, task.account_id).first();
  if (!campaign || campaign.state !== "strategy_requested") {
    throw new HttpError(409, "campaign is not awaiting reference research");
  }
  const agentRunLineage = agentRunLineageFromRow(campaign);
  if (canonicalJson(payload.agent_run_lineage ?? null) !== canonicalJson(agentRunLineage)) {
    throw new HttpError(409, "reference research agent-run lineage binding is invalid");
  }
  const now = new Date().toISOString();
  if (status !== "succeeded") {
    if (worker) {
      const reservation = await reserveWorkerTaskCallback(
        env.DB,
        worker,
        task,
        callback.callback_id,
        storedResultJson,
      );
      if (reservation.duplicate) return { accepted: true, duplicate: true };
    }
    return finishFailedResearch(env, task, callback, worker, campaign, storedResultJson, now);
  }
  const output = requireObject(callback.result?.output, "reference research output");
  const snapshot = requireObject(output.reference_snapshot, "reference snapshot");
  const snapshotSha256 = await canonicalSha256(snapshot);
  if (
    output.pipeline !== MARKETING_JUDGMENT_PIPELINE
    || output.judgment !== "market_research"
    || output.campaign_id !== campaign.campaign_id
    || payload.pipeline !== MARKETING_JUDGMENT_PIPELINE
    || payload.judgment !== "market_research"
    || snapshotSha256 !== output.reference_snapshot_sha256
    || snapshot.schema_version !== "trace.reference-research.v1"
    || snapshot.campaign_id !== campaign.campaign_id
    || snapshot.feature_packet_sha256 !== campaign.feature_packet_sha256
    || snapshot.quarantine !== true
    || canonicalJson(output.agent_run_lineage ?? null) !== canonicalJson(agentRunLineage)
    || output.tool_actions_created !== 0
  ) {
    throw new HttpError(409, "reference research output binding is invalid");
  }
  await assertFrozenMarketSeed(payload, snapshot);
  validateSnapshot(snapshot);
  let verificationBundle;
  try {
    verificationBundle = await verifyReferenceSources(
      snapshot,
      snapshotSha256,
      sourceFetcher,
      now,
    );
  } catch (error) {
    if (error instanceof ReferenceVerificationError) {
      throw new HttpError(409, error.message);
    }
    throw error;
  }
  const verificationBundleSha256 = await canonicalSha256(verificationBundle);
  const marketingContext = await resolveMarketingContextProjection(
    env.DB,
    campaign.account_id,
    campaign.marketing_context_snapshot_id,
  );
  if (canonicalJson(payload.marketing_context ?? null) !== canonicalJson(marketingContext)) {
    throw new HttpError(409, "reference research marketing context binding is invalid");
  }
  const strategyTaskId = crypto.randomUUID();
  const strategyTask = {
    schema_version: "1",
    task_id: strategyTaskId,
    run_id: campaign.campaign_id,
    account_id: campaign.account_id,
    kind: "marketing_judgment",
    idempotency_key: `marketing-judgment:${campaign.account_id}:${campaign.campaign_id}`,
    payload: {
      pipeline: MARKETING_JUDGMENT_PIPELINE,
      judgment: "shadow_strategy",
      campaign_id: campaign.campaign_id,
      mode: payload.mode,
      feature_packet: payload.feature_packet,
      feature_packet_sha256: payload.feature_packet_sha256,
      account: payload.account,
      business_outcome: payload.business_outcome,
      current_control: payload.current_control,
      marketing_context: marketingContext,
      reference_snapshot: snapshot,
      reference_snapshot_sha256: snapshotSha256,
      reference_verification: verificationBundle,
      reference_verification_sha256: verificationBundleSha256,
      canonical_principles: payload.canonical_principles,
      knowledge_snapshot_sha256: payload.knowledge_snapshot_sha256,
      available_capabilities: payload.available_capabilities,
      capability_snapshot_sha256: payload.capability_snapshot_sha256,
      agent_run_lineage: agentRunLineage,
      requested_by: "hosted_workspace",
    },
    created_at: now,
    credential_ref: null,
  };
  const taskJson = JSON.stringify(strategyTask);
  if (new TextEncoder().encode(taskJson).byteLength > MAX_TASK_BYTES) {
    throw new HttpError(413, "research-bound strategy task is too large");
  }
  if (worker) {
    const reservation = await reserveWorkerTaskCallback(
      env.DB,
      worker,
      task,
      callback.callback_id,
      storedResultJson,
    );
    if (reservation.duplicate) return { accepted: true, duplicate: true };
  }
  const nextRevision = Number(campaign.projection_revision) + 1;
  const detail = {
    campaign_id: campaign.campaign_id,
    research_task_id: task.task_id,
    strategy_task_id: strategyTaskId,
    reference_snapshot_id: snapshot.snapshot_id,
    reference_snapshot_sha256: snapshotSha256,
    source_count: snapshot.sources.length,
    verified_source_count: verificationBundle.receipts.length,
    reference_verification_sha256: verificationBundleSha256,
    agent_run_lineage: agentRunLineage,
  };
  const statements = [
    env.DB.prepare(
      `INSERT INTO hosted_marketing_reference_snapshots
        (snapshot_id, campaign_id, schema_version, snapshot_json, snapshot_sha256,
         source_count, verification_bundle_json, verification_bundle_sha256, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      safeId(snapshot.snapshot_id, "snapshot_id"),
      campaign.campaign_id,
      snapshot.schema_version,
      canonicalJson(snapshot),
      snapshotSha256,
      snapshot.sources.length,
      canonicalJson(verificationBundle),
      verificationBundleSha256,
      snapshot.collected_at,
    ),
  ];
  for (const receipt of verificationBundle.receipts) {
    statements.push(env.DB.prepare(
      `INSERT INTO hosted_marketing_reference_source_receipts
        (receipt_id, snapshot_id, source_id, schema_version, requested_url, final_url,
         content_type, content_sha256, byte_length, receipt_json, receipt_sha256, fetched_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      safeId(receipt.receipt_id, "receipt_id"),
      safeId(snapshot.snapshot_id, "snapshot_id"),
      safeId(receipt.source_id, "source_id"),
      receipt.schema_version,
      receipt.requested_url,
      receipt.final_url,
      receipt.content_type,
      receipt.content_sha256,
      receipt.byte_length,
      canonicalJson(receipt),
      await canonicalSha256(receipt),
      receipt.fetched_at,
    ));
  }
  statements.push(
    env.DB.prepare(
      `INSERT INTO hosted_workspace_capture_tasks
        (task_id, run_id, account_id, candidate_id, candidate_revision, idempotency_key,
         task_json, state, dispatch_mode, kind, required_capability, created_at, updated_at)
       VALUES (?, ?, ?, '', 1, ?, ?, 'queued', 'worker_broker', 'marketing_judgment',
               ?, ?, ?)`,
    ).bind(
      strategyTaskId,
      strategyTask.run_id,
      campaign.account_id,
      strategyTask.idempotency_key,
      taskJson,
      marketingJudgmentCapability("shadow_strategy"),
      now,
      now,
    ),
    env.DB.prepare(
      `UPDATE hosted_marketing_campaigns SET projection_revision = ?, updated_at = ?
       WHERE campaign_id = ? AND account_id = ? AND state = 'strategy_requested'
         AND projection_revision = ?`,
    ).bind(
      nextRevision,
      now,
      campaign.campaign_id,
      campaign.account_id,
      campaign.projection_revision,
    ),
    env.DB.prepare(
      `INSERT INTO hosted_marketing_run_events
        (event_id, campaign_id, sequence, prior_revision, resulting_revision, event_type,
         event_json, event_sha256, idempotency_key, causation_id, correlation_id,
         event_time, observed_at, actor_type)
       VALUES (?, ?, ?, ?, ?, 'market_research_completed', ?, ?, ?, ?, ?, ?, ?, 'codex')`,
    ).bind(
      crypto.randomUUID(),
      campaign.campaign_id,
      nextRevision,
      campaign.projection_revision,
      nextRevision,
      canonicalJson(detail),
      await canonicalSha256(detail),
      `campaign:${campaign.campaign_id}:research:${snapshotSha256}`,
      task.task_id,
      campaign.campaign_id,
      now,
      now,
    ),
    completionStatement(env, task, callback, worker, "succeeded", storedResultJson, now),
  );
  const campaignUpdateIndex = 2 + verificationBundle.receipts.length;
  const completionIndex = statements.length - 1;
  const results = await env.DB.batch(statements);
  if (
    results.some((result) => result?.meta?.changes !== 1)
    || results[campaignUpdateIndex]?.meta?.changes !== 1
    || results[completionIndex]?.meta?.changes !== 1
  ) {
    throw new HttpError(409, "reference research completion lost its state race");
  }
  return {
    accepted: true,
    duplicate: false,
    campaign_id: campaign.campaign_id,
    state: "strategy_requested",
    reference_snapshot_id: snapshot.snapshot_id,
    strategy_task_id: strategyTaskId,
  };
}

async function assertFrozenMarketSeed(payload, snapshot) {
  const seed = payload.market_research_seed ?? null;
  const seedSha256 = payload.market_research_seed_sha256 ?? null;
  if ((seed === null) !== (seedSha256 === null)) {
    throw new HttpError(409, "market research seed binding is incomplete");
  }
  if (seed === null) return;
  const snapshotProposal = {
    schema_version: "trace.reference-research-proposal.v1",
    sources: snapshot.sources,
    observations: snapshot.observations,
    blind_spots: snapshot.blind_spots,
  };
  if (
    await canonicalSha256(seed) !== seedSha256
    || canonicalJson(snapshotProposal) !== canonicalJson(seed)
  ) {
    throw new HttpError(409, "reference research output drifted from its frozen market seed");
  }
}

function validateSnapshot(snapshot) {
  const sources = requireArray(snapshot.sources, "reference sources", 2, 16);
  const sourceIds = new Set();
  for (const source of sources) {
    sourceIds.add(safeId(source?.source_id, "source_id"));
    try {
      if (new URL(source?.url).protocol !== "https:") throw new Error("protocol");
    } catch {
      throw new HttpError(409, "reference source URL is invalid");
    }
  }
  if (sourceIds.size !== sources.length) throw new HttpError(409, "reference sources repeat");
  const observations = requireArray(snapshot.observations, "market observations", 2, 24);
  for (const observation of observations) {
    const cited = requireArray(observation?.source_ids, "observation source IDs", 1, 8);
    if (cited.some((sourceId) => !sourceIds.has(sourceId))) {
      throw new HttpError(409, "market observation cites an unknown source");
    }
  }
  requireArray(snapshot.blind_spots, "research blind spots", 1, 12);
}

async function finishFailedResearch(env, task, callback, worker, campaign, resultJson, now) {
  const nextRevision = Number(campaign.projection_revision) + 1;
  const detail = {
    campaign_id: campaign.campaign_id,
    task_id: task.task_id,
    status: callback.result.status,
    failure_code: callback.result.failure_code,
  };
  const results = await env.DB.batch([
    env.DB.prepare(
      `UPDATE hosted_marketing_campaigns
       SET state = 'failed', projection_revision = ?, updated_at = ?
       WHERE campaign_id = ? AND account_id = ? AND state = 'strategy_requested'
         AND projection_revision = ?`,
    ).bind(
      nextRevision,
      now,
      campaign.campaign_id,
      campaign.account_id,
      campaign.projection_revision,
    ),
    env.DB.prepare(
      `INSERT INTO hosted_marketing_run_events
        (event_id, campaign_id, sequence, prior_revision, resulting_revision, event_type,
         event_json, event_sha256, idempotency_key, causation_id, correlation_id,
         event_time, observed_at, actor_type)
       VALUES (?, ?, ?, ?, ?, 'market_research_failed', ?, ?, ?, ?, ?, ?, ?, 'runtime')`,
    ).bind(
      crypto.randomUUID(),
      campaign.campaign_id,
      nextRevision,
      campaign.projection_revision,
      nextRevision,
      canonicalJson(detail),
      await canonicalSha256(detail),
      `campaign:${campaign.campaign_id}:research-failed:${task.task_id}`,
      task.task_id,
      campaign.campaign_id,
      now,
      now,
    ),
    completionStatement(env, task, callback, worker, callback.result.status, resultJson, now),
  ]);
  if (results[0]?.meta?.changes !== 1 || results[2]?.meta?.changes !== 1) {
    throw new HttpError(409, "reference research failure lost its state race");
  }
  return { accepted: true, duplicate: false, campaign_id: campaign.campaign_id, state: "failed" };
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
    return requireObject(JSON.parse(task.task_json)?.payload, "published research payload");
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(409, "published research payload is invalid");
  }
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

function safeId(value, name) {
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value)) {
    throw new HttpError(400, `${name} is invalid`);
  }
  return value;
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
