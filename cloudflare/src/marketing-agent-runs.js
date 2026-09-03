import {
  hasOnlineMarketingWorker,
  marketingJudgmentCapability,
} from "./marketing-worker-capabilities.js";
import {
  canonicalJson,
  canonicalSha256,
  deriveResearchCapabilitySnapshot,
} from "./marketing-run-capabilities.js";

export { canonicalJson, canonicalSha256 } from "./marketing-run-capabilities.js";

export const HOSTED_AGENT_RUN_PIPELINE = "hosted_marketing_agent_run_v5";
const MAX_TASK_BYTES = 64 * 1024;

export async function enqueueMarketingAgentRun(env, account, launchRequest) {
  const runId = launchRequest.agent_run_id;
  const requestSha256 = await canonicalSha256(launchRequest);
  let capability;
  try {
    capability = await deriveResearchCapabilitySnapshot(
      launchRequest?.research?.required_scopes,
    );
  } catch {
    throw new MarketingAgentRunError(400, "required_scopes가 올바르지 않습니다.");
  }
  const idempotencyKey = `marketing-agent-run:${account.account_id}:${runId}`;
  const existing = await findRunByIdentity(env.DB, account.account_id, runId, idempotencyKey);
  if (existing) {
    if (existing.request_sha256 !== requestSha256) {
      throw new MarketingAgentRunError(409, "run ID가 다른 요청에 이미 사용됐습니다.");
    }
    return marketingAgentRunStatus(env.DB, account.account_id, runId);
  }
  const modelId = requiredModelId(env.MARKETING_AGENT_MODEL);
  if (!(await hasOnlineMarketingWorker(env.DB, "feature_launch_run"))) {
    throw new MarketingAgentRunError(
      503,
      "동적 조사 실행을 지원하는 온라인 Mac 워커가 없습니다.",
    );
  }
  const taskId = crypto.randomUUID();
  const now = new Date().toISOString();
  const resumableScopes = launchRequest.research.required_scopes.includes("customer_intelligence")
    ? ["customer_intelligence"]
    : [];
  const task = {
    schema_version: "1",
    task_id: taskId,
    // Broker task run IDs are globally unique. The product run ID stays in the bound payload so
    // the later campaign strategy task may continue using campaign_id as its run_id.
    run_id: `agent-task-${taskId}`,
    account_id: account.account_id,
    kind: "marketing_judgment",
    idempotency_key: `feature-launch-run:${account.account_id}:${runId}`,
    payload: {
      pipeline: HOSTED_AGENT_RUN_PIPELINE,
      judgment: "feature_launch_run",
      run_id: runId,
      request_sha256: requestSha256,
      launch_request: launchRequest,
      model_id: modelId,
      capability_snapshot: capability.snapshot,
      capability_snapshot_sha256: capability.sha256,
      requested_by: "hosted_workspace",
      phase: "initial",
      step_sequence: 1,
      parent_step_sha256: null,
      root_request_sha256: requestSha256,
      resumable_scopes: resumableScopes,
    },
    created_at: now,
    credential_ref: null,
  };
  const taskJson = JSON.stringify(task);
  if (new TextEncoder().encode(taskJson).byteLength > MAX_TASK_BYTES) {
    throw new MarketingAgentRunError(413, "marketing agent run task가 너무 큽니다.");
  }
  try {
    await env.DB.batch([
      env.DB.prepare(
        `INSERT INTO hosted_workspace_capture_tasks
          (task_id, run_id, account_id, candidate_id, candidate_revision, idempotency_key,
           task_json, state, dispatch_mode, kind, required_capability, created_at, updated_at)
         VALUES (?, ?, ?, '', 1, ?, ?, 'queued', 'worker_broker', 'marketing_judgment',
                 ?, ?, ?)`,
      ).bind(
        taskId,
        task.run_id,
        account.account_id,
        task.idempotency_key,
        taskJson,
        marketingJudgmentCapability("feature_launch_run"),
        now,
        now,
      ),
      env.DB.prepare(
        `INSERT INTO hosted_marketing_agent_runs
          (run_id, account_id, schema_version, request_json, request_sha256,
           idempotency_key, task_id, state, created_at, updated_at,
           capability_snapshot_json, capability_snapshot_sha256, active_task_id,
           loop_state, loop_revision, cumulative_cost_units, completed_steps)
         VALUES (?, ?, 'trace.feature-launch-run-request.v1', ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?,
                 'running', 1, 0, 0)`,
      ).bind(
        runId,
        account.account_id,
        canonicalJson(launchRequest),
        requestSha256,
        idempotencyKey,
        taskId,
        now,
        now,
        canonicalJson(capability.snapshot),
        capability.sha256,
        taskId,
      ),
      env.DB.prepare(
        `INSERT INTO hosted_marketing_agent_run_tasks
          (task_id, run_id, account_id, sequence, phase, parent_step_sha256,
           root_request_sha256, request_json, request_sha256, capability_snapshot_json,
           capability_snapshot_sha256, resumable_scopes_json, created_at)
         VALUES (?, ?, ?, 1, 'initial', NULL, ?, ?, ?, ?, ?, ?, ?)`,
      ).bind(
        taskId,
        runId,
        account.account_id,
        requestSha256,
        canonicalJson(launchRequest),
        requestSha256,
        canonicalJson(capability.snapshot),
        capability.sha256,
        canonicalJson(resumableScopes),
        now,
      ),
    ]);
  } catch (error) {
    const winner = await findRunByIdentity(env.DB, account.account_id, runId, idempotencyKey);
    if (!winner) throw error;
    if (winner.request_sha256 !== requestSha256) {
      throw new MarketingAgentRunError(409, "run ID가 다른 요청에 이미 사용됐습니다.");
    }
  }
  return marketingAgentRunStatus(env.DB, account.account_id, runId);
}

export async function resumeMarketingAgentRun(env, account, runId, input, resolveContext) {
  assertExactResumeKeys(input);
  if (input.schema_version !== "trace.marketing-agent-resume-request.v1") {
    throw new MarketingAgentRunError(400, "resume request schema가 올바르지 않습니다.");
  }
  const resumeId = boundedId(input.resume_id, "resume_id");
  const expectedHead = requiredSha256(input.expected_head_step_sha256, "expected head");
  const snapshotId = boundedId(input.marketing_context_snapshot_id, "marketing context snapshot");
  const resumeRequest = {
    schema_version: input.schema_version,
    resume_id: resumeId,
    expected_head_step_sha256: expectedHead,
    marketing_context_snapshot_id: snapshotId,
  };
  const resumeRequestSha256 = await canonicalSha256(resumeRequest);
  const existing = await env.DB.prepare(
    `SELECT mapping.resume_request_sha256
     FROM hosted_marketing_agent_run_tasks AS mapping
     WHERE mapping.account_id = ? AND mapping.run_id = ? AND mapping.resume_id = ?`,
  ).bind(account.account_id, runId, resumeId).first();
  if (existing) {
    if (existing.resume_request_sha256 !== resumeRequestSha256) {
      throw new MarketingAgentRunError(409, "resume ID가 다른 요청에 이미 사용됐습니다.");
    }
    return marketingAgentRunStatus(env.DB, account.account_id, runId);
  }
  const run = await env.DB.prepare(
    `SELECT run.*, step.decision_json
     FROM hosted_marketing_agent_runs AS run
     LEFT JOIN hosted_marketing_agent_run_steps AS step
       ON step.run_id = run.run_id AND step.sequence = run.completed_steps
     WHERE run.account_id = ? AND run.run_id = ?`,
  ).bind(account.account_id, runId).first();
  if (!run) throw new MarketingAgentRunError(404, "marketing agent run을 찾을 수 없습니다.");
  let decision;
  let rootRequest;
  try {
    decision = JSON.parse(run.decision_json);
    rootRequest = JSON.parse(run.request_json);
  } catch {
    throw new MarketingAgentRunError(409, "resume source record가 손상되었습니다.");
  }
  if (
    run.state !== "blocked"
    || run.loop_state !== "needs_input"
    || run.active_task_id !== null
    || run.completed_steps !== 1
    || run.head_step_sha256 !== expectedHead
    || decision.intent_id !== "request_more_evidence"
    || decision.requested_scope !== "customer_intelligence"
    || !rootRequest.research.required_scopes.includes("customer_intelligence")
  ) throw new MarketingAgentRunError(409, "marketing agent run은 resume할 수 없습니다.");
  if (!(await hasOnlineMarketingWorker(env.DB, "feature_launch_run"))) {
    throw new MarketingAgentRunError(503, "resume을 지원하는 온라인 Mac 워커가 없습니다.");
  }
  const marketingContext = await resolveContext(env.DB, account.account_id, snapshotId);
  const launchRequest = structuredClone(rootRequest);
  launchRequest.research.session_id = `resume-${resumeId}`;
  launchRequest.research.marketing_context = marketingContext;
  launchRequest.marketing_context_snapshot_id = snapshotId;
  const requestSha256 = await canonicalSha256(launchRequest);
  const capability = await deriveResearchCapabilitySnapshot(
    launchRequest.research.required_scopes,
  );
  const taskId = crypto.randomUUID();
  const now = new Date().toISOString();
  const task = {
    schema_version: "1",
    task_id: taskId,
    run_id: `agent-task-${taskId}`,
    account_id: account.account_id,
    kind: "marketing_judgment",
    idempotency_key: `feature-launch-resume:${account.account_id}:${runId}:${resumeId}`,
    payload: {
      pipeline: HOSTED_AGENT_RUN_PIPELINE,
      judgment: "feature_launch_run",
      run_id: runId,
      request_sha256: requestSha256,
      launch_request: launchRequest,
      model_id: requiredModelId(env.MARKETING_AGENT_MODEL),
      capability_snapshot: capability.snapshot,
      capability_snapshot_sha256: capability.sha256,
      requested_by: "hosted_workspace",
      phase: "resume",
      step_sequence: 2,
      parent_step_sha256: expectedHead,
      root_request_sha256: run.request_sha256,
      resumable_scopes: [],
    },
    created_at: now,
    credential_ref: null,
  };
  const taskJson = JSON.stringify(task);
  if (new TextEncoder().encode(taskJson).byteLength > MAX_TASK_BYTES) {
    throw new MarketingAgentRunError(413, "marketing agent resume task가 너무 큽니다.");
  }
  try {
    const results = await env.DB.batch([
      env.DB.prepare(
        `INSERT INTO hosted_workspace_capture_tasks
          (task_id, run_id, account_id, candidate_id, candidate_revision, idempotency_key,
           task_json, state, dispatch_mode, kind, required_capability, created_at, updated_at)
         VALUES (?, ?, ?, '', 1, ?, ?, 'queued', 'worker_broker', 'marketing_judgment', ?, ?, ?)`,
      ).bind(taskId, task.run_id, account.account_id, task.idempotency_key, taskJson,
        marketingJudgmentCapability("feature_launch_run"), now, now),
      env.DB.prepare(
        `INSERT INTO hosted_marketing_agent_run_tasks
          (task_id, run_id, account_id, sequence, phase, parent_step_sha256,
           root_request_sha256, request_json, request_sha256, capability_snapshot_json,
           capability_snapshot_sha256, resumable_scopes_json, resume_id,
           resume_request_json, resume_request_sha256, created_at)
         VALUES (?, ?, ?, 2, 'resume', ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, ?)`,
      ).bind(taskId, runId, account.account_id, expectedHead, run.request_sha256,
        canonicalJson(launchRequest), requestSha256, canonicalJson(capability.snapshot),
        capability.sha256, resumeId, canonicalJson(resumeRequest), resumeRequestSha256, now),
      env.DB.prepare(
        `UPDATE hosted_marketing_agent_runs
         SET state = 'queued', research_result_json = NULL, research_result_sha256 = NULL,
             failure_code = NULL, active_task_id = ?, loop_state = 'running',
             loop_revision = loop_revision + 1, updated_at = ?
         WHERE run_id = ? AND account_id = ? AND state = 'blocked'
           AND loop_state = 'needs_input' AND active_task_id IS NULL
           AND head_step_sha256 = ? AND completed_steps = 1`,
      ).bind(taskId, now, runId, account.account_id, expectedHead),
    ]);
    if (results.at(-1).meta.changes !== 1) {
      throw new MarketingAgentRunError(409, "resume head가 변경되었습니다.");
    }
  } catch (error) {
    const winner = await env.DB.prepare(
      `SELECT resume_request_sha256 FROM hosted_marketing_agent_run_tasks
       WHERE account_id = ? AND run_id = ? AND resume_id = ?`,
    ).bind(account.account_id, runId, resumeId).first();
    if (!winner) {
      const current = await env.DB.prepare(
        `SELECT head_step_sha256, active_task_id, state, loop_state, completed_steps
         FROM hosted_marketing_agent_runs WHERE account_id = ? AND run_id = ?`,
      ).bind(account.account_id, runId).first();
      if (current && (
        current.head_step_sha256 !== expectedHead
        || current.active_task_id !== null
        || current.state !== "blocked"
        || current.loop_state !== "needs_input"
        || current.completed_steps !== 1
      )) throw new MarketingAgentRunError(409, "resume head가 변경되었습니다.");
      throw error;
    }
    if (winner.resume_request_sha256 !== resumeRequestSha256) {
      throw new MarketingAgentRunError(409, "resume ID가 다른 요청에 이미 사용됐습니다.");
    }
  }
  return marketingAgentRunStatus(env.DB, account.account_id, runId);
}

export async function marketingAgentRunStatus(database, accountId, runId) {
  const row = await database.prepare(
    `SELECT run.run_id, run.account_id, run.request_sha256, run.state,
            run.research_result_sha256, run.campaign_id, run.failure_code,
            run.capability_snapshot_sha256, run.next_intent_json, run.next_intent_sha256,
            run.head_step_sha256, run.active_task_id, run.loop_state, run.loop_revision,
            run.cumulative_cost_units, run.completed_steps,
            (SELECT delegation.state FROM hosted_marketing_agent_run_delegations AS delegation
             WHERE delegation.run_id = run.run_id) AS delegation_state,
            ((SELECT COUNT(*) FROM hosted_marketing_agent_run_receipts AS receipt
              WHERE receipt.run_id = run.run_id)
             + (SELECT COUNT(*) FROM hosted_marketing_agent_run_task_receipts AS receipt
                WHERE receipt.run_id = run.run_id)) AS receipt_count,
            (SELECT COUNT(*) FROM hosted_marketing_agent_run_steps AS step
             WHERE step.run_id = run.run_id) AS step_count,
            run.created_at, run.updated_at, task.task_id, task.state AS task_state,
            task.execution_started_at, task.result_json, campaign.state AS campaign_state
     FROM hosted_marketing_agent_runs AS run
     JOIN hosted_workspace_capture_tasks AS task ON task.task_id = COALESCE(
       run.active_task_id,
       (SELECT mapping.task_id FROM hosted_marketing_agent_run_tasks AS mapping
        WHERE mapping.run_id = run.run_id ORDER BY mapping.sequence DESC LIMIT 1)
     )
     LEFT JOIN hosted_marketing_campaigns AS campaign
       ON campaign.campaign_id = run.campaign_id AND campaign.account_id = run.account_id
     WHERE run.account_id = ? AND run.run_id = ?`,
  ).bind(accountId, runId).first();
  if (!row) throw new MarketingAgentRunError(404, "marketing agent run을 찾을 수 없습니다.");
  return publicRunStatus(row);
}

export async function listMarketingAgentRuns(database, accountId) {
  const rows = await database.prepare(
    `SELECT run.run_id, run.account_id, run.request_sha256, run.state,
            run.research_result_sha256, run.campaign_id, run.failure_code,
            run.capability_snapshot_sha256, run.next_intent_json, run.next_intent_sha256,
            run.head_step_sha256, run.active_task_id, run.loop_state, run.loop_revision,
            run.cumulative_cost_units, run.completed_steps,
            (SELECT delegation.state FROM hosted_marketing_agent_run_delegations AS delegation
             WHERE delegation.run_id = run.run_id) AS delegation_state,
            ((SELECT COUNT(*) FROM hosted_marketing_agent_run_receipts AS receipt
              WHERE receipt.run_id = run.run_id)
             + (SELECT COUNT(*) FROM hosted_marketing_agent_run_task_receipts AS receipt
                WHERE receipt.run_id = run.run_id)) AS receipt_count,
            (SELECT COUNT(*) FROM hosted_marketing_agent_run_steps AS step
             WHERE step.run_id = run.run_id) AS step_count,
            run.created_at, run.updated_at, task.task_id, task.state AS task_state,
            task.execution_started_at, task.result_json, campaign.state AS campaign_state
     FROM hosted_marketing_agent_runs AS run
     JOIN hosted_workspace_capture_tasks AS task ON task.task_id = COALESCE(
       run.active_task_id,
       (SELECT mapping.task_id FROM hosted_marketing_agent_run_tasks AS mapping
        WHERE mapping.run_id = run.run_id ORDER BY mapping.sequence DESC LIMIT 1)
     )
     LEFT JOIN hosted_marketing_campaigns AS campaign
       ON campaign.campaign_id = run.campaign_id AND campaign.account_id = run.account_id
     WHERE run.account_id = ? ORDER BY run.created_at DESC, run.run_id DESC LIMIT 50`,
  ).bind(accountId).all();
  return rows.results.map(publicRunStatus);
}

function publicRunStatus(row) {
  const state = row.delegation_state === "pending"
    ? "delegation_pending"
    : (row.state === "queued" && row.execution_started_at ? "running" : row.state);
  return {
    schema_version: "trace.marketing-agent-run-status.v1",
    run_id: row.run_id,
    account_id: row.account_id,
    request_sha256: row.request_sha256,
    state,
    task: {
      task_id: row.task_id,
      state: row.task_state,
      execution_started_at: row.execution_started_at ?? null,
    },
    campaign_id: row.campaign_id ?? null,
    campaign_state: row.campaign_state ?? null,
    research_result_sha256: row.research_result_sha256 ?? null,
    capability_snapshot_sha256: row.capability_snapshot_sha256 ?? null,
    receipt_count: Number(row.receipt_count ?? 0),
    next_intent: safeNextIntent(row.next_intent_json),
    next_intent_sha256: row.next_intent_sha256 ?? null,
    step_count: Number(row.step_count ?? 0),
    loop: {
      state: row.loop_state,
      revision: Number(row.loop_revision),
      head_step_sha256: row.head_step_sha256 ?? null,
      active_task_id: row.active_task_id ?? null,
      completed_steps: Number(row.completed_steps ?? 0),
      cumulative_cost_units: Number(row.cumulative_cost_units ?? 0),
    },
    failure_code: row.delegation_state === "pending"
      ? null
      : (row.failure_code ?? taskFailureCode(row.result_json)),
    links: {
      self: `/api/marketing-agent/runs/${encodeURIComponent(row.run_id)}`,
      campaign: row.campaign_id
        ? `/api/marketing-agent/campaigns/${encodeURIComponent(row.campaign_id)}`
        : null,
      review_queue: row.campaign_id ? "/api/marketing-agent/review-queue" : null,
    },
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

export async function storedMarketingAgentRun(database, taskId) {
  return database.prepare(
    `SELECT run.run_id, run.account_id, mapping.request_json, mapping.request_sha256, run.state,
            mapping.task_id, run.task_id AS root_task_id, run.campaign_id,
            run.research_result_sha256, run.failure_code, mapping.capability_snapshot_json,
            mapping.capability_snapshot_sha256, mapping.sequence AS step_sequence,
            mapping.phase, mapping.parent_step_sha256, mapping.root_request_sha256,
            mapping.resumable_scopes_json, run.head_step_sha256, run.active_task_id,
            run.loop_state, run.loop_revision, run.cumulative_cost_units, run.completed_steps
     FROM hosted_marketing_agent_runs AS run
     JOIN hosted_marketing_agent_run_tasks AS mapping ON mapping.run_id = run.run_id
     WHERE mapping.task_id = ?`,
  ).bind(taskId).first();
}

async function findRunByIdentity(database, accountId, runId, idempotencyKey) {
  return database.prepare(
    `SELECT run_id, request_sha256 FROM hosted_marketing_agent_runs
     WHERE account_id = ? AND (run_id = ? OR idempotency_key = ?)`,
  ).bind(accountId, runId, idempotencyKey).first();
}

function requiredModelId(value) {
  if (typeof value !== "string" || !value.trim() || value.length > 240) {
    throw new MarketingAgentRunError(503, "MARKETING_AGENT_MODEL 설정이 필요합니다.");
  }
  return value.trim();
}

function assertExactResumeKeys(value) {
  const expected = [
    "schema_version",
    "resume_id",
    "expected_head_step_sha256",
    "marketing_context_snapshot_id",
  ].sort();
  if (!value || typeof value !== "object" || Array.isArray(value)
      || canonicalJson(Object.keys(value).sort()) !== canonicalJson(expected)) {
    throw new MarketingAgentRunError(400, "resume request shape가 올바르지 않습니다.");
  }
}

function boundedId(value, field) {
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$/.test(value)) {
    throw new MarketingAgentRunError(400, `${field}가 올바르지 않습니다.`);
  }
  return value;
}

function requiredSha256(value, field) {
  if (typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value)) {
    throw new MarketingAgentRunError(400, `${field} digest가 올바르지 않습니다.`);
  }
  return value;
}

function taskFailureCode(resultJson) {
  if (typeof resultJson !== "string") return null;
  try {
    const result = JSON.parse(resultJson);
    return typeof result?.failure_code === "string" ? result.failure_code : null;
  } catch {
    return null;
  }
}

function safeNextIntent(value) {
  if (typeof value !== "string") return null;
  try {
    const intent = JSON.parse(value);
    if (!intent || typeof intent !== "object" || Array.isArray(intent)) return null;
    return {
      intent_id: typeof intent.intent_id === "string" ? intent.intent_id : null,
      requested_scope: typeof intent.requested_scope === "string"
        ? intent.requested_scope
        : null,
    };
  } catch {
    return null;
  }
}

export class MarketingAgentRunError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}
