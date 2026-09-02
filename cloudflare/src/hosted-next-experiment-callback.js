import { HttpError } from "./http-error.js";
import { assertHostedCallbackTransport, reserveWorkerTaskCallback } from "./mac-workers.js";
import {
  buildNextExperimentRequest,
  buildNextExperimentTask,
  canonicalJson,
  canonicalSha256,
  InvalidNextExperiment,
  NEXT_EXPERIMENT_CAPABILITY,
  NEXT_EXPERIMENT_JUDGMENT,
  nextExperimentDraftRecord,
} from "./marketing-next-experiment.js";

const PIPELINE = "hosted_marketing_judgment_v1";

export async function receiveHostedNextExperimentCallback(env, task, callback, worker = null) {
  assertHostedCallbackTransport(task, worker);
  if (task.dispatch_mode !== "worker_broker" || !worker) {
    throw new HttpError(409, "next experiment callback requires its broker worker");
  }
  if (
    task.run_id !== callback.run_id
    || task.account_id !== callback.account_id
    || callback.kind !== "marketing_judgment"
    || callback.callback_id !== `${callback.task_id}:completed`
    || task.required_capability !== NEXT_EXPERIMENT_CAPABILITY
  ) {
    throw new HttpError(409, "next experiment callback scope is invalid");
  }
  const status = callback.result?.status;
  if (!["succeeded", "failed", "unknown_side_effect"].includes(status)) {
    throw new HttpError(400, "next experiment callback status is invalid");
  }
  const resultJson = JSON.stringify(callback.result);
  if (task.callback_id) {
    if (task.callback_id !== callback.callback_id || task.result_json !== resultJson) {
      throw new HttpError(409, "next experiment callback changed");
    }
    return { accepted: true, duplicate: true };
  }
  const published = publishedPayload(task);
  const source = await loadExactSource(env.DB, task.account_id, task.task_id);
  if (
    !source
    || source.request_state !== "queued"
    || source.task_id !== task.task_id
  ) {
    throw new HttpError(409, "next experiment source is stale");
  }
  const stored = parseStoredSource(source);
  const rederived = await rederiveRequest(source, stored);
  const expectedTask = rederived ? buildNextExperimentTask(rederived) : null;
  if (
    !rederived
    || expectedTask.task_id !== task.task_id
    || canonicalJson(published) !== canonicalJson(expectedTask.payload)
    || canonicalJson(stored.request) !== canonicalJson(rederived.request)
    || source.request_sha256 !== rederived.request_sha256
    || source.source_lineage_sha256 !== rederived.source_lineage_sha256
    || source.idempotency_key !== rederived.idempotency_key
  ) {
    throw new HttpError(409, "next experiment task binding is invalid");
  }
  const now = new Date().toISOString();
  if (status !== "succeeded") {
    const reservation = await reserveWorkerTaskCallback(
      env.DB, worker, task, callback.callback_id, resultJson,
    );
    if (reservation.duplicate) return { accepted: true, duplicate: true };
    const results = await env.DB.batch([
      env.DB.prepare(
        `UPDATE hosted_marketing_next_experiment_requests
         SET state = ?, updated_at = ?
         WHERE request_id = ? AND account_id = ? AND task_id = ? AND state = 'queued'`,
      ).bind(
        status === "unknown_side_effect" ? "unknown_side_effect" : "failed",
        now,
        source.request_id,
        source.account_id,
        task.task_id,
      ),
      completionStatement(env.DB, task, callback, worker, status, resultJson, now),
    ]);
    assertChangedOnce(results, "next experiment failure race");
    return { accepted: true, duplicate: false, state: status };
  }
  const output = requireObject(callback.result?.output, "next experiment output");
  const draft = requireObject(output.next_experiment_draft, "next experiment draft");
  const admission = requireObject(output.next_experiment_admission, "next experiment admission");
  let draftRecord;
  try {
    draftRecord = await nextExperimentDraftRecord(rederived, draft, admission);
  } catch (error) {
    if (error instanceof InvalidNextExperiment) throw new HttpError(409, error.message);
    throw error;
  }
  if (
    output.pipeline !== PIPELINE
    || output.judgment !== NEXT_EXPERIMENT_JUDGMENT
    || output.tool_actions_created !== 0
    || output.next_experiment_draft_sha256 !== draftRecord.draft_sha256
    || output.next_experiment_admission_sha256 !== draftRecord.admission_sha256
  ) {
    throw new HttpError(409, "next experiment output binding is invalid");
  }
  const reservation = await reserveWorkerTaskCallback(
    env.DB, worker, task, callback.callback_id, resultJson,
  );
  if (reservation.duplicate) return { accepted: true, duplicate: true };
  const results = await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO hosted_marketing_next_experiment_drafts
        (draft_id, request_id, request_sha256, account_id, source_campaign_id,
         source_lineage_sha256, schema_version, draft_json, draft_sha256,
         admission_json, admission_sha256, state, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)`,
    ).bind(
      draft.draft_id,
      draftRecord.request_id,
      draftRecord.request_sha256,
      draftRecord.account_id,
      draftRecord.source_campaign_id,
      draftRecord.source_lineage_sha256,
      draft.schema_version,
      canonicalJson(draft),
      draftRecord.draft_sha256,
      canonicalJson(admission),
      draftRecord.admission_sha256,
      draft.created_at,
      now,
    ),
    env.DB.prepare(
      `UPDATE hosted_marketing_next_experiment_requests
       SET state = 'completed', updated_at = ?
       WHERE request_id = ? AND account_id = ? AND task_id = ? AND state = 'queued'`,
    ).bind(now, source.request_id, source.account_id, task.task_id),
    completionStatement(env.DB, task, callback, worker, "succeeded", resultJson, now),
  ]);
  assertChangedOnce(results, "next experiment callback race");
  return {
    accepted: true,
    duplicate: false,
    campaign_id: source.source_campaign_id,
    request_id: source.request_id,
    draft_id: draft.draft_id,
    draft_sha256: draftRecord.draft_sha256,
    state: "draft",
  };
}

async function loadExactSource(db, accountId, taskId) {
  return db.prepare(
    `SELECT request.request_id, request.account_id, request.source_campaign_id,
            request.source_feature_packet_id, request.source_feature_packet_sha256,
            request.source_strategy_brief_id, request.source_strategy_sha256,
            request.source_experiment_id, request.source_registration_sha256,
            request.source_evaluation_id, request.source_evaluation_sha256,
            request.source_reassessment_id, request.source_reassessment_sha256,
            request.knowledge_snapshot_sha256, request.marketing_context_snapshot_id,
            request.marketing_context_snapshot_sha256, request.agent_run_id,
            request.research_session_id, request.research_input_sha256,
            request.research_trace_sha256, request.research_continuation_sha256,
            request.source_lineage_sha256, request.request_json, request.request_sha256,
            request.idempotency_key, request.task_id, request.state AS request_state,
            campaign.state AS campaign_state, campaign.business_outcome,
            campaign.feature_packet_id AS campaign_feature_packet_id,
            campaign.marketing_context_snapshot_id AS campaign_context_id,
            campaign.marketing_context_snapshot_sha256 AS campaign_context_sha256,
            campaign.agent_run_id AS campaign_agent_run_id,
            campaign.research_session_id AS campaign_research_session_id,
            campaign.research_input_sha256 AS campaign_research_input_sha256,
            campaign.research_trace_sha256 AS campaign_research_trace_sha256,
            campaign.research_continuation_sha256 AS campaign_research_continuation_sha256,
            packet.packet_json, packet.packet_sha256,
            brief.brief_json, brief.brief_sha256,
            experiment.strategy_brief_id AS experiment_strategy_brief_id,
            experiment.registration_json, experiment.registration_sha256,
            evaluation.evaluation_json, evaluation.evaluation_sha256,
            reassessment.reassessment_json, reassessment.reassessment_sha256,
            knowledge.snapshot_json AS knowledge_json,
            knowledge.snapshot_sha256 AS stored_knowledge_sha256,
            context.snapshot_json AS context_json,
            context.snapshot_sha256 AS stored_context_sha256
     FROM hosted_marketing_next_experiment_requests AS request
     JOIN hosted_marketing_campaigns AS campaign
       ON campaign.campaign_id = request.source_campaign_id
      AND campaign.account_id = request.account_id
     JOIN hosted_marketing_feature_packets AS packet
       ON packet.packet_id = request.source_feature_packet_id
     JOIN hosted_marketing_strategy_briefs AS brief
       ON brief.brief_id = request.source_strategy_brief_id
     JOIN hosted_marketing_experiments AS experiment
       ON experiment.experiment_id = request.source_experiment_id
     JOIN hosted_marketing_experiment_evaluations AS evaluation
       ON evaluation.evaluation_id = request.source_evaluation_id
     JOIN hosted_marketing_outcome_reassessments AS reassessment
       ON reassessment.reassessment_id = request.source_reassessment_id
     JOIN hosted_marketing_knowledge_snapshots AS knowledge
       ON knowledge.campaign_id = request.source_campaign_id
     LEFT JOIN hosted_marketing_context_snapshots AS context
       ON context.snapshot_id = request.marketing_context_snapshot_id
      AND context.account_id = request.account_id
     WHERE request.task_id = ? AND request.account_id = ?`,
  ).bind(taskId, accountId).first();
}

function parseStoredSource(row) {
  try {
    return {
      request: requireObject(JSON.parse(row.request_json), "stored next experiment request"),
      featurePacket: requireObject(JSON.parse(row.packet_json), "stored feature packet"),
      priorStrategy: requireObject(JSON.parse(row.brief_json), "stored strategy"),
      experimentRegistration: requireObject(JSON.parse(row.registration_json), "stored registration"),
      evaluation: requireObject(JSON.parse(row.evaluation_json), "stored evaluation"),
      reassessment: requireObject(JSON.parse(row.reassessment_json), "stored reassessment"),
      knowledgeSnapshot: requireObject(JSON.parse(row.knowledge_json), "stored knowledge snapshot"),
      marketingContext: row.context_json == null
        ? null
        : requireObject(JSON.parse(row.context_json), "stored marketing context"),
    };
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(409, "next experiment source records are invalid");
  }
}

async function rederiveRequest(row, stored) {
  const exactDigests = [
    [stored.featurePacket, row.packet_sha256, row.source_feature_packet_sha256],
    [stored.priorStrategy, row.brief_sha256, row.source_strategy_sha256],
    [stored.experimentRegistration, row.registration_sha256, row.source_registration_sha256],
    [stored.evaluation, row.evaluation_sha256, row.source_evaluation_sha256],
    [stored.reassessment, row.reassessment_sha256, row.source_reassessment_sha256],
    [stored.knowledgeSnapshot, row.stored_knowledge_sha256, row.knowledge_snapshot_sha256],
  ];
  if (row.marketing_context_snapshot_id != null) {
    exactDigests.push([
      stored.marketingContext,
      row.stored_context_sha256,
      row.marketing_context_snapshot_sha256,
    ]);
  } else if (stored.marketingContext != null || row.stored_context_sha256 != null) {
    throw new HttpError(409, "next experiment context source is invalid");
  }
  for (const [value, storedSha, requestSha] of exactDigests) {
    if (storedSha !== requestSha || await canonicalSha256(value) !== storedSha) {
      throw new HttpError(409, "next experiment source digest is invalid");
    }
  }
  try {
    return await buildNextExperimentRequest({
      accountId: row.account_id,
      campaign: {
        campaign_id: row.source_campaign_id,
        account_id: row.account_id,
        state: row.campaign_state,
        business_outcome: row.business_outcome,
        feature_packet_id: row.campaign_feature_packet_id,
        marketing_context_snapshot_id: row.campaign_context_id,
        marketing_context_snapshot_sha256: row.campaign_context_sha256,
        agent_run_id: row.campaign_agent_run_id,
        research_session_id: row.campaign_research_session_id,
        research_input_sha256: row.campaign_research_input_sha256,
        research_trace_sha256: row.campaign_research_trace_sha256,
        research_continuation_sha256: row.campaign_research_continuation_sha256,
      },
      featurePacket: stored.featurePacket,
      featurePacketSha256: row.packet_sha256,
      priorStrategy: stored.priorStrategy,
      priorStrategySha256: row.brief_sha256,
      experimentRegistration: stored.experimentRegistration,
      registrationSha256: row.registration_sha256,
      evaluation: stored.evaluation,
      evaluationSha256: row.evaluation_sha256,
      reassessment: stored.reassessment,
      reassessmentSha256: row.reassessment_sha256,
      knowledgeSnapshot: stored.knowledgeSnapshot,
      knowledgeSnapshotSha256: row.stored_knowledge_sha256,
      marketingContext: stored.marketingContext,
      marketingContextSnapshotSha256: row.stored_context_sha256,
    });
  } catch (error) {
    if (error instanceof InvalidNextExperiment) throw new HttpError(409, error.message);
    throw error;
  }
}

function completionStatement(db, task, callback, worker, status, resultJson, now) {
  return db.prepare(
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
  );
}

function publishedPayload(task) {
  try {
    return requireObject(JSON.parse(task.task_json)?.payload, "next experiment task payload");
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(409, "next experiment task payload is invalid");
  }
}

function requireObject(value, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new HttpError(400, `${name} is invalid`);
  }
  return value;
}

function assertChangedOnce(results, message) {
  if (results.some((result) => result?.meta?.changes !== 1)) throw new HttpError(409, message);
}
