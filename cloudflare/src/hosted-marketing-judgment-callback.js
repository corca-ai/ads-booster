import { HttpError } from "./http-error.js";
import { assertHostedCallbackTransport, reserveWorkerTaskCallback } from "./mac-workers.js";
import {
  agentRunLineageFromRow,
  MARKETING_JUDGMENT_PIPELINE,
} from "./marketing-agent.js";
import { marketingJudgmentCapabilityMatches } from "./marketing-worker-capabilities.js";
import { SUCCESSOR_CONVERSATION_MOTIVE } from "./marketing-successor-activation.js";

export async function receiveHostedMarketingJudgmentCallback(env, task, callback, worker = null) {
  assertHostedCallbackTransport(task, worker);
  if (
    task.run_id !== callback.run_id
    || task.account_id !== callback.account_id
    || callback.kind !== "marketing_judgment"
  ) {
    throw new HttpError(409, "callback scope does not match hosted marketing judgment task");
  }
  if (!marketingJudgmentCapabilityMatches(task, "shadow_strategy")) {
    throw new HttpError(409, "strategy callback capability does not match its task");
  }
  if (callback.callback_id !== `${callback.task_id}:completed`) {
    throw new HttpError(409, "callback_id does not match hosted marketing judgment task");
  }
  const status = callback.result?.status;
  if (!["succeeded", "failed", "unknown_side_effect"].includes(status)) {
    throw new HttpError(400, "invalid hosted marketing judgment result status");
  }
  const storedResultJson = JSON.stringify(callback.result);
  if (task.callback_id) {
    if (task.callback_id !== callback.callback_id) throw new HttpError(409, "conflicting callback");
    if (task.result_json !== storedResultJson) throw new HttpError(409, "callback result changed");
    return { accepted: true, duplicate: true };
  }
  const publishedPayload = publishedJudgmentPayload(task);
  const campaign = await env.DB.prepare(
    `SELECT campaign_id, account_id, feature_packet_id, feature_packet_sha256, mode, state,
            projection_revision, business_outcome, marketing_context_snapshot_id,
            marketing_context_snapshot_sha256, agent_run_id, research_session_id,
            research_input_sha256, research_trace_sha256, research_continuation_sha256
     FROM hosted_marketing_campaigns WHERE campaign_id = ? AND account_id = ?`,
  ).bind(task.run_id, task.account_id).first();
  if (
    !campaign
    || !["shadow", "assisted"].includes(campaign.mode)
    || campaign.state !== "strategy_requested"
  ) {
    throw new HttpError(409, "agent campaign is not awaiting a strategy judgment");
  }
  const now = new Date().toISOString();
  const agentRunLineage = agentRunLineageFromRow(campaign);
  if (
    canonicalJson(publishedPayload.agent_run_lineage ?? null)
    !== canonicalJson(agentRunLineage)
  ) {
    throw new HttpError(409, "strategy agent-run lineage binding is invalid");
  }
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
    return finishFailedJudgment(env, task, callback, worker, campaign, storedResultJson, now);
  }

  const output = requireObject(callback.result?.output, "marketing judgment output");
  if (
    output.pipeline !== MARKETING_JUDGMENT_PIPELINE
    || output.judgment !== "shadow_strategy"
    || output.campaign_id !== campaign.campaign_id
    || output.publication_allowed !== (publishedPayload.feature_packet?.gate?.publication_allowed === true)
    || publishedPayload.pipeline !== MARKETING_JUDGMENT_PIPELINE
    || publishedPayload.judgment !== "shadow_strategy"
    || publishedPayload.campaign_id !== campaign.campaign_id
    || (publishedPayload.mode ?? "shadow") !== campaign.mode
    || canonicalJson(output.agent_run_lineage ?? null) !== canonicalJson(agentRunLineage)
  ) {
    throw new HttpError(409, "marketing judgment output binding is invalid");
  }
  const receipt = requireObject(output.context_receipt, "context receipt");
  const brief = requireObject(output.strategy_brief, "strategy brief");
  const receiptSha256 = await canonicalSha256(receipt);
  const briefSha256 = await canonicalSha256(brief);
  const marketingContext = await currentMarketingContextProjection(env.DB, campaign, now);
  if (
    receiptSha256 !== output.context_receipt_sha256
    || briefSha256 !== output.strategy_brief_sha256
  ) {
    throw new HttpError(409, "marketing judgment receipt digest is invalid");
  }
  if (
    receipt.schema_version !== "trace.context-receipt.v1"
    || receipt.receipt_id !== task.task_id
    || receipt.campaign_id !== campaign.campaign_id
    || receipt.feature_packet_id !== campaign.feature_packet_id
    || receipt.feature_packet_sha256 !== campaign.feature_packet_sha256
    || brief.schema_version !== "trace.strategy-brief.v1"
    || brief.campaign_id !== campaign.campaign_id
    || brief.account_id !== campaign.account_id
    || brief.feature_packet_id !== campaign.feature_packet_id
    || brief.feature_packet_sha256 !== campaign.feature_packet_sha256
    || brief.context_receipt_sha256 !== receiptSha256
    || brief.business_outcome !== campaign.business_outcome
    || brief.business_outcome !== publishedPayload.business_outcome
    || receipt.knowledge_snapshot_sha256 !== publishedPayload.knowledge_snapshot_sha256
    || receipt.capability_snapshot_sha256 !== publishedPayload.capability_snapshot_sha256
    || canonicalJson(receipt.marketing_context ?? null) !== canonicalJson(marketingContext)
    || canonicalJson(publishedPayload.marketing_context ?? null) !== canonicalJson(marketingContext)
    || receipt.created_at !== brief.created_at
  ) {
    throw new HttpError(409, "marketing judgment strategy scope is invalid");
  }
  const hypotheses = requireArray(brief.hypotheses, "strategy hypotheses", 2, 8);
  const controls = hypotheses.filter((hypothesis) => hypothesis?.role === "control");
  if (controls.length !== 1) throw new HttpError(409, "strategy requires exactly one control");
  const referenceSnapshot = publishedPayload.reference_snapshot ?? null;
  const referenceIds = await validateStoredReferenceVerification(
    env.DB,
    campaign,
    publishedPayload,
  );
  const successorSeed = publishedPayload.next_experiment_seed ?? null;
  validateHypothesisEvidence(
    hypotheses,
    publishedPayload.feature_packet,
    referenceIds,
    successorSeed,
  );
  if (successorSeed == null) {
    validateDecisionDossier(
      brief.decision_dossier,
      publishedPayload.feature_packet,
      marketingContext,
      referenceSnapshot,
    );
  } else {
    await validateSuccessorStrategy(
      env.DB,
      task,
      campaign,
      receipt,
      brief,
      hypotheses,
      successorSeed,
    );
  }
  const experiment = requireObject(brief.experiment, "strategy experiment");
  const experimentId = safeId(experiment.experiment_id, "experiment_id");
  const activated = requireArray(
    experiment.activated_hypothesis_ids,
    "activated hypotheses",
    2,
    8,
  );
  const hypothesisIds = new Set(hypotheses.map((hypothesis) => safeId(
    hypothesis?.hypothesis_id,
    "hypothesis_id",
  )));
  if (activated.some((hypothesisId) => !hypothesisIds.has(hypothesisId))) {
    throw new HttpError(409, "experiment activates an unknown hypothesis");
  }
  const controlId = safeId(controls[0].hypothesis_id, "control hypothesis_id");
  if (!activated.includes(controlId) || new Set(activated).size !== activated.length) {
    throw new HttpError(409, "experiment must activate one unique control portfolio");
  }
  validateOutcomeDefinition(experiment.primary_outcome);
  const allocationMethod = validateExperimentAllocation(experiment, activated, controlId);
  const randomizationSeed = allocationMethod === "server_randomized_complete_blocks_v1"
    ? randomHex(32)
    : null;
  const randomizationSeedSha256 = randomizationSeed
    ? await sha256Text(randomizationSeed)
    : null;
  const exposurePlan = allocationMethod === "server_randomized_complete_blocks_v1"
    ? await buildExperimentExposurePlan(env.DB, campaign, experimentId, now)
    : null;
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
  const eventDetail = {
    campaign_id: campaign.campaign_id,
    task_id: task.task_id,
    context_receipt_id: receipt.receipt_id,
    context_receipt_sha256: receiptSha256,
    strategy_brief_id: brief.brief_id,
    strategy_brief_sha256: briefSha256,
    experiment_id: experimentId,
    allocation_method: allocationMethod,
    randomization_seed_sha256: randomizationSeedSha256,
    exposure_plan_sha256: exposurePlan?.plan_sha256 ?? null,
    agent_run_lineage: agentRunLineage,
  };
  const statements = [
    env.DB.prepare(
      `INSERT INTO hosted_marketing_context_receipts
        (receipt_id, campaign_id, schema_version, receipt_json, receipt_sha256,
         feature_packet_sha256, knowledge_snapshot_sha256, capability_snapshot_sha256,
         prompt_sha256, output_schema_sha256, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      receipt.receipt_id,
      campaign.campaign_id,
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
      `INSERT INTO hosted_marketing_strategy_briefs
        (brief_id, campaign_id, context_receipt_id, schema_version, brief_json,
         brief_sha256, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      brief.brief_id,
      campaign.campaign_id,
      receipt.receipt_id,
      brief.schema_version,
      canonicalJson(brief),
      briefSha256,
      brief.created_at,
    ),
    env.DB.prepare(
      `INSERT INTO hosted_marketing_experiments
        (experiment_id, campaign_id, strategy_brief_id, state, primary_outcome_scope,
         allocation_method, randomization_seed, randomization_seed_sha256,
         registration_json, registration_sha256, created_at, updated_at)
       VALUES (?, ?, ?, 'registered', ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      experimentId,
      campaign.campaign_id,
      brief.brief_id,
      experiment.primary_outcome?.scope,
      allocationMethod,
      randomizationSeed,
      randomizationSeedSha256,
      canonicalJson(experiment),
      await canonicalSha256(experiment),
      now,
      now,
    ),
  ];
  if (exposurePlan) {
    statements.push(env.DB.prepare(
      `INSERT INTO hosted_marketing_experiment_exposure_plans
        (experiment_id, account_id, profile_id, threads_user_id_snapshot,
         username_snapshot, timezone_snapshot, morning_time_snapshot,
         evening_time_snapshot, account_revision, plan_json, plan_sha256, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      experimentId,
      campaign.account_id,
      exposurePlan.profile_id,
      exposurePlan.threads_user_id_snapshot,
      exposurePlan.username_snapshot,
      exposurePlan.timezone_snapshot,
      exposurePlan.morning_time_snapshot,
      exposurePlan.evening_time_snapshot,
      exposurePlan.account_revision,
      exposurePlan.plan_json,
      exposurePlan.plan_sha256,
      now,
    ));
  }
  for (const hypothesis of hypotheses) {
    const hypothesisId = safeId(hypothesis.hypothesis_id, "hypothesis_id");
    statements.push(
      env.DB.prepare(
        `INSERT INTO hosted_marketing_hypotheses
          (hypothesis_id, campaign_id, strategy_brief_id, portfolio_role, hypothesis_json,
           hypothesis_sha256, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?)`,
      ).bind(
        hypothesisId,
        campaign.campaign_id,
        brief.brief_id,
        hypothesis.role,
        canonicalJson(hypothesis),
        await canonicalSha256(hypothesis),
        now,
      ),
    );
    if (activated.includes(hypothesisId)) {
      statements.push(
        env.DB.prepare(
          `INSERT INTO hosted_marketing_experiment_arms
            (arm_id, experiment_id, hypothesis_id, treatment_json, treatment_sha256,
             allocation_weight, created_at)
           VALUES (?, ?, ?, ?, ?, 1, ?)`,
        ).bind(
          `${experimentId}.${hypothesisId}`,
          experimentId,
          hypothesisId,
          canonicalJson(hypothesis),
          await canonicalSha256(hypothesis),
          now,
        ),
      );
    }
  }
  statements.push(
    env.DB.prepare(
      `UPDATE hosted_marketing_campaigns
       SET state = 'experiment_registered', projection_revision = ?, updated_at = ?
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
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'codex')`,
    ).bind(
      crypto.randomUUID(),
      campaign.campaign_id,
      nextRevision,
      campaign.projection_revision,
      nextRevision,
      campaign.mode === "shadow" ? "shadow_strategy_completed" : "strategy_completed",
      canonicalJson(eventDetail),
      await canonicalSha256(eventDetail),
      `campaign:${campaign.campaign_id}:strategy:${briefSha256}`,
      task.task_id,
      campaign.campaign_id,
      now,
      now,
    ),
    completionStatement(env, task, callback, worker, "succeeded", storedResultJson, now),
  );
  const results = await env.DB.batch(statements);
  const campaignUpdate = results.at(-3);
  const completion = results.at(-1);
  if (campaignUpdate?.meta?.changes !== 1 || completion?.meta?.changes !== 1) {
    throw new HttpError(409, "marketing judgment completion lost its state race");
  }
  return {
    accepted: true,
    duplicate: false,
    campaign_id: campaign.campaign_id,
    state: "experiment_registered",
    strategy_brief_id: brief.brief_id,
    experiment_id: experimentId,
  };
}

async function currentMarketingContextProjection(database, campaign, now) {
  const snapshotId = campaign.marketing_context_snapshot_id;
  const snapshotSha256 = campaign.marketing_context_snapshot_sha256;
  if (snapshotId == null && snapshotSha256 == null) return null;
  if (snapshotId == null || snapshotSha256 == null) {
    throw new HttpError(409, "campaign marketing context binding is incomplete");
  }
  const row = await database.prepare(
    `SELECT snapshot_json, snapshot_sha256, expires_at
     FROM hosted_marketing_context_snapshots
     WHERE snapshot_id = ? AND account_id = ? AND snapshot_sha256 = ?`,
  ).bind(snapshotId, campaign.account_id, snapshotSha256).first();
  if (!row || Date.parse(row.expires_at) <= Date.parse(now)) {
    throw new HttpError(409, "campaign marketing context binding is expired or invalid");
  }
  let snapshot;
  try {
    snapshot = requireObject(JSON.parse(row.snapshot_json), "campaign marketing context snapshot");
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(409, "campaign marketing context snapshot is invalid");
  }
  if (
    snapshot.schema_version !== "trace.marketing-context.v1"
    || snapshot.snapshot_id !== snapshotId
    || snapshot.account_id !== campaign.account_id
    || await canonicalSha256(snapshot) !== snapshotSha256
  ) {
    throw new HttpError(409, "campaign marketing context snapshot binding is invalid");
  }
  const customerSignals = requireArray(snapshot.customer_signals, "context customer signals", 1, 24);
  return {
    schema_version: "trace.marketing-context-projection.v1",
    snapshot_id: snapshot.snapshot_id,
    snapshot_sha256: snapshotSha256,
    account_id: snapshot.account_id,
    brand_guardrails: requireArray(snapshot.brand_guardrails, "context brand guardrails", 1, 16),
    audience_context: requireArray(snapshot.audience_context, "context audience", 1, 16),
    channel_policy_ids: requireArray(snapshot.channel_policy_ids ?? [], "context channel policies", 0, 16),
    customer_signals: customerSignals,
    expires_at: snapshot.expires_at,
  };
}

async function validateStoredReferenceVerification(database, campaign, payload) {
  const snapshot = payload.reference_snapshot ?? null;
  const snapshotSha256 = payload.reference_snapshot_sha256 ?? null;
  const verification = payload.reference_verification ?? null;
  const verificationSha256 = payload.reference_verification_sha256 ?? null;
  if (snapshot === null) {
    if (snapshotSha256 !== null || verification !== null || verificationSha256 !== null) {
      throw new HttpError(409, "strategy reference binding is incomplete");
    }
    return new Set();
  }
  const frozenSnapshot = requireObject(snapshot, "published reference snapshot");
  if (
    await canonicalSha256(frozenSnapshot) !== snapshotSha256
    || frozenSnapshot.schema_version !== "trace.reference-research.v1"
    || frozenSnapshot.campaign_id !== campaign.campaign_id
    || frozenSnapshot.feature_packet_sha256 !== campaign.feature_packet_sha256
    || frozenSnapshot.quarantine !== true
  ) {
    throw new HttpError(409, "strategy reference snapshot binding is invalid");
  }
  const frozenVerification = requireObject(verification, "published reference verification");
  if (
    await canonicalSha256(frozenVerification) !== verificationSha256
    || frozenVerification.schema_version !== "trace.reference-verification.v1"
    || frozenVerification.snapshot_id !== frozenSnapshot.snapshot_id
    || frozenVerification.snapshot_sha256 !== snapshotSha256
  ) {
    throw new HttpError(409, "strategy reference verification binding is invalid");
  }
  const sources = requireArray(frozenSnapshot.sources, "reference sources", 2, 16);
  const sourceUrls = new Map(sources.map((source) => [
    safeId(source?.source_id, "reference source_id"),
    normalizedHttpsUrl(source?.url, "reference source URL"),
  ]));
  if (sourceUrls.size !== sources.length) {
    throw new HttpError(409, "strategy reference source IDs are not unique");
  }
  const receipts = requireArray(frozenVerification.receipts, "reference receipts", 2, 16);
  const receiptIds = new Set();
  const receiptsBySource = new Map();
  for (const receipt of receipts) {
    const sourceId = safeId(receipt?.source_id, "reference receipt source_id");
    const receiptId = safeId(receipt?.receipt_id, "reference receipt_id");
    if (
      receipt?.schema_version !== "trace.reference-source-receipt.v1"
      || receiptIds.has(receiptId)
      || receiptsBySource.has(sourceId)
      || sourceUrls.get(sourceId) !== normalizedHttpsUrl(
        receipt?.requested_url,
        "reference receipt requested URL",
      )
      || !Number.isInteger(receipt?.http_status)
      || receipt.http_status < 200
      || receipt.http_status > 299
      || !["application/json", "application/pdf", "text/html", "text/plain"]
        .includes(receipt?.content_type)
      || typeof receipt?.content_sha256 !== "string"
      || !/^[a-f0-9]{64}$/.test(receipt.content_sha256)
      || !Number.isInteger(receipt?.byte_length)
      || receipt.byte_length < 1
      || receipt.byte_length > 1024 * 1024
    ) {
      throw new HttpError(409, "strategy reference receipt is invalid");
    }
    normalizedHttpsUrl(receipt.final_url, "reference receipt final URL");
    requiredString(receipt.fetched_at, "reference receipt fetched_at", 80);
    receiptIds.add(receiptId);
    receiptsBySource.set(sourceId, receipt);
  }
  if (
    receiptsBySource.size !== sourceUrls.size
    || [...sourceUrls].some(([sourceId]) => !receiptsBySource.has(sourceId))
  ) {
    throw new HttpError(409, "strategy reference receipts do not cover frozen sources");
  }
  const snapshotRow = await database.prepare(
    `SELECT verification_bundle_json, verification_bundle_sha256
     FROM hosted_marketing_reference_snapshots
     WHERE snapshot_id = ? AND campaign_id = ? AND snapshot_sha256 = ?`,
  ).bind(frozenSnapshot.snapshot_id, campaign.campaign_id, snapshotSha256).first();
  if (
    !snapshotRow
    || snapshotRow.verification_bundle_sha256 !== verificationSha256
    || snapshotRow.verification_bundle_json !== canonicalJson(frozenVerification)
  ) {
    throw new HttpError(409, "strategy reference verification is not stored provenance");
  }
  const storedRows = (await database.prepare(
    `SELECT source_id, receipt_json, receipt_sha256
     FROM hosted_marketing_reference_source_receipts
     WHERE snapshot_id = ? ORDER BY source_id`,
  ).bind(frozenSnapshot.snapshot_id).all()).results ?? [];
  if (storedRows.length !== receiptsBySource.size) {
    throw new HttpError(409, "strategy reference receipt provenance is incomplete");
  }
  for (const row of storedRows) {
    let storedReceipt;
    try {
      storedReceipt = requireObject(JSON.parse(row.receipt_json), "stored reference receipt");
    } catch (error) {
      if (error instanceof HttpError) throw error;
      throw new HttpError(409, "stored reference receipt is invalid");
    }
    const expected = receiptsBySource.get(row.source_id);
    if (
      expected == null
      || canonicalJson(expected) !== canonicalJson(storedReceipt)
      || await canonicalSha256(storedReceipt) !== row.receipt_sha256
    ) {
      throw new HttpError(409, "strategy reference receipt provenance changed");
    }
  }
  return new Set(sourceUrls.keys());
}

async function finishFailedJudgment(
  env,
  task,
  callback,
  worker,
  campaign,
  storedResultJson,
  now,
) {
  const nextRevision = Number(campaign.projection_revision) + 1;
  const eventDetail = {
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
       VALUES (?, ?, ?, ?, ?, 'shadow_strategy_failed', ?, ?, ?, ?, ?, ?, ?, 'runtime')`,
    ).bind(
      crypto.randomUUID(),
      campaign.campaign_id,
      nextRevision,
      campaign.projection_revision,
      nextRevision,
      canonicalJson(eventDetail),
      await canonicalSha256(eventDetail),
      `campaign:${campaign.campaign_id}:strategy-failed:${task.task_id}`,
      task.task_id,
      campaign.campaign_id,
      now,
      now,
    ),
    completionStatement(env, task, callback, worker, callback.result.status, storedResultJson, now),
  ]);
  if (results[0]?.meta?.changes !== 1 || results[2]?.meta?.changes !== 1) {
    throw new HttpError(409, "marketing judgment failure lost its state race");
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

function publishedJudgmentPayload(task) {
  try {
    const payload = JSON.parse(task.task_json)?.payload;
    return requireObject(payload, "published marketing judgment payload");
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(409, "published marketing judgment payload is invalid");
  }
}

function validateHypothesisEvidence(
  hypotheses,
  featurePacket,
  allowedReferenceIds,
  successorSeed = null,
) {
  const packet = requireObject(featurePacket, "published feature packet");
  const claims = requireArray(packet.claims, "published feature claims", 1, 64);
  const supportedClaimIds = new Set(claims
    .filter((claim) => [
      "source_supported",
      "build_bound",
      "installed_confirmed",
    ].includes(claim?.status))
    .map((claim) => safeId(claim?.claim_id, "published claim_id")));
  for (const hypothesis of hypotheses) {
    const claimIds = requireArray(hypothesis?.claim_ids, "hypothesis claim_ids", 1, 16)
      .map((claimId) => safeId(claimId, "hypothesis claim_id"));
    if (claimIds.some((claimId) => !supportedClaimIds.has(claimId))) {
      throw new HttpError(409, "strategy uses an unsupported feature claim");
    }
    const sourceControlReferences = successorSeed != null && hypothesis?.role === "control"
      ? new Set(requireArray(
        requireArray(
          requireObject(successorSeed.prior_strategy, "successor prior strategy").hypotheses,
          "successor prior hypotheses",
          2,
          8,
        ).find((item) => item?.role === "control")?.reference_ids,
        "successor control reference_ids",
        0,
        16,
      ))
      : new Set();
    if (!Array.isArray(hypothesis?.reference_ids)
      || hypothesis.reference_ids.some((referenceId) => (
        !allowedReferenceIds.has(referenceId) && !sourceControlReferences.has(referenceId)
      ))) {
      throw new HttpError(409, "shadow strategy breached the external reference quarantine");
    }
  }
}

async function validateSuccessorStrategy(
  database,
  task,
  campaign,
  receipt,
  brief,
  hypotheses,
  seed,
) {
  const row = await database.prepare(
    `SELECT activation.activation_json, activation.activation_sha256,
            activation.source_campaign_id, activation.source_lineage_sha256,
            activation.request_sha256, activation.draft_sha256,
            activation.approval_grant_id, activation.successor_campaign_id,
            activation.strategy_task_id, activation.state,
            request.request_json, draft.draft_json,
            grant.decision AS grant_decision, grant.reviewer_id, grant.reviewed_at
     FROM hosted_marketing_successor_activations AS activation
     JOIN hosted_marketing_next_experiment_requests AS request
       ON request.request_id = activation.request_id
      AND request.request_sha256 = activation.request_sha256
     JOIN hosted_marketing_next_experiment_drafts AS draft
       ON draft.draft_id = activation.draft_id
      AND draft.draft_sha256 = activation.draft_sha256
     JOIN hosted_marketing_approval_grants AS grant
       ON grant.grant_id = activation.approval_grant_id
     WHERE activation.activation_id = ? AND activation.account_id = ?`,
  ).bind(seed.activation_id, campaign.account_id).first();
  if (!row) throw new HttpError(409, "successor activation lineage is missing");
  const activation = parsedObject(row.activation_json, "successor activation");
  const request = parsedObject(row.request_json, "successor request");
  const draft = parsedObject(row.draft_json, "successor draft");
  const priorStrategy = requireObject(request.prior_strategy, "successor prior strategy");
  const evaluation = requireObject(request.evaluation, "successor evaluation");
  const reassessment = requireObject(request.reassessment, "successor reassessment");
  if (
    row.state !== "activated"
    || row.strategy_task_id !== task.task_id
    || row.successor_campaign_id !== campaign.campaign_id
    || row.grant_decision !== "approved"
    || await canonicalSha256(activation) !== row.activation_sha256
    || await canonicalSha256(request) !== row.request_sha256
    || await canonicalSha256(draft) !== row.draft_sha256
    || seed.schema_version !== "trace.successor-strategy-seed.v1"
    || seed.activation_id !== activation.activation_id
    || seed.successor_campaign_id !== campaign.campaign_id
    || seed.source_campaign_id !== row.source_campaign_id
    || seed.source_feature_packet_sha256 !== priorStrategy.feature_packet_sha256
    || seed.successor_feature_packet_sha256 !== campaign.feature_packet_sha256
    || priorStrategy.feature_packet_id === campaign.feature_packet_id
    || seed.source_lineage_sha256 !== row.source_lineage_sha256
    || seed.request_sha256 !== row.request_sha256
    || seed.approval_grant_id !== row.approval_grant_id
    || seed.approved_by !== row.reviewer_id
    || seed.approved_at !== row.reviewed_at
    || seed.approved_draft_sha256 !== row.draft_sha256
    || canonicalJson(seed.approved_draft) !== canonicalJson(draft)
    || canonicalJson(seed.prior_strategy) !== canonicalJson(priorStrategy)
    || canonicalJson(seed.evaluation) !== canonicalJson(evaluation)
    || canonicalJson(seed.reassessment) !== canonicalJson(reassessment)
    || await canonicalSha256(priorStrategy) !== seed.prior_strategy_sha256
    || await canonicalSha256(evaluation) !== seed.evaluation_sha256
    || await canonicalSha256(reassessment) !== seed.reassessment_sha256
  ) throw new HttpError(409, "successor strategy source binding is invalid");
  const candidate = requireObject(draft.candidate, "successor candidate");
  const controls = hypotheses.filter((hypothesis) => hypothesis?.role === "control");
  const challengers = hypotheses.filter((hypothesis) => hypothesis?.role === "challenger");
  const priorControl = requireArray(
    priorStrategy.hypotheses,
    "prior hypotheses",
    2,
    8,
  ).find((hypothesis) => hypothesis?.role === "control");
  const expectedControl = { ...requireObject(priorControl, "prior control") };
  expectedControl.hypothesis_id = seed.successor_control_hypothesis_id;
  const experiment = requireObject(brief.experiment, "successor experiment");
  const expectedRationale = `${candidate.hypothesis}\n\n${candidate.rationale}`;
  const expectedProofRequirement = `Expected signal: ${candidate.expected_signal}`;
  if (
    controls.length !== 1 || challengers.length !== 1
    || canonicalJson(controls[0]) !== canonicalJson(expectedControl)
    || challengers[0].hypothesis_id !== seed.successor_challenger_hypothesis_id
    || canonicalJson(challengers[0].claim_ids) !== canonicalJson(candidate.claim_ids)
    || challengers[0].value_frame !== candidate.treatment_concept
    || challengers[0].rationale !== expectedRationale
    || challengers[0].falsifier !== candidate.falsifier
    || challengers[0].proof_requirement !== expectedProofRequirement
    || challengers[0].conversation_motive !== SUCCESSOR_CONVERSATION_MOTIVE
    || brief.audience_situation !== candidate.audience_situation
    || brief.belief_to_change !== candidate.belief_to_change
    || canonicalJson(brief.decision_dossier) !== canonicalJson(reassessment.decision_dossier)
    || experiment.experiment_id !== seed.successor_experiment_id
    || experiment.manipulated_component !== candidate.manipulated_component
    || canonicalJson(experiment.held_constant_components)
      !== canonicalJson(draft.held_constant_components)
    || canonicalJson(experiment.primary_outcome) !== canonicalJson(draft.primary_outcome)
    || canonicalJson(experiment.activated_hypothesis_ids) !== canonicalJson([
      seed.successor_control_hypothesis_id,
      seed.successor_challenger_hypothesis_id,
    ])
    || !receipt.included_record_ids.includes(seed.activation_id)
    || !receipt.included_record_ids.includes(draft.draft_id)
    || !receipt.included_record_ids.includes(evaluation.evaluation_id)
    || !receipt.included_record_ids.includes(reassessment.reassessment_id)
  ) throw new HttpError(409, "successor strategy changed approved constraints");
}

function parsedObject(raw, name) {
  try {
    return requireObject(JSON.parse(raw), name);
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(409, `${name} is invalid`);
  }
}

function validateOutcomeDefinition(value) {
  const outcome = requireObject(value, "primary outcome");
  if (![
    "first_open",
    "feature_start",
    "generation_completed",
    "scheduling_completed",
    "setup_completed",
  ].includes(outcome.name)) {
    throw new HttpError(409, "primary outcome is not a versioned Trace product event");
  }
  if (outcome.scope === "direct_response_attribution" && outcome.causal_estimand != null) {
    throw new HttpError(409, "direct-response attribution cannot claim a causal estimand");
  }
  if (
    outcome.scope === "estimated_treatment_effect"
    && (typeof outcome.causal_estimand !== "string" || !outcome.causal_estimand.trim())
  ) {
    throw new HttpError(409, "estimated treatment effect requires a causal estimand");
  }
  if (![
    "direct_response_attribution",
    "estimated_treatment_effect",
  ].includes(outcome.scope)) {
    throw new HttpError(409, "primary outcome scope is invalid");
  }
}

function validateExperimentAllocation(experiment, activated, controlId) {
  const outcome = requireObject(experiment.primary_outcome, "primary outcome");
  const method = experiment.allocation_method ?? "balanced_complete_blocks";
  const causalTreatment = experiment.causal_treatment_hypothesis_id ?? null;
  if (outcome.scope === "estimated_treatment_effect") {
    if (method !== "server_randomized_complete_blocks_v1") {
      throw new HttpError(409, "estimated treatment effect requires server randomized blocks");
    }
    if (activated.length !== 2) {
      throw new HttpError(409, "estimated treatment effect requires exactly two hypotheses");
    }
    if (
      !Number.isInteger(experiment.maximum_posts)
      || experiment.maximum_posts !== experiment.minimum_eligible_blocks * activated.length
    ) {
      throw new HttpError(
        409,
        "estimated treatment effect requires one fixed complete block per post pair",
      );
    }
    if (
      typeof causalTreatment !== "string"
      || !activated.includes(causalTreatment)
      || causalTreatment === controlId
    ) {
      throw new HttpError(409, "estimated treatment effect requires one active non-control treatment");
    }
    return method;
  }
  if (method !== "balanced_complete_blocks" || causalTreatment !== null) {
    throw new HttpError(409, "direct-response attribution cannot register a causal estimator");
  }
  return method;
}

async function buildExperimentExposurePlan(database, campaign, experimentId, createdAt) {
  const row = await database.prepare(
    `SELECT account.account_id, account.timezone, account.morning_time, account.evening_time,
            account.revision AS account_revision, profile.profile_id,
            profile.threads_user_id, profile.username, profile.state AS profile_state
     FROM hosted_workspace_accounts AS account
     LEFT JOIN hosted_threads_profiles AS profile
       ON profile.account_id = account.account_id
      AND profile.profile_id = account.default_threads_profile_id
     WHERE account.account_id = ? AND account.enabled = 1`,
  ).bind(campaign.account_id).first();
  if (
    !row
    || row.profile_state !== "active"
    || !row.profile_id
    || !row.threads_user_id
    || !row.username
  ) {
    throw new HttpError(
      409,
      "causal experiment requires one active default Threads profile before registration",
    );
  }
  const plan = {
    schema_version: "trace.experiment-exposure-plan.v1",
    experiment_id: experimentId,
    account_id: campaign.account_id,
    account_revision: Number(row.account_revision),
    profile_id: row.profile_id,
    threads_user_id_snapshot: row.threads_user_id,
    username_snapshot: row.username,
    timezone_snapshot: requiredString(row.timezone, "account timezone", 80),
    morning_time_snapshot: requiredString(row.morning_time, "account morning time", 5),
    evening_time_snapshot: requiredString(row.evening_time, "account evening time", 5),
    created_at: createdAt,
  };
  return {
    ...plan,
    plan_json: canonicalJson(plan),
    plan_sha256: await canonicalSha256(plan),
  };
}

function validateDecisionDossier(value, packet, marketingContext, referenceSnapshot) {
  const dossier = requireObject(value, "decision dossier");
  if (
    dossier.schema_version !== "trace.marketing-decision-dossier.v1"
    || dossier.situation !== "new_launch"
  ) {
    throw new HttpError(409, "strategy decision dossier situation is invalid");
  }
  const positioning = requireObject(dossier.positioning, "decision positioning");
  requiredString(positioning.category, "positioning category", 500);
  requiredString(positioning.current_alternative, "positioning current alternative", 1000);
  requiredString(positioning.differentiated_mechanism, "positioning mechanism", 1500);
  requiredString(dossier.reason, "decision reason", 1500);
  const supportedClaims = new Set(requireArray(packet?.claims, "feature claims", 1, 64)
    .filter((claim) => ["source_supported", "build_bound", "installed_confirmed"]
      .includes(claim?.status))
    .map((claim) => safeId(claim?.claim_id, "feature claim_id")));
  const proofClaims = requireArray(positioning.proof_claim_ids, "positioning proof claims", 1, 16);
  if (proofClaims.some((claimId) => !supportedClaims.has(claimId))) {
    throw new HttpError(409, "strategy positioning uses an unsupported claim");
  }
  const allowedIcps = new Set((marketingContext?.customer_signals ?? [])
    .map((signal) => safeId(signal?.audience_segment_id, "audience segment_id")));
  const selectedIcp = safeId(dossier.selected_icp_id, "selected_icp_id");
  if (selectedIcp !== "research_needed" && !allowedIcps.has(selectedIcp)) {
    throw new HttpError(409, "strategy selected an unsupported ICP");
  }
  const requiredEvidence = new Set(requireArray(packet?.evidence, "feature evidence", 0, 128)
    .map((item) => safeId(item?.evidence_id, "feature evidence_id")));
  for (const signal of marketingContext?.customer_signals ?? []) {
    requiredEvidence.add(safeId(signal?.signal_id, "customer signal_id"));
  }
  for (const observation of referenceSnapshot?.observations ?? []) {
    requiredEvidence.add(safeId(observation?.observation_id, "market observation_id"));
  }
  const dispositions = requireArray(
    dossier.evidence_dispositions,
    "evidence dispositions",
    1,
    256,
  );
  const dispositionIds = dispositions.map((item) => safeId(item?.evidence_id, "evidence_id"));
  if (
    new Set(dispositionIds).size !== dispositionIds.length
    || dispositionIds.length !== requiredEvidence.size
    || dispositionIds.some((id) => !requiredEvidence.has(id))
    || dispositions.some((item) => (
      !["supports", "contradicts", "insufficient"].includes(item?.disposition)
      || !["fresh", "stale", "unknown"].includes(item?.freshness)
      || !["use_as_constraint", "test", "exclude"].includes(item?.use)
      || !Number.isInteger(item?.confidence_basis_points)
      || item.confidence_basis_points < 0
      || item.confidence_basis_points > 10_000
      || typeof item?.reason !== "string"
      || !item.reason.trim()
      || item.reason.length > 1000
      || (item?.freshness === "stale" && item?.use !== "exclude")
    ))
  ) {
    throw new HttpError(409, "strategy evidence dispositions are incomplete or unsafe");
  }
  const selectionBasis = requireArray(
    dossier.selection_basis_ids ?? [],
    "selection basis IDs",
    0,
    32,
  );
  if (
    new Set(selectionBasis).size !== selectionBasis.length
    || selectionBasis.some((id) => !requiredEvidence.has(id))
  ) {
    throw new HttpError(409, "strategy ICP basis is unbound");
  }
  const dispositionsById = new Map(dispositions.map((item) => [item.evidence_id, item]));
  for (const evidence of packet?.evidence ?? []) {
    const disposition = dispositionsById.get(evidence.evidence_id);
    if (disposition?.freshness !== "unknown") {
      throw new HttpError(409, "strategy evidence freshness is not independently verified");
    }
    if (
      ["fail", "absent", "inconclusive"].includes(evidence.result)
      && disposition?.disposition === "supports"
    ) {
      throw new HttpError(409, "strategy rewrote a feature evidence result");
    }
    if (evidence.result === "inconclusive" && disposition?.disposition !== "insufficient") {
      throw new HttpError(409, "strategy rewrote a feature evidence result");
    }
  }
  for (const signal of marketingContext?.customer_signals ?? []) {
    const disposition = dispositionsById.get(signal.signal_id);
    if (
      disposition?.freshness !== "fresh"
      || disposition.confidence_basis_points !== signal.confidence_basis_points
    ) {
      throw new HttpError(409, "strategy customer signal was rewritten");
    }
  }
  for (const observation of referenceSnapshot?.observations ?? []) {
    const disposition = dispositionsById.get(observation.observation_id);
    if (disposition?.freshness !== "unknown") {
      throw new HttpError(409, "strategy market evidence freshness is not independently verified");
    }
    if (
      observation.classification === "counterevidence"
      && (disposition?.disposition !== "contradicts"
        || !["use_as_constraint", "test"].includes(disposition?.use))
    ) {
      throw new HttpError(409, "strategy hid frozen market counterevidence");
    }
  }
  if (selectedIcp !== "research_needed") {
    const selectedSignalIds = new Set((marketingContext?.customer_signals ?? [])
      .filter((signal) => signal?.audience_segment_id === selectedIcp)
      .map((signal) => safeId(signal?.signal_id, "customer signal_id")));
    if (!selectionBasis.some((id) => {
      const disposition = dispositionsById.get(id);
      return selectedSignalIds.has(id)
        && disposition?.disposition === "supports"
        && ["use_as_constraint", "test"].includes(disposition?.use);
    })) {
      throw new HttpError(409, "strategy ICP basis is unbound");
    }
  }
  const requiredProofIds = requireArray(
    dossier.required_proof_ids ?? [],
    "required proof IDs",
    0,
    32,
  );
  const allowedProofIds = new Set([...supportedClaims, ...requiredEvidence]);
  if (
    new Set(requiredProofIds).size !== requiredProofIds.length
    || requiredProofIds.some((id) => !allowedProofIds.has(id))
  ) {
    throw new HttpError(409, "strategy required proof is unbound");
  }
  const nextStep = dossier.recommended_next_step;
  if (!["research", "design_experiment", "hold_for_review"].includes(nextStep)) {
    throw new HttpError(409, "strategy next step is unsafe for a new launch");
  }
  if (selectedIcp === "research_needed" && !["research", "hold_for_review"].includes(nextStep)) {
    throw new HttpError(409, "strategy cannot experiment before resolving its ICP");
  }
}

function randomHex(byteLength) {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function safeId(value, name) {
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value)) {
    throw new HttpError(400, `${name} is invalid`);
  }
  return value;
}

function requiredString(value, name, maximum) {
  if (typeof value !== "string" || !value.trim() || value.length > maximum) {
    throw new HttpError(400, `${name} is invalid`);
  }
  return value;
}

function normalizedHttpsUrl(value, name) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new HttpError(400, `${name} is invalid`);
  }
  if (url.protocol !== "https:" || url.username || url.password) {
    throw new HttpError(400, `${name} is invalid`);
  }
  url.hash = "";
  return url.href;
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

async function sha256Text(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
