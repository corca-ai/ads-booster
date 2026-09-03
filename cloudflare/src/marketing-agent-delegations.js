import { createShadowCampaign } from "./marketing-agent.js";
import { canonicalJson, canonicalSha256 } from "./marketing-run-capabilities.js";

const DELEGATION_SCHEMA = "trace.marketing-agent-shadow-delegation.v1";

export async function marketingAgentDelegationRecord({
  run,
  task,
  stepSha256,
  researchResultSha256,
  campaignRequest,
  marketResearchSeed,
  createdAt,
}) {
  const value = {
    schema_version: DELEGATION_SCHEMA,
    delegation_id: `shadow-delegation:${run.account_id}:${run.run_id}`,
    run_id: run.run_id,
    account_id: run.account_id,
    task_id: task.task_id,
    step_sha256: stepSha256,
    research_result_sha256: researchResultSha256,
    campaign_id: run.run_id,
    campaign_request: campaignRequest,
    market_research_seed: marketResearchSeed,
    created_at: createdAt,
  };
  return { value, sha256: await canonicalSha256(value) };
}

export function marketingAgentDelegationInsertStatement(database, record) {
  const value = record.value;
  return database.prepare(
    `INSERT INTO hosted_marketing_agent_run_delegations
      (delegation_id, run_id, account_id, task_id, step_sha256,
       research_result_sha256, campaign_id, schema_version, delegation_json,
       delegation_sha256, state, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)`,
  ).bind(
    value.delegation_id,
    value.run_id,
    value.account_id,
    value.task_id,
    value.step_sha256,
    value.research_result_sha256,
    value.campaign_id,
    value.schema_version,
    canonicalJson(value),
    record.sha256,
    value.created_at,
    value.created_at,
  );
}

export async function runDueMarketingAgentDelegations(
  env,
  {
    limit = 10,
    afterCampaign = null,
    reconcile = reconcileMarketingAgentDelegation,
    now = new Date(),
  } = {},
) {
  const rows = await env.DB.prepare(
    `SELECT delegation.*, run.state AS run_state, run.failure_code AS run_failure_code,
            run.loop_state, run.active_task_id, run.head_step_sha256,
            run.research_result_sha256 AS run_research_result_sha256,
            step.step_sha256 AS stored_step_sha256,
            step.research_result_sha256 AS step_research_result_sha256,
            step.decision_json, mapping.request_json AS launch_request_json
     FROM hosted_marketing_agent_run_delegations AS delegation
     JOIN hosted_marketing_agent_runs AS run
       ON run.run_id = delegation.run_id AND run.account_id = delegation.account_id
     JOIN hosted_marketing_agent_run_steps AS step
       ON step.run_id = delegation.run_id AND step.task_id = delegation.task_id
      AND step.step_sha256 = delegation.step_sha256
     JOIN hosted_marketing_agent_run_tasks AS mapping
       ON mapping.task_id = delegation.task_id AND mapping.run_id = delegation.run_id
     WHERE delegation.state = 'pending'
       AND (delegation.next_attempt_at IS NULL OR delegation.next_attempt_at <= ?)
     ORDER BY delegation.created_at, delegation.delegation_id LIMIT ?`,
  ).bind(
    now.toISOString(),
    Math.max(1, Math.min(Number(limit) || 10, 100)),
  ).all();
  let finalized = 0;
  let raced = 0;
  let invalid = 0;
  for (const row of rows.results) {
    let outcome;
    try {
      outcome = await reconcile(env, row.delegation_id, {
        afterCampaign,
      });
    } catch {
      // Keep the immutable payload pending for operator inspection and durably defer this row so
      // corrupt or temporarily unavailable work cannot starve later server-owned delegations.
      invalid += 1;
      await deferDelegation(env.DB, row, now);
      continue;
    }
    if (outcome.finalized) finalized += 1;
    if (outcome.duplicate) raced += 1;
  }
  return { finalized, raced, invalid };
}

async function deferDelegation(database, row, now) {
  const attemptCount = Math.min(Number(row.attempt_count ?? 0) + 1, 12);
  const delaySeconds = Math.min(3600, 30 * (2 ** Math.min(attemptCount - 1, 7)));
  const nextAttemptAt = new Date(now.getTime() + delaySeconds * 1000).toISOString();
  await database.prepare(
    `UPDATE hosted_marketing_agent_run_delegations
     SET attempt_count = ?, next_attempt_at = ?,
         last_failure_code = 'delegation_reconcile_failed', updated_at = ?
     WHERE delegation_id = ? AND state = 'pending' AND attempt_count = ?`,
  ).bind(
    attemptCount,
    nextAttemptAt,
    now.toISOString(),
    row.delegation_id,
    Number(row.attempt_count ?? 0),
  ).run();
}

export async function reconcileMarketingAgentDelegation(
  env,
  delegationId,
  { afterCampaign = null } = {},
) {
  const row = await loadDelegation(env.DB, delegationId);
  if (!row) return { finalized: false, duplicate: true };
  if (row.state === "finalized") {
    return { finalized: true, duplicate: true, campaign_id: row.campaign_id };
  }
  const delegation = await validateDelegation(row);
  const account = await loadAccount(env.DB, row.account_id);
  let campaign;
  try {
    campaign = await createShadowCampaign(
      env,
      account,
      delegation.campaign_request,
      { marketResearchSeed: delegation.market_research_seed },
    );
  } catch (error) {
    const current = await loadDelegation(env.DB, delegationId);
    if (current?.state === "finalized") {
      return { finalized: true, duplicate: true, campaign_id: current.campaign_id };
    }
    const existingCampaign = await campaignTask(env.DB, row.account_id, row.campaign_id);
    if (!existingCampaign) throw error;
    campaign = { campaign_id: row.campaign_id, task_id: existingCampaign.task_id };
  }
  if (afterCampaign) await afterCampaign({ delegation, campaign });
  const task = await campaignTask(env.DB, row.account_id, row.campaign_id);
  if (!task) throw new Error("marketing agent delegated campaign task is missing");
  const now = new Date().toISOString();
  let results;
  try {
    results = await env.DB.batch([
      env.DB.prepare(
        `UPDATE hosted_marketing_agent_runs
         SET state = 'campaign_created', campaign_id = ?, failure_code = NULL,
             loop_state = 'delegated', updated_at = ?
         WHERE run_id = ? AND account_id = ? AND state = 'blocked'
           AND failure_code = 'shadow_strategy_delegation_pending'
           AND active_task_id IS NULL AND head_step_sha256 = ?
           AND research_result_sha256 = ?`,
      ).bind(
        row.campaign_id,
        now,
        row.run_id,
        row.account_id,
        row.step_sha256,
        row.research_result_sha256,
      ),
      env.DB.prepare(
        `UPDATE hosted_marketing_agent_run_delegations
         SET state = 'finalized', campaign_task_id = ?, finalized_at = ?, updated_at = ?
         WHERE delegation_id = ? AND state = 'pending'`,
      ).bind(task.task_id, now, now, delegationId),
    ]);
  } catch (error) {
    const current = await loadDelegation(env.DB, delegationId);
    if (current?.state === "finalized") {
      return { finalized: true, duplicate: true, campaign_id: current.campaign_id };
    }
    throw error;
  }
  if (results[0].meta.changes !== 1 || results[1].meta.changes !== 1) {
    const current = await loadDelegation(env.DB, delegationId);
    if (current?.state === "finalized") {
      return { finalized: true, duplicate: true, campaign_id: current.campaign_id };
    }
    throw new Error("marketing agent delegation finalization lost its binding");
  }
  return { finalized: true, duplicate: false, campaign_id: row.campaign_id };
}

async function loadDelegation(database, delegationId) {
  return database.prepare(
    `SELECT delegation.*, run.state AS run_state, run.failure_code AS run_failure_code,
            run.loop_state, run.active_task_id, run.head_step_sha256,
            run.research_result_sha256 AS run_research_result_sha256,
            step.step_sha256 AS stored_step_sha256,
            step.research_result_sha256 AS step_research_result_sha256,
            step.decision_json, mapping.request_json AS launch_request_json
     FROM hosted_marketing_agent_run_delegations AS delegation
     JOIN hosted_marketing_agent_runs AS run
       ON run.run_id = delegation.run_id AND run.account_id = delegation.account_id
     JOIN hosted_marketing_agent_run_steps AS step
       ON step.run_id = delegation.run_id AND step.task_id = delegation.task_id
      AND step.step_sha256 = delegation.step_sha256
     JOIN hosted_marketing_agent_run_tasks AS mapping
       ON mapping.task_id = delegation.task_id AND mapping.run_id = delegation.run_id
     WHERE delegation.delegation_id = ?`,
  ).bind(delegationId).first();
}

async function validateDelegation(row) {
  let delegation;
  let decision;
  let launchRequest;
  try {
    delegation = JSON.parse(row.delegation_json);
    decision = JSON.parse(row.decision_json);
    launchRequest = JSON.parse(row.launch_request_json);
  } catch {
    throw new Error("stored marketing agent delegation is invalid");
  }
  if (
    delegation.schema_version !== DELEGATION_SCHEMA
    || await canonicalSha256(delegation) !== row.delegation_sha256
    || delegation.delegation_id !== row.delegation_id
    || delegation.run_id !== row.run_id
    || delegation.account_id !== row.account_id
    || delegation.task_id !== row.task_id
    || delegation.step_sha256 !== row.step_sha256
    || delegation.research_result_sha256 !== row.research_result_sha256
    || delegation.campaign_id !== row.campaign_id
    || delegation.campaign_id !== row.run_id
    || row.stored_step_sha256 !== row.step_sha256
    || row.step_research_result_sha256 !== row.research_result_sha256
    || row.run_research_result_sha256 !== row.research_result_sha256
    || row.head_step_sha256 !== row.step_sha256
    || row.run_state !== "blocked"
    || row.run_failure_code !== "shadow_strategy_delegation_pending"
    || row.loop_state !== "running"
    || row.active_task_id !== null
    || decision.intent_id !== "propose_shadow_strategy"
    || !delegation.campaign_request
    || delegation.campaign_request.campaign_id !== row.campaign_id
    || delegation.campaign_request.account_id !== row.account_id
    || !delegation.market_research_seed
    || delegation.market_research_seed.sha256
      !== await canonicalSha256(delegation.market_research_seed.proposal)
    || delegation.campaign_request.business_outcome !== launchRequest.business_outcome
    || delegation.campaign_request.current_control !== launchRequest.current_control
    || canonicalJson(delegation.campaign_request.feature_packet)
      !== canonicalJson(launchRequest.research?.feature_packet)
    || delegation.campaign_request.marketing_context_snapshot_id
      !== launchRequest.marketing_context_snapshot_id
    || delegation.campaign_request.research_enabled !== true
    || delegation.campaign_request.mode !== "shadow"
    || delegation.campaign_request.agent_run_lineage?.agent_run_id !== row.run_id
  ) throw new Error("stored marketing agent delegation binding is invalid");
  return delegation;
}

async function loadAccount(database, accountId) {
  const row = await database.prepare(
    `SELECT account_id, country, language, timezone
     FROM hosted_workspace_accounts WHERE account_id = ? AND enabled = 1`,
  ).bind(accountId).first();
  if (!row) throw new Error("marketing agent delegation account is missing");
  return row;
}

async function campaignTask(database, accountId, campaignId) {
  return database.prepare(
    `SELECT task_id FROM hosted_workspace_capture_tasks
     WHERE account_id = ? AND idempotency_key = ? AND kind = 'marketing_judgment'
       AND required_capability = 'market_research_v1'`,
  ).bind(accountId, `marketing-research:${accountId}:${campaignId}`).first();
}
