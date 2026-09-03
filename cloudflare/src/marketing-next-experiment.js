import { supportedClaimIdsFromPacket } from "./marketing-outcome-reassessment.js";
import { hasOnlineMarketingWorker } from "./marketing-worker-capabilities.js";

export const NEXT_EXPERIMENT_JUDGMENT = "next_experiment";
export const NEXT_EXPERIMENT_CAPABILITY = "next_experiment_v1";
export const NEXT_EXPERIMENT_REQUEST_SCHEMA = "trace.next-experiment-request.v1";
export const NEXT_EXPERIMENT_DRAFT_SCHEMA = "trace.next-experiment-draft.v1";

const DRAFT_KEYS = new Set([
  "schema_version", "draft_id", "campaign_id", "account_id", "trigger_evaluation_id",
  "trigger_evaluation_sha256", "trigger_reassessment_id", "trigger_reassessment_sha256",
  "prior_strategy_sha256", "control_hypothesis_id", "primary_outcome",
  "held_constant_components", "source_hypothesis_ids", "supporting_claim_ids", "evidence",
  "counterevidence", "assumptions", "unresolved_questions", "candidate", "effect_class",
  "state", "human_review_required", "created_at",
]);
const CANDIDATE_KEYS = new Set([
  "parent_hypothesis_ids", "claim_ids", "audience_situation", "belief_to_change",
  "hypothesis", "rationale",
  "manipulated_component", "treatment_concept", "expected_signal", "falsifier",
]);
const EVIDENCE_KEYS = new Set(["evidence_id", "interpretation"]);
const ADMISSION_KEYS = new Set([
  "schema_version", "state", "evidence_sha256", "reassessment_sha256",
  "source_strategy_sha256", "human_review_required", "effect_class",
]);

/**
 * Build an offline-safe, immutable outbox record from exact stored source records. Returning null
 * is a closed safety gate; it does not replace the model's later evidence-grounded judgment.
 */
export async function buildNextExperimentRequest({
  accountId,
  campaign,
  featurePacket,
  featurePacketSha256,
  priorStrategy,
  priorStrategySha256,
  experimentRegistration,
  registrationSha256,
  evaluation,
  evaluationSha256,
  reassessment,
  reassessmentSha256,
  knowledgeSnapshot,
  knowledgeSnapshotSha256,
  marketingContext = null,
  marketingContextSnapshotSha256 = null,
}) {
  const dossier = requireObject(reassessment?.decision_dossier, "reassessment decision dossier");
  if (
    dossier.recommended_next_step !== "design_experiment"
    || dossier.selected_icp_id === "research_needed"
    || reassessment?.situation === "tool_failure"
  ) return null;
  const reassessedHypotheses = reassessment?.hypothesis_reassessments;
  if (!Array.isArray(reassessedHypotheses)) {
    throw new InvalidNextExperiment("next experiment hypothesis reassessments are invalid");
  }
  if (!reassessedHypotheses.some((item) => (
    item?.disposition !== "retire"
    && typeof item?.next_test === "string"
    && item.next_test.trim()
  ))) return null;

  const normalizedAccountId = requiredId(accountId, "account ID");
  const campaignId = requiredId(campaign?.campaign_id, "campaign ID");
  if (
    campaign?.account_id !== normalizedAccountId
    || priorStrategy?.campaign_id !== campaignId
    || priorStrategy?.account_id !== normalizedAccountId
    || experimentRegistration?.experiment_id !== priorStrategy?.experiment?.experiment_id
    || evaluation?.campaign_id !== campaignId
    || evaluation?.experiment_id !== experimentRegistration?.experiment_id
    || reassessment?.campaign_id !== campaignId
    || reassessment?.trigger_evaluation_id !== evaluation?.evaluation_id
    || reassessment?.trigger_evaluation_sha256 !== evaluationSha256
    || campaign?.feature_packet_id !== featurePacket?.packet_id
  ) throw new InvalidNextExperiment("next experiment source identity is invalid");

  const hashes = {
    feature_packet_sha256: requiredSha(featurePacketSha256, "feature packet SHA"),
    strategy_sha256: requiredSha(priorStrategySha256, "strategy SHA"),
    registration_sha256: requiredSha(registrationSha256, "registration SHA"),
    evaluation_sha256: requiredSha(evaluationSha256, "evaluation SHA"),
    reassessment_sha256: requiredSha(reassessmentSha256, "reassessment SHA"),
    knowledge_snapshot_sha256: requiredSha(knowledgeSnapshotSha256, "knowledge snapshot SHA"),
  };
  for (const [value, digest, name] of [
    [featurePacket, hashes.feature_packet_sha256, "feature packet"],
    [priorStrategy, hashes.strategy_sha256, "strategy"],
    [experimentRegistration, hashes.registration_sha256, "registration"],
    [evaluation, hashes.evaluation_sha256, "evaluation"],
    [reassessment, hashes.reassessment_sha256, "reassessment"],
    [knowledgeSnapshot, hashes.knowledge_snapshot_sha256, "knowledge snapshot"],
  ]) await requireCanonicalHash(value, digest, name);

  const contextId = campaign.marketing_context_snapshot_id ?? null;
  const contextSha = campaign.marketing_context_snapshot_sha256 ?? null;
  if ((contextId == null) !== (contextSha == null) || contextSha !== marketingContextSnapshotSha256) {
    throw new InvalidNextExperiment("next experiment context lineage is invalid");
  }
  if (contextId != null) {
    requireObject(marketingContext, "marketing context");
    await requireCanonicalHash(marketingContext, requiredSha(contextSha, "context SHA"), "context");
  } else if (marketingContext != null) {
    throw new InvalidNextExperiment("unbound marketing context is invalid");
  }

  const lineage = {
    account_id: normalizedAccountId,
    campaign_id: campaignId,
    feature_packet_id: featurePacket.packet_id,
    feature_packet_sha256: hashes.feature_packet_sha256,
    strategy_brief_id: priorStrategy.brief_id,
    strategy_sha256: hashes.strategy_sha256,
    experiment_id: experimentRegistration.experiment_id,
    registration_sha256: hashes.registration_sha256,
    evaluation_id: evaluation.evaluation_id,
    evaluation_sha256: hashes.evaluation_sha256,
    reassessment_id: reassessment.reassessment_id,
    reassessment_sha256: hashes.reassessment_sha256,
    knowledge_snapshot_sha256: hashes.knowledge_snapshot_sha256,
    marketing_context_snapshot_id: contextId,
    marketing_context_snapshot_sha256: contextSha,
    agent_run_id: campaign.agent_run_id ?? null,
    research_session_id: campaign.research_session_id ?? null,
    research_input_sha256: campaign.research_input_sha256 ?? null,
    research_trace_sha256: campaign.research_trace_sha256 ?? null,
    research_continuation_sha256: campaign.research_continuation_sha256 ?? null,
  };
  validateAgentRunLineage(lineage);
  const sourceLineageSha256 = await canonicalSha256(lineage);
  const requestId = `next-experiment-request-${hashes.reassessment_sha256.slice(0, 48)}`;
  const createdAt = requiredTimestamp(reassessment.created_at, "reassessment created_at");
  const request = {
    schema_version: NEXT_EXPERIMENT_REQUEST_SCHEMA,
    request_id: requestId,
    account_id: normalizedAccountId,
    campaign_id: campaignId,
    source_lineage: lineage,
    source_lineage_sha256: sourceLineageSha256,
    feature_packet: featurePacket,
    prior_strategy: priorStrategy,
    experiment_registration: experimentRegistration,
    evaluation,
    reassessment,
    knowledge_snapshot: knowledgeSnapshot,
    marketing_context: marketingContext,
    supported_claim_ids: supportedClaimIdsFromPacket(featurePacket),
    requested_by: "hosted_workspace",
    created_at: createdAt,
  };
  return {
    request,
    request_sha256: await canonicalSha256(request),
    source_lineage_sha256: sourceLineageSha256,
    idempotency_key: `next-experiment:${normalizedAccountId}:${reassessment.reassessment_id}`,
  };
}

/** Safe to append after the reassessment INSERT in the same D1 batch; no worker lookup occurs. */
export function nextExperimentRequestInsertStatement(db, record) {
  const request = requireObject(record?.request, "next experiment request");
  const lineage = requireObject(request.source_lineage, "next experiment source lineage");
  return db.prepare(
    `INSERT INTO hosted_marketing_next_experiment_requests
      (request_id, account_id, source_campaign_id, source_feature_packet_id,
       source_feature_packet_sha256, source_strategy_brief_id, source_strategy_sha256,
       source_experiment_id, source_registration_sha256, source_evaluation_id,
       source_evaluation_sha256, source_reassessment_id, source_reassessment_sha256,
       knowledge_snapshot_sha256, marketing_context_snapshot_id,
       marketing_context_snapshot_sha256, agent_run_id, research_session_id,
       research_input_sha256, research_trace_sha256, research_continuation_sha256,
       source_lineage_sha256, schema_version, request_json, request_sha256,
       idempotency_key, task_id, state, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
             NULL, 'pending', ?, ?)`,
  ).bind(
    request.request_id, request.account_id, request.campaign_id,
    lineage.feature_packet_id, lineage.feature_packet_sha256,
    lineage.strategy_brief_id, lineage.strategy_sha256,
    lineage.experiment_id, lineage.registration_sha256,
    lineage.evaluation_id, lineage.evaluation_sha256,
    lineage.reassessment_id, lineage.reassessment_sha256,
    lineage.knowledge_snapshot_sha256,
    lineage.marketing_context_snapshot_id, lineage.marketing_context_snapshot_sha256,
    lineage.agent_run_id, lineage.research_session_id, lineage.research_input_sha256,
    lineage.research_trace_sha256, lineage.research_continuation_sha256,
    record.source_lineage_sha256, request.schema_version, canonicalJson(request),
    record.request_sha256, record.idempotency_key, request.created_at, request.created_at,
  );
}

/** Python's NextExperimentJudgmentRequest is the direct task payload, not this D1 wrapper. */
export function buildNextExperimentTask(record) {
  const request = requireObject(record?.request, "next experiment request");
  const requestSha = requiredSha(record.request_sha256, "request SHA");
  const taskId = `next-experiment-task-${requestSha.slice(0, 48)}`;
  return {
    schema_version: "1",
    task_id: taskId,
    run_id: taskId,
    account_id: request.account_id,
    kind: "marketing_judgment",
    idempotency_key: `next-experiment-task:${request.account_id}:${request.request_id}`,
    payload: {
      pipeline: "hosted_marketing_judgment_v1",
      judgment: NEXT_EXPERIMENT_JUDGMENT,
      campaign_id: request.campaign_id,
      account_id: request.account_id,
      prior_strategy: request.prior_strategy,
      prior_strategy_sha256: request.source_lineage.strategy_sha256,
      evaluation: request.evaluation,
      evaluation_sha256: request.source_lineage.evaluation_sha256,
      reassessment: request.reassessment,
      reassessment_sha256: request.source_lineage.reassessment_sha256,
      supported_claim_ids: request.supported_claim_ids,
      requested_by: "hosted_workspace",
    },
    created_at: request.created_at,
    credential_ref: null,
  };
}

/** Queue durable requests only when the exact reasoning capability is online. */
export async function runDueNextExperimentRequests(
  env,
  { workerAvailable = hasOnlineMarketingWorker, limit = 10 } = {},
) {
  if (!(await workerAvailable(env.DB, NEXT_EXPERIMENT_JUDGMENT))) {
    return { queued: 0, waiting_for_worker: true };
  }
  const pending = await env.DB.prepare(
    `SELECT request_id, account_id, source_campaign_id, request_json, request_sha256,
            source_lineage_sha256, idempotency_key
     FROM hosted_marketing_next_experiment_requests
     WHERE state = 'pending' ORDER BY created_at LIMIT ?`,
  ).bind(Math.max(1, Math.min(Number(limit) || 10, 100))).all();
  let queued = 0;
  for (const row of pending.results) {
    let request;
    try {
      request = requireObject(JSON.parse(row.request_json), "stored next experiment request");
    } catch {
      throw new InvalidNextExperiment("stored next experiment request is invalid");
    }
    const record = {
      request,
      request_sha256: row.request_sha256,
      source_lineage_sha256: row.source_lineage_sha256,
      idempotency_key: row.idempotency_key,
    };
    if (
      request.request_id !== row.request_id
      || request.account_id !== row.account_id
      || request.campaign_id !== row.source_campaign_id
      || request.source_lineage_sha256 !== row.source_lineage_sha256
      || await canonicalSha256(request) !== row.request_sha256
    ) throw new InvalidNextExperiment("stored next experiment request binding is invalid");
    const task = buildNextExperimentTask(record);
    const now = new Date().toISOString();
    const results = await env.DB.batch([
      env.DB.prepare(
        `INSERT INTO hosted_workspace_capture_tasks
          (task_id, run_id, account_id, candidate_id, candidate_revision, idempotency_key,
           task_json, state, created_at, updated_at, dispatch_mode, kind, required_capability)
         SELECT ?, ?, ?, ?, 1, ?, ?, 'queued', ?, ?, 'worker_broker',
                'marketing_judgment', ?
         WHERE EXISTS (
           SELECT 1 FROM hosted_marketing_next_experiment_requests
           WHERE request_id = ? AND account_id = ? AND state = 'pending' AND task_id IS NULL
         )`,
      ).bind(
        task.task_id, task.run_id, task.account_id, task.payload.campaign_id,
        task.idempotency_key, JSON.stringify(task), now, now, NEXT_EXPERIMENT_CAPABILITY,
        row.request_id, row.account_id,
      ),
      env.DB.prepare(
        `UPDATE hosted_marketing_next_experiment_requests
         SET state = 'queued', task_id = ?, updated_at = ?
         WHERE request_id = ? AND account_id = ? AND state = 'pending' AND task_id IS NULL`,
      ).bind(task.task_id, now, row.request_id, row.account_id),
    ]);
    if (results.every((result) => result?.meta?.changes === 1)) queued += 1;
    else if (results.some((result) => result?.meta?.changes !== 0)) {
      throw new InvalidNextExperiment("next experiment dispatch lost its state race");
    }
  }
  return { queued, waiting_for_worker: false };
}

export function validateNextExperimentDraft(request, draft) {
  requireExactKeys(draft, DRAFT_KEYS, "next experiment draft");
  const lineage = request.source_lineage;
  if (
    draft.schema_version !== NEXT_EXPERIMENT_DRAFT_SCHEMA
    || !/^next-experiment-[a-f0-9]{32}$/.test(draft.draft_id)
    || draft.campaign_id !== request.campaign_id
    || draft.account_id !== request.account_id
    || draft.trigger_evaluation_id !== request.evaluation.evaluation_id
    || draft.trigger_evaluation_sha256 !== lineage.evaluation_sha256
    || draft.trigger_reassessment_id !== request.reassessment.reassessment_id
    || draft.trigger_reassessment_sha256 !== lineage.reassessment_sha256
    || draft.prior_strategy_sha256 !== lineage.strategy_sha256
    || draft.control_hypothesis_id !== request.prior_strategy.hypotheses
      .find((hypothesis) => hypothesis.role === "control")?.hypothesis_id
    || canonicalJson(draft.primary_outcome)
      !== canonicalJson(request.prior_strategy.experiment.primary_outcome)
    || !Array.isArray(draft.held_constant_components)
    || !sameOrderedValues(
      draft.held_constant_components,
      request.prior_strategy.experiment.held_constant_components,
    )
    || draft.effect_class !== "none"
    || draft.state !== "draft"
    || draft.human_review_required !== true
    || !sameTimestamp(draft.created_at, request.reassessment.created_at)
  ) throw new InvalidNextExperiment("next experiment draft identity is invalid");
  if (draft.held_constant_components.length < 1 || draft.held_constant_components.length > 32) {
    throw new InvalidNextExperiment("next experiment held constants are invalid");
  }
  for (const item of draft.held_constant_components) {
    requiredString(item, "next experiment held constant", 1000);
  }

  const eligibleSourceIds = request.reassessment.hypothesis_reassessments
    .filter((item) => item.disposition !== "retire" && typeof item.next_test === "string"
      && item.next_test.trim())
    .map((item) => item.hypothesis_id);
  const sourceIds = uniqueIds(draft.source_hypothesis_ids, "source hypothesis IDs");
  if (!sourceIds.every((sourceId) => eligibleSourceIds.includes(sourceId))) {
    throw new InvalidNextExperiment("next experiment draft source hypotheses changed");
  }
  const supportingClaims = uniqueIds(draft.supporting_claim_ids, "supporting claim IDs");
  if (!supportingClaims.every((claimId) => request.supported_claim_ids.includes(claimId))) {
    throw new InvalidNextExperiment("next experiment draft claim support changed");
  }
  const dispositions = request.reassessment.decision_dossier.evidence_dispositions;
  const requiredEvidenceIds = dispositions.map((item) => item.evidence_id);
  const requiredCounterIds = dispositions
    .filter((item) => ["contradicts", "insufficient"].includes(item.disposition))
    .map((item) => item.evidence_id);
  const evidence = interpretationList(draft.evidence, "draft evidence", 1);
  const counterevidence = interpretationList(draft.counterevidence, "draft counterevidence", 0);
  if (!sameSet(evidence.map((item) => item.evidence_id), requiredEvidenceIds)
      || !sameSet(counterevidence.map((item) => item.evidence_id), requiredCounterIds)) {
    throw new InvalidNextExperiment("next experiment draft evidence coverage changed");
  }
  statementList(draft.assumptions, "draft assumptions", 1);
  statementList(draft.unresolved_questions, "draft unresolved questions", 0);
  requireExactKeys(draft.candidate, CANDIDATE_KEYS, "next experiment candidate");
  const parentIds = uniqueIds(draft.candidate.parent_hypothesis_ids, "candidate parent IDs");
  const claimIds = uniqueIds(draft.candidate.claim_ids, "candidate claim IDs");
  const parentClaimIds = request.prior_strategy.hypotheses
    .filter((hypothesis) => parentIds.includes(hypothesis.hypothesis_id))
    .flatMap((hypothesis) => hypothesis.claim_ids);
  if (!sameOrderedValues(parentIds, sourceIds)
      || !sameOrderedValues(claimIds, supportingClaims)
      || !parentIds.every((parentId) => eligibleSourceIds.includes(parentId))
      || !claimIds.every((claimId) => parentClaimIds.includes(claimId))) {
    throw new InvalidNextExperiment("next experiment candidate lineage changed");
  }
  for (const [key, maximum] of [
    ["audience_situation", 2000], ["belief_to_change", 1000], ["hypothesis", 2000],
    ["rationale", 2000], ["manipulated_component", 500], ["treatment_concept", 2000],
    ["expected_signal", 1000], ["falsifier", 1000],
  ]) requiredString(draft.candidate[key], `candidate ${key}`, maximum);
  const manipulatedComponent = canonicalComponent(draft.candidate.manipulated_component);
  const heldConstantComponents = new Set(
    draft.held_constant_components.map(canonicalComponent),
  );
  if (heldConstantComponents.has(manipulatedComponent)) {
    throw new InvalidNextExperiment("next experiment candidate mutates a held constant");
  }
}

function canonicalComponent(value) {
  return value.normalize("NFKC")
    .replace(/^[\u0009-\u000d\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+|[\u0009-\u000d\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+$/gu, "")
    .toLowerCase()
    .replaceAll("ß", "ss")
    .replaceAll("ς", "σ");
}

export function validateNextExperimentAdmission(request, admission) {
  requireExactKeys(admission, ADMISSION_KEYS, "next experiment admission");
  const lineage = request.source_lineage;
  if (
    admission.schema_version !== "trace.next-experiment-admission.v1"
    || admission.state !== "ready_for_review"
    || admission.evidence_sha256 !== lineage.evaluation_sha256
    || admission.reassessment_sha256 !== lineage.reassessment_sha256
    || admission.source_strategy_sha256 !== lineage.strategy_sha256
    || admission.human_review_required !== true
    || admission.effect_class !== "none"
  ) throw new InvalidNextExperiment("next experiment admission is invalid");
}

export async function nextExperimentDraftRecord(requestRecord, draft, admission) {
  validateNextExperimentDraft(requestRecord.request, draft);
  validateNextExperimentAdmission(requestRecord.request, admission);
  const expectedDraftId = await expectedNextExperimentDraftId(requestRecord.request);
  if (draft.draft_id !== expectedDraftId) {
    throw new InvalidNextExperiment("next experiment draft identity is not host-derived");
  }
  return {
    draft,
    draft_sha256: await canonicalSha256(draft),
    admission,
    admission_sha256: await canonicalSha256(admission),
    request_id: requestRecord.request.request_id,
    request_sha256: requestRecord.request_sha256,
    account_id: requestRecord.request.account_id,
    source_campaign_id: requestRecord.request.campaign_id,
    source_lineage_sha256: requestRecord.source_lineage_sha256,
  };
}

export async function expectedNextExperimentDraftId(request) {
  const lineage = requireObject(request?.source_lineage, "next experiment source lineage");
  const identity = `${requiredId(request.campaign_id, "campaign ID")}:`+
    `${requiredSha(lineage.evaluation_sha256, "evaluation SHA")}:`+
    `${requiredSha(lineage.reassessment_sha256, "reassessment SHA")}:`+
    `${requiredSha(lineage.strategy_sha256, "strategy SHA")}`;
  return `next-experiment-${(await sha256Text(identity)).slice(0, 32)}`;
}

/** Public status gets identity/state only; full reasoning and candidate content stay protected. */
export function publicNextExperimentSummary(row) {
  if (!row) return null;
  return {
    request_id: row.request_id,
    request_sha256: row.request_sha256,
    request_state: row.request_state ?? row.state,
    draft_id: row.draft_id ?? null,
    draft_sha256: row.draft_sha256 ?? null,
    draft_state: row.draft_state ?? null,
  };
}

export class InvalidNextExperiment extends Error {}

function validateAgentRunLineage(lineage) {
  const fields = [lineage.agent_run_id, lineage.research_session_id, lineage.research_input_sha256,
    lineage.research_trace_sha256, lineage.research_continuation_sha256];
  if (fields.every((value) => value == null)) return;
  if (fields.some((value) => value == null)) {
    throw new InvalidNextExperiment("agent run lineage is incomplete");
  }
  requiredId(lineage.agent_run_id, "agent run ID");
  requiredId(lineage.research_session_id, "research session ID");
  for (const value of fields.slice(2)) requiredSha(value, "research lineage SHA");
}

async function requireCanonicalHash(value, expected, name) {
  if (await canonicalSha256(value) !== expected) {
    throw new InvalidNextExperiment(`${name} digest is invalid`);
  }
}

function requireExactKeys(value, keys, name) {
  const object = requireObject(value, name);
  if (Object.keys(object).length !== keys.size || Object.keys(object).some((key) => !keys.has(key))) {
    throw new InvalidNextExperiment(`${name} fields are invalid`);
  }
  return object;
}

function requireObject(value, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new InvalidNextExperiment(`${name} is invalid`);
  }
  return value;
}

function requiredId(value, name) {
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value)) {
    throw new InvalidNextExperiment(`${name} is invalid`);
  }
  return value;
}

function requiredSha(value, name) {
  if (typeof value !== "string" || !/^[a-f0-9]{64}$/.test(value)) {
    throw new InvalidNextExperiment(`${name} is invalid`);
  }
  return value;
}

function requiredTimestamp(value, name) {
  if (typeof value !== "string" || !Number.isFinite(Date.parse(value))) {
    throw new InvalidNextExperiment(`${name} is invalid`);
  }
  return value;
}

function requiredString(value, name, maximum) {
  if (typeof value !== "string" || !value.trim() || value.length > maximum) {
    throw new InvalidNextExperiment(`${name} is invalid`);
  }
  return value;
}

function uniqueIds(value, name) {
  if (!Array.isArray(value)) throw new InvalidNextExperiment(`${name} is invalid`);
  const ids = value.map((item) => requiredId(item, name));
  if (ids.length === 0 || new Set(ids).size !== ids.length) {
    throw new InvalidNextExperiment(`${name} is empty or repeated`);
  }
  return ids;
}

function statementList(value, name, minimum) {
  if (!Array.isArray(value) || value.length < minimum || value.length > 16) {
    throw new InvalidNextExperiment(`${name} is invalid`);
  }
  for (const item of value) requiredString(item, name, 2000);
  return value;
}

function interpretationList(value, name, minimum) {
  if (!Array.isArray(value) || value.length < minimum || value.length > 256) {
    throw new InvalidNextExperiment(`${name} is invalid`);
  }
  const ids = [];
  for (const item of value) {
    requireExactKeys(item, EVIDENCE_KEYS, name);
    ids.push(requiredId(item.evidence_id, `${name} ID`));
    requiredString(item.interpretation, `${name} interpretation`, 2000);
  }
  if (new Set(ids).size !== ids.length) throw new InvalidNextExperiment(`${name} IDs repeat`);
  return value;
}

function sameOrderedValues(left, right) {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function sameTimestamp(left, right) {
  return typeof left === "string" && typeof right === "string"
    && Number.isFinite(Date.parse(left)) && Date.parse(left) === Date.parse(right)
    && left.endsWith("Z") && right.endsWith("Z");
}

function sameSet(left, right) {
  return left.length === right.length && new Set(left).size === left.length
    && left.every((value) => right.includes(value));
}

export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) =>
      `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export async function canonicalSha256(value) {
  return sha256Text(canonicalJson(value));
}

async function sha256Text(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
