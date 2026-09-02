import { HttpError } from "./http-error.js";
import { assertHostedCallbackTransport, reserveWorkerTaskCallback } from "./mac-workers.js";
import { MARKETING_JUDGMENT_PIPELINE } from "./marketing-agent.js";
import {
  hasOnlineMarketingWorker,
  marketingJudgmentCapability,
  marketingJudgmentCapabilityMatches,
} from "./marketing-worker-capabilities.js";
import {
  deriveExperimentEvaluation,
  InvalidExperimentEvaluationRequest,
} from "./experiment-evaluation.js";
import {
  buildOutcomeReassessmentTask,
  supportedClaimIdsFromPacket,
} from "./marketing-outcome-reassessment.js";

export async function receiveHostedExperimentEvaluationCallback(env, task, callback, worker = null) {
  assertHostedCallbackTransport(task, worker);
  if (
    task.run_id !== callback.run_id
    || task.account_id !== callback.account_id
    || callback.kind !== "marketing_judgment"
    || callback.callback_id !== `${callback.task_id}:completed`
  ) {
    throw new HttpError(409, "experiment evaluation callback scope is invalid");
  }
  if (!marketingJudgmentCapabilityMatches(task, "experiment_evaluation")) {
    throw new HttpError(409, "evaluation callback capability does not match its task");
  }
  const status = callback.result?.status;
  if (!["succeeded", "failed", "unknown_side_effect"].includes(status)) {
    throw new HttpError(400, "experiment evaluation status is invalid");
  }
  const storedResultJson = JSON.stringify(callback.result);
  if (task.callback_id) {
    if (task.callback_id !== callback.callback_id || task.result_json !== storedResultJson) {
      throw new HttpError(409, "experiment evaluation callback changed");
    }
    return { accepted: true, duplicate: true };
  }
  const payload = publishedPayload(task);
  const request = requireObject(payload.request, "evaluation request");
  const campaign = await env.DB.prepare(
    `SELECT campaign.campaign_id, campaign.account_id, campaign.state,
            campaign.projection_revision, experiment.experiment_id,
            experiment.registration_sha256,
            brief.brief_json, brief.brief_sha256,
            packet.packet_json
     FROM hosted_marketing_campaigns AS campaign
     JOIN hosted_marketing_experiments AS experiment ON experiment.campaign_id = campaign.campaign_id
     JOIN hosted_marketing_strategy_briefs AS brief
       ON brief.brief_id = experiment.strategy_brief_id
     JOIN hosted_marketing_feature_packets AS packet
       ON packet.packet_id = campaign.feature_packet_id
      AND packet.packet_sha256 = campaign.feature_packet_sha256
     WHERE campaign.campaign_id = ? AND campaign.account_id = ?
       AND experiment.experiment_id = ?`,
  ).bind(
    request.campaign_id,
    task.account_id,
    request.registration?.experiment_id,
  ).first();
  if (!campaign || !["awaiting_review", "published", "observing"].includes(campaign.state)) {
    throw new HttpError(409, "experiment evaluation campaign is stale");
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
    const updated = await completionStatement(
      env,
      task,
      callback,
      worker,
      status,
      storedResultJson,
      now,
    ).run();
    if (updated.meta.changes !== 1) throw new HttpError(409, "evaluation failure race");
    return { accepted: true, duplicate: false, state: "failed" };
  }
  const output = requireObject(callback.result?.output, "evaluation output");
  const suppliedEvaluation = requireObject(output.evaluation, "experiment evaluation");
  let evaluation;
  try {
    evaluation = deriveExperimentEvaluation(request);
  } catch (error) {
    if (error instanceof InvalidExperimentEvaluationRequest) {
      throw new HttpError(409, "frozen experiment evaluation request is invalid");
    }
    throw error;
  }
  const evaluationSha256 = await canonicalSha256(evaluation);
  const registrationSha256 = await canonicalSha256(request.registration);
  if (
    output.pipeline !== MARKETING_JUDGMENT_PIPELINE
    || output.judgment !== "experiment_evaluation"
    || output.tool_actions_created !== 0
    || output.evaluation_sha256 !== evaluationSha256
    || canonicalJson(suppliedEvaluation) !== canonicalJson(evaluation)
    || evaluation.evaluation_id !== request.evaluation_id
    || evaluation.campaign_id !== campaign.campaign_id
    || evaluation.experiment_id !== campaign.experiment_id
    || registrationSha256 !== campaign.registration_sha256
  ) {
    throw new HttpError(409, "experiment evaluation output binding is invalid");
  }
  let priorStrategy;
  let featurePacket;
  try {
    priorStrategy = requireObject(JSON.parse(campaign.brief_json), "strategy brief");
    featurePacket = requireObject(JSON.parse(campaign.packet_json), "feature packet");
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(409, "reassessment source records are invalid");
  }
  if (await canonicalSha256(priorStrategy) !== campaign.brief_sha256) {
    throw new HttpError(409, "reassessment strategy lineage is invalid");
  }
  let reassessmentTask = null;
  if (priorStrategy.decision_dossier != null) {
    try {
      reassessmentTask = buildOutcomeReassessmentTask({
        accountId: campaign.account_id,
        campaignId: campaign.campaign_id,
        priorStrategy,
        priorStrategySha256: campaign.brief_sha256,
        evaluation,
        evaluationSha256,
        supportedClaimIds: supportedClaimIdsFromPacket(featurePacket),
      });
    } catch {
      throw new HttpError(409, "outcome reassessment request is invalid");
    }
  }
  if (
    reassessmentTask !== null
    && !(await hasOnlineMarketingWorker(env.DB, "outcome_reassessment"))
  ) {
    throw new HttpError(503, "no online worker can run the outcome reassessment");
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
  const statements = [];
  for (const observation of request.observations ?? []) {
    if (!observation.attribution_observed || !observation.publication_id) continue;
    const observationValue = {
      schema_version: "trace.attribution-observation.v1",
      observation_id: `attribution-${(await sha256Text(observation.assignment_id)).slice(0, 48)}`,
      campaign_id: campaign.campaign_id,
      experiment_id: campaign.experiment_id,
      assignment_id: observation.assignment_id,
      publication_id: observation.publication_id,
      product_event_id: observation.product_event_id,
      scope: "direct_response_attribution",
      window_hours: request.registration.primary_outcome.window_hours,
      matched: observation.converted === true,
      observed_at: evaluation.evaluated_at,
    };
    statements.push(
      env.DB.prepare(
        `INSERT INTO hosted_marketing_attribution_observations
          (observation_id, campaign_id, experiment_id, assignment_id, publication_id,
           product_event_id, scope, window_hours, matched, observed_at,
           observation_json, observation_sha256)
         VALUES (?, ?, ?, ?, ?, ?, 'direct_response_attribution', ?, ?, ?, ?, ?)`,
      ).bind(
        observationValue.observation_id,
        campaign.campaign_id,
        campaign.experiment_id,
        observation.assignment_id,
        observation.publication_id,
        observation.product_event_id,
        observationValue.window_hours,
        observationValue.matched ? 1 : 0,
        evaluation.evaluated_at,
        canonicalJson(observationValue),
        await canonicalSha256(observationValue),
      ),
    );
  }
  const nextRevision = Number(campaign.projection_revision) + 1;
  const event = {
    campaign_id: campaign.campaign_id,
    experiment_id: campaign.experiment_id,
    evaluation_id: evaluation.evaluation_id,
    evaluation_sha256: evaluationSha256,
    state: evaluation.state,
    reassessment_queued: reassessmentTask !== null,
  };
  statements.push(
    env.DB.prepare(
      `INSERT INTO hosted_marketing_experiment_evaluations
        (evaluation_id, campaign_id, experiment_id, schema_version, state,
         evaluation_json, evaluation_sha256, evaluated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      evaluation.evaluation_id,
      campaign.campaign_id,
      campaign.experiment_id,
      evaluation.schema_version,
      evaluation.state,
      canonicalJson(evaluation),
      evaluationSha256,
      evaluation.evaluated_at,
    ),
    env.DB.prepare(
      `UPDATE hosted_marketing_experiments SET state = ?, updated_at = ?
       WHERE experiment_id = ? AND state IN ('registered', 'running', 'observing')`,
    ).bind(evaluation.state, now, campaign.experiment_id),
    env.DB.prepare(
      `UPDATE hosted_marketing_campaigns
       SET state = 'evaluated', projection_revision = ?, updated_at = ?
       WHERE campaign_id = ? AND account_id = ? AND projection_revision = ?`,
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
       VALUES (?, ?, ?, ?, ?, 'experiment_evaluated', ?, ?, ?, ?, ?, ?, ?, 'runtime')`,
    ).bind(
      crypto.randomUUID(),
      campaign.campaign_id,
      nextRevision,
      campaign.projection_revision,
      nextRevision,
      canonicalJson(event),
      await canonicalSha256(event),
      `campaign:${campaign.campaign_id}:evaluation:${evaluationSha256}`,
      task.task_id,
      campaign.campaign_id,
      now,
      now,
    ),
  );
  if (reassessmentTask !== null) {
    statements.push(env.DB.prepare(
      `INSERT INTO hosted_workspace_capture_tasks
        (task_id, run_id, account_id, candidate_id, candidate_revision, idempotency_key,
         task_json, state, dispatch_mode, kind, required_capability, created_at, updated_at)
       VALUES (?, ?, ?, '', 1, ?, ?, 'queued', 'worker_broker', 'marketing_judgment',
               ?, ?, ?)`,
    ).bind(
      reassessmentTask.task_id,
      reassessmentTask.run_id,
      campaign.account_id,
      reassessmentTask.idempotency_key,
      JSON.stringify(reassessmentTask),
      marketingJudgmentCapability("outcome_reassessment"),
      reassessmentTask.created_at,
      now,
    ));
  }
  statements.push(completionStatement(
    env,
    task,
    callback,
    worker,
    "succeeded",
    storedResultJson,
    now,
  ));
  const results = await env.DB.batch(statements);
  if (results.some((result) => result?.meta?.changes !== 1)) {
    throw new HttpError(409, "experiment evaluation batch lost its state race");
  }
  return {
    accepted: true,
    duplicate: false,
    campaign_id: campaign.campaign_id,
    evaluation_id: evaluation.evaluation_id,
    state: evaluation.state,
    reassessment_id: reassessmentTask?.payload.reassessment_id ?? null,
    reassessment_task_id: reassessmentTask?.task_id ?? null,
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
    return requireObject(JSON.parse(task.task_json)?.payload, "evaluation task payload");
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(409, "evaluation task payload is invalid");
  }
}

function requireObject(value, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
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
  return sha256Text(canonicalJson(value));
}

async function sha256Text(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
