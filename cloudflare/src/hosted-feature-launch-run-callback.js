import { HttpError } from "./http-error.js";
import { assertHostedCallbackTransport, reserveWorkerTaskCallback } from "./mac-workers.js";
import {
  marketingAgentDelegationInsertStatement,
  marketingAgentDelegationRecord,
  reconcileMarketingAgentDelegation,
} from "./marketing-agent-delegations.js";
import {
  canonicalJson,
  canonicalSha256,
  HOSTED_AGENT_RUN_PIPELINE,
  storedMarketingAgentRun,
} from "./marketing-agent-runs.js";
import { deriveResearchCapabilitySnapshot } from "./marketing-run-capabilities.js";
import {
  deriveFeatureLaunchIntentSnapshot,
  expectedNextIntentPlannerReceipt,
} from "./marketing-run-intents.js";
import { marketingJudgmentCapabilityMatches } from "./marketing-worker-capabilities.js";

export async function receiveHostedFeatureLaunchRunCallback(
  env,
  task,
  callback,
  worker = null,
  { reconcileDelegation = reconcileMarketingAgentDelegation } = {},
) {
  assertHostedCallbackTransport(task, worker);
  if (
    callback.schema_version !== "1"
    || task.task_id !== callback.task_id
    || task.run_id !== callback.run_id
    || task.account_id !== callback.account_id
    || callback.kind !== "marketing_judgment"
    || callback.callback_id !== `${callback.task_id}:completed`
  ) {
    throw new HttpError(409, "callback scope does not match hosted feature launch run");
  }
  const status = callback.result?.status;
  if (!["succeeded", "failed", "unknown_side_effect"].includes(status)) {
    throw new HttpError(400, "invalid hosted feature launch result status");
  }
  if (task.required_capability === "feature_launch_run_v4") {
    return acknowledgeMigratedV4Callback(env.DB, task, worker);
  }
  if (!marketingJudgmentCapabilityMatches(task, "feature_launch_run")) {
    throw new HttpError(409, "feature launch callback capability does not match its task");
  }
  const storedResultJson = JSON.stringify(callback.result);
  if (task.callback_id) {
    if (task.callback_id !== callback.callback_id || task.result_json !== storedResultJson) {
      throw new HttpError(409, "feature launch callback changed");
    }
    return { accepted: true, duplicate: true };
  }
  const run = await storedMarketingAgentRun(env.DB, task.task_id);
  if (
    !run
    || run.account_id !== task.account_id
    || run.state !== "queued"
    || run.active_task_id !== task.task_id
  ) {
    throw new HttpError(409, "marketing agent run is not awaiting this callback");
  }
  const payload = publishedPayload(task);
  let launchRequest;
  try {
    launchRequest = JSON.parse(run.request_json);
  } catch {
    throw new HttpError(409, "stored marketing agent run request is invalid");
  }
  let capability;
  let storedCapabilitySnapshot;
  let resumableScopes;
  try {
    capability = await deriveResearchCapabilitySnapshot(
      launchRequest?.research?.required_scopes,
    );
    storedCapabilitySnapshot = JSON.parse(run.capability_snapshot_json);
    resumableScopes = JSON.parse(run.resumable_scopes_json);
  } catch {
    throw new HttpError(409, "stored marketing agent run capability snapshot is invalid");
  }
  if (
    payload.pipeline !== HOSTED_AGENT_RUN_PIPELINE
    || payload.judgment !== "feature_launch_run"
    || payload.run_id !== run.run_id
    || payload.request_sha256 !== run.request_sha256
    || canonicalJson(payload.launch_request) !== canonicalJson(launchRequest)
    || await canonicalSha256(launchRequest) !== run.request_sha256
    || canonicalJson(payload.capability_snapshot) !== canonicalJson(capability.snapshot)
    || payload.capability_snapshot_sha256 !== capability.sha256
    || canonicalJson(storedCapabilitySnapshot) !== canonicalJson(capability.snapshot)
    || run.capability_snapshot_sha256 !== capability.sha256
    || payload.phase !== run.phase
    || payload.step_sequence !== run.step_sequence
    || payload.parent_step_sha256 !== run.parent_step_sha256
    || payload.root_request_sha256 !== run.root_request_sha256
    || canonicalJson(payload.resumable_scopes) !== canonicalJson(resumableScopes)
    || (run.phase === "initial" && (
      run.step_sequence !== 1
      || run.parent_step_sha256 !== null
      || run.root_request_sha256 !== run.request_sha256
    ))
    || (run.phase === "resume" && (
      run.step_sequence !== 2
      || run.parent_step_sha256 !== run.head_step_sha256
      || canonicalJson(resumableScopes) !== "[]"
    ))
    || typeof payload.model_id !== "string"
    || !payload.model_id
    || payload.requested_by !== "hosted_workspace"
    || task.task_id !== callback.task_id
  ) {
    throw new HttpError(409, "feature launch task binding is invalid");
  }
  if (status !== "succeeded") {
    return finishTerminalFailure(env, task, callback, worker, run, storedResultJson, status);
  }
  const output = requireObject(callback.result?.output, "feature launch output");
  const researchResult = requireObject(output.research_result, "dynamic research result");
  assertExactKeys(output, [
    "pipeline", "judgment", "task_id", "run_id", "account_id", "request_sha256",
    "capability_snapshot", "capability_snapshot_sha256", "research_input_sha256",
    "research_result", "research_result_sha256", "receipt_chain", "intent_snapshot",
    "intent_snapshot_sha256", "next_intent", "next_intent_sha256", "effect_class",
    "tool_actions_created", "phase", "step_sequence", "parent_step_sha256",
    "root_request_sha256", "resumable_scopes",
  ], "feature launch output");
  assertExactKeys(researchResult, [
    "schema_version", "session_id", "state", "input_snapshot_sha256",
    "registry_snapshot_sha256", "planner_protocol_sha256", "provider_id", "model_id",
    "trace_sha256", "tool_calls", "spent_cost_units", "capability_snapshot",
    "receipt_chain", "findings", "evidence_brief", "continuation", "market_proposal",
  ], "dynamic research result");
  const researchResultSha256 = await canonicalSha256(researchResult);
  const researchInputSha256 = await canonicalSha256(launchRequest.research);
  const featurePacketSha256 = await canonicalSha256(launchRequest.research.feature_packet);
  if (
    output.pipeline !== HOSTED_AGENT_RUN_PIPELINE
    || output.judgment !== "feature_launch_run"
    || output.task_id !== task.task_id
    || output.run_id !== run.run_id
    || output.account_id !== run.account_id
    || output.request_sha256 !== run.request_sha256
    || output.phase !== run.phase
    || output.step_sequence !== run.step_sequence
    || output.parent_step_sha256 !== run.parent_step_sha256
    || output.root_request_sha256 !== run.root_request_sha256
    || canonicalJson(output.resumable_scopes) !== canonicalJson(resumableScopes)
    || canonicalJson(output.capability_snapshot) !== canonicalJson(capability.snapshot)
    || output.capability_snapshot_sha256 !== capability.sha256
    || output.research_input_sha256 !== researchInputSha256
    || output.research_result_sha256 !== researchResultSha256
    || output.effect_class !== "none"
    || output.tool_actions_created !== 0
    || researchResult.schema_version !== "trace.dynamic-evidence-research-result.v4"
    || researchResult.session_id !== launchRequest.research.session_id
    || researchResult.input_snapshot_sha256 !== researchInputSha256
    || canonicalJson(researchResult.capability_snapshot) !== canonicalJson(capability.snapshot)
    || researchResult.model_id !== payload.model_id
    || typeof researchResult.provider_id !== "string"
    || !researchResult.provider_id
    || researchResult.registry_snapshot_sha256 !== capability.sha256
    || researchResult.planner_protocol_sha256 !== capability.snapshot.planner_protocol_sha256
    || !isSha256(researchResult.trace_sha256)
  ) {
    throw new HttpError(409, "feature launch research output binding is invalid");
  }
  const receiptChain = await validatedReceiptChain(
    capability.snapshot,
    launchRequest,
    researchResult,
    output.receipt_chain,
  );
  const continuation = admissibleContinuation(
    launchRequest,
    researchResult,
    researchInputSha256,
    featurePacketSha256,
  );
  const intent = await validatedNextIntent(
    output,
    payload,
    run,
    researchResult,
    researchResultSha256,
    continuation,
    resumableScopes,
  );
  const marketResearchSeed = researchResult.market_proposal
    ? await boundMarketResearchSeed(researchResult)
    : null;
  if (intent.decision.intent_id !== "propose_shadow_strategy") {
    return finishBlockedRun(
      env,
      task,
      callback,
      worker,
      run,
      storedResultJson,
      researchResult,
      researchResultSha256,
      receiptChain,
      intent,
    );
  }
  if (!marketResearchSeed) {
    throw new HttpError(409, "shadow strategy intent requires a bound market proposal");
  }
  const continuationSha256 = await canonicalSha256(continuation);
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
  const campaignRequest = {
    account_id: run.account_id,
    campaign_id: run.run_id,
    business_outcome: launchRequest.business_outcome,
    current_control: launchRequest.current_control,
    feature_packet: launchRequest.research.feature_packet,
    marketing_context_snapshot_id: launchRequest.marketing_context_snapshot_id,
    research_enabled: true,
    mode: "shadow",
    agent_run_lineage: {
      schema_version: "trace.feature-launch-lineage.v1",
      agent_run_id: run.run_id,
      research_session_id: continuation.research_session_id,
      research_input_sha256: continuation.research_input_sha256,
      research_trace_sha256: continuation.research_trace_sha256,
      research_continuation_sha256: continuationSha256,
    },
  };
  const now = new Date().toISOString();
  const intentStep = await intentStepStatement(
    env, task, run, intent, "delegated", null, now,
  );
  const delegation = await marketingAgentDelegationRecord({
    run,
    task,
    stepSha256: intentStep.stepSha256,
    researchResultSha256,
    campaignRequest,
    marketResearchSeed,
    createdAt: now,
  });
  const results = await env.DB.batch([
    ...await receiptLedgerStatements(env, task, run, receiptChain, now),
    intentStep.statement,
    marketingAgentDelegationInsertStatement(env.DB, delegation),
    env.DB.prepare(
      `UPDATE hosted_marketing_agent_runs
       SET state = 'blocked', research_result_json = ?, research_result_sha256 = ?,
           failure_code = 'shadow_strategy_delegation_pending',
           intent_snapshot_json = ?, intent_snapshot_sha256 = ?,
           next_intent_json = ?, next_intent_sha256 = ?, head_step_sha256 = ?,
           active_task_id = NULL, loop_state = 'running', loop_revision = loop_revision + 1,
           cumulative_cost_units = cumulative_cost_units + ?, completed_steps = completed_steps + 1,
           updated_at = ?
       WHERE run_id = ? AND account_id = ? AND active_task_id = ? AND state = 'queued'`,
    ).bind(
      canonicalJson(researchResult),
      researchResultSha256,
      canonicalJson(intent.snapshot),
      intent.snapshotSha256,
      canonicalJson(intent.decision),
      intent.decisionSha256,
      intentStep.stepSha256,
      researchResult.spent_cost_units,
      now,
      run.run_id,
      run.account_id,
      task.task_id,
    ),
    completionStatement(env, task, callback, worker, storedResultJson, "succeeded", now),
  ]);
  const runUpdate = results.at(-2);
  const taskUpdate = results.at(-1);
  if (runUpdate.meta.changes !== 1 || taskUpdate.meta.changes !== 1) {
    throw new HttpError(409, "feature launch callback finalization lost its run binding");
  }
  try {
    const reconciled = await reconcileDelegation(env, delegation.value.delegation_id);
    return {
      accepted: true,
      duplicate: false,
      campaign_id: reconciled.campaign_id,
      delegation_pending: !reconciled.finalized,
    };
  } catch {
    return { accepted: true, duplicate: false, delegation_pending: true };
  }
}

async function acknowledgeMigratedV4Callback(database, task, worker) {
  const run = await storedMarketingAgentRun(database, task.task_id);
  if (
    task.dispatch_mode !== "worker_broker"
    || !worker
    || worker.worker_id !== task.worker_id
    || typeof task.lease_id !== "string"
    || !task.lease_id
    || typeof task.execution_started_at !== "string"
    || !task.execution_started_at
    || task.state !== "failed"
    || task.callback_id !== null
    || !run
    || run.root_task_id !== task.task_id
    || run.account_id !== task.account_id
    || run.state !== "failed"
    || run.failure_code !== "feature_launch_resume_upgrade_required"
    || run.active_task_id !== null
    || run.loop_state !== "failed"
  ) throw new HttpError(409, "migrated feature launch callback binding is invalid");
  return { accepted: true, duplicate: true, migrated: true };
}

async function finishTerminalFailure(env, task, callback, worker, run, storedResultJson, status) {
  if (worker) {
    const reservation = await reserveWorkerTaskCallback(
      env.DB, worker, task, callback.callback_id, storedResultJson,
    );
    if (reservation.duplicate) return { accepted: true, duplicate: true };
  }
  const failureCode = boundedFailureCode(callback.result?.failure_code, status);
  const runState = status === "unknown_side_effect" ? "unknown_side_effect" : "failed";
  const now = new Date().toISOString();
  const [runUpdate, taskUpdate] = await env.DB.batch([
    env.DB.prepare(
      `UPDATE hosted_marketing_agent_runs
       SET state = ?, failure_code = ?, active_task_id = NULL, loop_state = 'failed',
           loop_revision = loop_revision + 1, updated_at = ?
       WHERE run_id = ? AND account_id = ? AND active_task_id = ? AND state = 'queued'`,
    ).bind(runState, failureCode, now, run.run_id, run.account_id, task.task_id),
    completionStatement(env, task, callback, worker, storedResultJson, status, now),
  ]);
  if (runUpdate.meta.changes !== 1 || taskUpdate.meta.changes !== 1) {
    throw new HttpError(409, "feature launch failure finalization lost its run binding");
  }
  return { accepted: true, duplicate: false };
}

async function finishBlockedRun(
  env,
  task,
  callback,
  worker,
  run,
  storedResultJson,
  researchResult,
  researchResultSha256,
  receiptChain,
  intent,
) {
  if (worker) {
    const reservation = await reserveWorkerTaskCallback(
      env.DB, worker, task, callback.callback_id, storedResultJson,
    );
    if (reservation.duplicate) return { accepted: true, duplicate: true };
  }
  const isNeedsInput = intent.decision.intent_id === "request_more_evidence";
  const failureCode = isNeedsInput
    ? "research_more_evidence_requested"
    : "research_stopped_by_intent";
  const disposition = isNeedsInput ? "needs_input" : "stopped";
  const now = new Date().toISOString();
  const intentStep = await intentStepStatement(
    env, task, run, intent, disposition, null, now,
  );
  const results = await env.DB.batch([
    ...await receiptLedgerStatements(env, task, run, receiptChain, now),
    intentStep.statement,
    env.DB.prepare(
      `UPDATE hosted_marketing_agent_runs
       SET state = 'blocked', research_result_json = ?, research_result_sha256 = ?,
           failure_code = ?, intent_snapshot_json = ?, intent_snapshot_sha256 = ?,
           next_intent_json = ?, next_intent_sha256 = ?, head_step_sha256 = ?,
           active_task_id = NULL, loop_state = ?, loop_revision = loop_revision + 1,
           cumulative_cost_units = cumulative_cost_units + ?, completed_steps = completed_steps + 1,
           updated_at = ?
       WHERE run_id = ? AND account_id = ? AND active_task_id = ? AND state = 'queued'`,
    ).bind(
      canonicalJson(researchResult),
      researchResultSha256,
      failureCode,
      canonicalJson(intent.snapshot),
      intent.snapshotSha256,
      canonicalJson(intent.decision),
      intent.decisionSha256,
      intentStep.stepSha256,
      disposition === "needs_input" ? "needs_input" : "stopped",
      researchResult.spent_cost_units,
      now,
      run.run_id,
      run.account_id,
      task.task_id,
    ),
    completionStatement(env, task, callback, worker, storedResultJson, "succeeded", now),
  ]);
  const runUpdate = results.at(-2);
  const taskUpdate = results.at(-1);
  if (runUpdate.meta.changes !== 1 || taskUpdate.meta.changes !== 1) {
    throw new HttpError(409, "blocked feature launch finalization lost its run binding");
  }
  return { accepted: true, duplicate: false };
}

async function validatedNextIntent(
  output,
  payload,
  run,
  researchResult,
  researchResultSha256,
  continuation,
  resumableScopes,
) {
  const derived = await deriveFeatureLaunchIntentSnapshot(
    run.run_id,
    researchResult,
    researchResultSha256,
    continuation !== null,
    resumableScopes,
  );
  const decision = requireObject(output.next_intent, "feature launch next intent");
  const decisionSha256 = await canonicalSha256(decision);
  if (
    canonicalJson(output.intent_snapshot) !== canonicalJson(derived.snapshot)
    || output.intent_snapshot_sha256 !== derived.sha256
    || output.next_intent_sha256 !== decisionSha256
  ) throw new HttpError(409, "feature launch intent snapshot binding is invalid");
  assertExactKeys(decision, [
    "schema_version",
    "run_id",
    "research_result_sha256",
    "intent_snapshot_sha256",
    "intent_id",
    "reason",
    "requested_scope",
    "planner_receipt",
  ], "feature launch next intent");
  const plannerReceipt = requireObject(
    decision.planner_receipt,
    "feature launch intent planner receipt",
  );
  assertExactKeys(plannerReceipt, [
    "schema_version",
    "provider_id",
    "model_id",
    "prompt_sha256",
    "context_sha256",
    "output_schema_sha256",
    "planner_protocol_sha256",
  ], "feature launch intent planner receipt");
  const expectedPlannerReceipt = await expectedNextIntentPlannerReceipt(
    run.run_id,
    researchResult,
    researchResultSha256,
    derived.snapshot,
    payload.model_id,
  );
  // This binds the worker-reported planner envelope to the admitted bytes and model identity.
  // Because the same worker reports it, it is not independent proof that provider execution occurred.
  const option = derived.snapshot.intents.find(({ intent_id: id }) => id === decision.intent_id);
  const requestedScopeIsValid = decision.intent_id === "request_more_evidence"
    ? option?.requested_scopes.includes(decision.requested_scope)
    : decision.requested_scope === null;
  if (
    decision.schema_version !== "trace.feature-launch-next-intent-decision.v1"
    || decision.run_id !== run.run_id
    || decision.research_result_sha256 !== researchResultSha256
    || decision.intent_snapshot_sha256 !== derived.sha256
    || !option
    || !requestedScopeIsValid
    || !boundedString(decision.reason, 1000)
    || canonicalJson(plannerReceipt) !== canonicalJson(expectedPlannerReceipt)
    || (decision.intent_id === "propose_shadow_strategy" && !continuation)
  ) throw new HttpError(409, "feature launch next intent is not host-admissible");
  return {
    snapshot: derived.snapshot,
    snapshotSha256: derived.sha256,
    decision,
    decisionSha256,
  };
}

async function intentStepStatement(
  env,
  task,
  run,
  intent,
  disposition,
  campaignId,
  now,
) {
  const stateBefore = {
    schema_version: "trace.marketing-agent-run-step-state.v1",
    run_id: run.run_id,
    account_id: run.account_id,
    task_id: task.task_id,
    state: "queued",
    loop_state: "running",
    loop_revision: run.loop_revision,
    request_sha256: run.request_sha256,
    capability_snapshot_sha256: run.capability_snapshot_sha256,
  };
  const stateBeforeSha256 = await canonicalSha256(stateBefore);
  const result = {
    schema_version: "trace.feature-launch-intent-step-result.v1",
    intent_id: intent.decision.intent_id,
    disposition,
    campaign_id: campaignId,
    effect_class: campaignId === null ? "none" : "control_plane_write",
    tasks_created: campaignId === null ? 0 : 1,
  };
  const resultSha256 = await canonicalSha256(result);
  const step = {
    schema_version: "trace.marketing-agent-run-step.v1",
    sequence: run.step_sequence,
    parent_step_sha256: run.parent_step_sha256,
    step_type: "research_intent_decision",
    state_before_sha256: stateBeforeSha256,
    research_result_sha256: intent.decision.research_result_sha256,
    intent_snapshot_sha256: intent.snapshotSha256,
    decision_sha256: intent.decisionSha256,
    result_sha256: resultSha256,
    disposition,
    started_at: now,
    completed_at: now,
  };
  const stepSha256 = await canonicalSha256(step);
  const statement = env.DB.prepare(
    `INSERT INTO hosted_marketing_agent_run_steps
      (run_id, account_id, task_id, sequence, parent_step_sha256, step_type,
       state_before_sha256, research_result_sha256, intent_snapshot_json,
       intent_snapshot_sha256, decision_json, decision_sha256, result_json,
       result_sha256, disposition, started_at, completed_at, step_json, step_sha256)
     VALUES (?, ?, ?, ?, ?, 'research_intent_decision', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  ).bind(
    run.run_id,
    run.account_id,
    task.task_id,
    run.step_sequence,
    run.parent_step_sha256,
    stateBeforeSha256,
    intent.decision.research_result_sha256,
    canonicalJson(intent.snapshot),
    intent.snapshotSha256,
    canonicalJson(intent.decision),
    intent.decisionSha256,
    canonicalJson(result),
    resultSha256,
    disposition,
    now,
    now,
    canonicalJson(step),
    stepSha256,
  );
  return { statement, stepSha256 };
}

async function validatedReceiptChain(snapshot, launchRequest, researchResult, outputReceiptChain) {
  const chain = boundedArray(researchResult.receipt_chain, "research receipt chain", 1, 3);
  if (canonicalJson(outputReceiptChain) !== canonicalJson(chain)) {
    throw new HttpError(409, "feature launch receipt chain echo is invalid");
  }
  if (chain.length !== snapshot.capabilities.length) {
    throw new HttpError(409, "feature launch receipt chain coverage is invalid");
  }
  const capabilities = new Map(snapshot.capabilities.map((item) => [item.scope, item]));
  const scopes = new Set();
  const uniqueFields = ["action_id", "call_sha256", "request_sha256", "receipt_sha256",
    "observation_sha256"];
  const seen = Object.fromEntries(uniqueFields.map((field) => [field, new Set()]));
  const findings = new Map((researchResult.findings ?? []).map((item) => [item?.scope, item]));
  let totalCost = 0;
  for (const [index, entryValue] of chain.entries()) {
    const entry = requireObject(entryValue, "research receipt chain entry");
    assertExactKeys(entry, [
      "sequence",
      "iteration",
      "action_id",
      "scope",
      "call_sha256",
      "request_sha256",
      "receipt_sha256",
      "observation_sha256",
      "actual_cost_units",
      "invocation",
      "receipt",
      "observation",
      "hand_result",
    ], "research receipt chain entry");
    const capability = capabilities.get(entry.scope);
    const invocation = requireObject(entry.invocation, "research invocation proof");
    const call = requireObject(invocation.call, "research call proof");
    const request = requireObject(invocation.request, "research request proof");
    const goal = requireObject(request.goal, "research goal proof");
    const decision = requireObject(request.decision, "research decision proof");
    const plannerReceipt = requireObject(decision.planner_receipt, "planner receipt proof");
    const receipt = requireObject(entry.receipt, "research receipt proof");
    const hand = requireObject(entry.hand_result, "research hand result proof");
    const observation = requireObject(entry.observation, "research observation proof");
    assertExactProofShapes(invocation, call, request, goal, decision, plannerReceipt,
      receipt, hand, observation);
    const descriptorSha256 = capability && await canonicalSha256({
      schema_version: "trace.dynamic-research-capability.v1",
      capability_id: capability.capability_id,
      owner: capability.owner_id,
      effect_class: capability.effect_class,
      request_schema_sha256: capability.request_schema_sha256,
      worst_case_cost_units: capability.worst_case_cost_units,
    });
    const requestSha256 = await canonicalSha256({
      schema_version: invocation.schema_version,
      request_schema_sha256: call.request_schema_sha256,
      request,
    });
    const callSha256 = await canonicalSha256(call);
    const decisionSha256 = await canonicalSha256(decision);
    const receiptSha256 = await canonicalSha256(hand);
    const observationSha256 = await canonicalSha256(observation);
    const finding = findings.get(entry.scope);
    if (
      entry.sequence !== index + 1
      || entry.iteration !== index + 1
      || !capability
      || entry.action_id !== capability.action_id
      || scopes.has(entry.scope)
      || invocation.schema_version !== "trace.bound-tool-invocation.v1"
      || call.schema_version !== "trace.tool-call.v1"
      || call.capability_id !== capability.capability_id
      || call.descriptor_sha256 !== descriptorSha256
      || call.request_schema_sha256 !== capability.request_schema_sha256
      || call.input_sha256 !== requestSha256
      || call.effect_class !== "observe"
      || call.call_id !== `research-${goal.goal_id}-${decision.decision_id}`
      || call.idempotency_key !== `research:${goal.goal_id}:${entry.iteration}:${entry.action_id}`
      || request.schema_version !== "trace.evidence-research-tool-request.v1"
      || request.feature_packet_sha256 !== await canonicalSha256(launchRequest.research.feature_packet)
      || goal.goal_id !== launchRequest.research.session_id
      || goal.schema_version !== "trace.evidence-research-goal.v2"
      || goal.feature_packet_id !== launchRequest.research.feature_packet.packet_id
      || goal.feature_packet_sha256 !== request.feature_packet_sha256
      || goal.input_snapshot_sha256 !== await canonicalSha256(launchRequest.research)
      || goal.planner_provider_id !== researchResult.provider_id
      || goal.planner_model_id !== researchResult.model_id
      || goal.planner_protocol_sha256 !== snapshot.planner_protocol_sha256
      || goal.pinned_skill_registry_sha256 !== await canonicalSha256(snapshot)
      || canonicalJson(goal.required_scopes) !== canonicalJson(launchRequest.research.required_scopes)
      || goal.max_iterations !== snapshot.capabilities.length
      || decision.goal_id !== goal.goal_id
      || decision.schema_version !== "trace.evidence-research-decision.v2"
      || decision.iteration !== entry.iteration
      || decision.skill_id !== snapshot.skill_id
      || decision.skill_sha256 !== snapshot.skill_sha256
      || decision.action_id !== entry.action_id
      || decision.scope !== entry.scope
      || !validDecisionClaims(decision, launchRequest.research.feature_packet)
      || !boundedString(decision.research_question, 1000)
      || !boundedString(decision.counter_evidence_question, 1000)
      || plannerReceipt.schema_version !== "trace.planner-invocation-receipt.v1"
      || plannerReceipt.provider_id !== goal.planner_provider_id
      || plannerReceipt.model_id !== goal.planner_model_id
      || plannerReceipt.planner_protocol_sha256 !== goal.planner_protocol_sha256
      || !isSha256(plannerReceipt.prompt_sha256)
      || !isSha256(plannerReceipt.context_sha256)
      || !isSha256(plannerReceipt.output_schema_sha256)
      || receipt.call_id !== call.call_id
      || receipt.call_sha256 !== callSha256
      || receipt.approval_grant_sha256 !== null
      || receipt.disposition !== "succeeded"
      || receipt.actual_cost_units !== capability.worst_case_cost_units
      || receipt.receipt_sha256 !== receiptSha256
      || entry.call_sha256 !== callSha256
      || entry.request_sha256 !== requestSha256
      || entry.receipt_sha256 !== receiptSha256
      || entry.observation_sha256 !== observationSha256
      || entry.actual_cost_units !== receipt.actual_cost_units
      || !handMatchesProof(hand, entry, call, requestSha256, decisionSha256, receipt)
      || !observationMatchesProof(
        observation, entry, callSha256, requestSha256, decisionSha256, receiptSha256, hand,
      )
      || !findingMatchesProof(finding, hand)
      || !await sourceMatchesProof(entry.scope, hand, decision, launchRequest, researchResult)
    ) throw new HttpError(409, "feature launch receipt chain entry is invalid");
    scopes.add(entry.scope);
    for (const field of uniqueFields) {
      if (
        seen[field].has(entry[field])
      ) throw new HttpError(409, "feature launch receipt chain lineage is invalid");
      seen[field].add(entry[field]);
    }
    totalCost += receipt.actual_cost_units;
  }
  if (
    scopes.size !== capabilities.size
    || snapshot.capabilities.some(({ scope }) => !scopes.has(scope))
    || researchResult.tool_calls !== chain.length
    || researchResult.spent_cost_units !== totalCost
  ) throw new HttpError(409, "feature launch receipt chain totals are invalid");
  return chain;
}

function assertExactProofShapes(invocation, call, request, goal, decision, plannerReceipt,
  receipt, hand, observation) {
  assertExactKeys(invocation, ["schema_version", "call", "request"], "research invocation proof");
  assertExactKeys(call, ["schema_version", "call_id", "idempotency_key", "capability_id",
    "descriptor_sha256", "request_schema_sha256", "input_sha256", "effect_class"],
  "research call proof");
  assertExactKeys(request, ["schema_version", "goal", "feature_packet_sha256", "decision"],
    "research request proof");
  assertExactKeys(goal, ["schema_version", "goal_id", "feature_packet_id", "feature_packet_sha256",
    "input_snapshot_sha256", "planner_provider_id", "planner_model_id",
    "planner_protocol_sha256", "pinned_skill_registry_sha256", "required_scopes",
    "max_iterations"], "research goal proof");
  assertExactKeys(decision, ["schema_version", "decision_id", "goal_id", "iteration", "skill_id",
    "skill_sha256", "action_id", "scope", "claim_ids", "research_question",
    "counter_evidence_question", "planner_receipt"], "research decision proof");
  assertExactKeys(plannerReceipt, ["schema_version", "provider_id", "model_id", "prompt_sha256",
    "context_sha256", "output_schema_sha256", "planner_protocol_sha256"],
  "planner receipt proof");
  assertExactKeys(receipt, ["call_id", "call_sha256", "approval_grant_sha256", "disposition",
    "actual_cost_units", "receipt_sha256"], "research receipt proof");
  assertExactKeys(hand, ["schema_version", "goal_id", "call_id", "call_sha256", "request_sha256",
    "feature_packet_sha256", "decision_sha256", "disposition", "actual_cost_units", "iteration",
    "scope", "evidence_status", "source_ref", "source_sha256", "source_artifact_sha256",
    "trust_state", "supported_claim_ids", "summary", "caveats", "observed_at"],
  "research hand result proof");
  assertExactKeys(observation, ["schema_version", "observation_id", "scope", "receipt_sha256",
    "call_sha256", "request_sha256", "feature_packet_sha256", "decision_sha256", "source_ref",
    "source_sha256", "evidence_summary", "caveats", "trust_state", "supported_claim_ids",
    "evidence_status", "observed_at"], "research observation proof");
}

function handMatchesProof(hand, entry, call, requestSha256, decisionSha256, receipt) {
  return hand.schema_version === "trace.dynamic-research-hand-result-proof.v1"
    && hand.goal_id === entry.invocation.request.goal.goal_id
    && hand.call_id === call.call_id
    && hand.call_sha256 === receipt.call_sha256
    && hand.request_sha256 === requestSha256
    && hand.feature_packet_sha256 === entry.invocation.request.feature_packet_sha256
    && hand.decision_sha256 === decisionSha256
    && hand.disposition === receipt.disposition
    && hand.actual_cost_units === receipt.actual_cost_units
    && hand.iteration === entry.iteration
    && hand.scope === entry.scope
    && (hand.source_artifact_sha256 === null || hand.source_artifact_sha256 === hand.source_sha256)
    && ["sufficient", "insufficient"].includes(hand.evidence_status)
    && ["packet_bound", "caller_supplied_projection", "unverified_model_proposal"]
      .includes(hand.trust_state)
    && boundedString(hand.summary, 2000)
    && validStringArray(hand.caveats, 12, false)
    && validStringArray(hand.supported_claim_ids, 16, true)
    && boundedString(hand.source_ref, 1000)
    && isSha256(hand.source_sha256)
    && isUtcTimestamp(hand.observed_at);
}

function observationMatchesProof(observation, entry, callSha256, requestSha256,
  decisionSha256, receiptSha256, hand) {
  return observation.schema_version === "trace.evidence-research-observation.v2"
    && observation.observation_id === `observation-${receiptSha256.slice(0, 24)}`
    && observation.scope === entry.scope
    && observation.receipt_sha256 === receiptSha256
    && observation.call_sha256 === callSha256
    && observation.request_sha256 === requestSha256
    && observation.feature_packet_sha256 === hand.feature_packet_sha256
    && observation.decision_sha256 === decisionSha256
    && observation.source_ref === hand.source_ref
    && observation.source_sha256 === hand.source_sha256
    && observation.evidence_summary === hand.summary
    && canonicalJson(observation.caveats) === canonicalJson(hand.caveats)
    && observation.trust_state === hand.trust_state
    && canonicalJson(observation.supported_claim_ids) === canonicalJson(hand.supported_claim_ids)
    && observation.evidence_status === hand.evidence_status
    && observation.observed_at === hand.observed_at;
}

function findingMatchesProof(finding, hand) {
  return finding && canonicalJson(finding) === canonicalJson({
    iteration: hand.iteration,
    scope: hand.scope,
    evidence_status: hand.evidence_status,
    summary: hand.summary,
    caveats: hand.caveats,
    source_ref: hand.source_ref,
    source_sha256: hand.source_sha256,
    trust_state: hand.trust_state,
    supported_claim_ids: hand.supported_claim_ids,
  });
}

async function sourceMatchesProof(scope, hand, decision, launchRequest, researchResult) {
  if (scope === "product_truth") {
    const allowed = new Set(launchRequest.research.feature_packet.gate.allowed_claim_ids);
    const supported = decision.claim_ids.filter((claimId) => allowed.has(claimId));
    return hand.source_artifact_sha256 === null
      && hand.source_ref === `trace-feature-packet:${launchRequest.research.feature_packet.packet_id}`
      && hand.source_sha256 === await canonicalSha256(launchRequest.research.feature_packet)
      && hand.trust_state === "packet_bound"
      && canonicalJson(hand.supported_claim_ids) === canonicalJson(supported)
      && hand.evidence_status === (
        supported.length > 0 && supported.length === decision.claim_ids.length
          ? "sufficient"
          : "insufficient"
      );
  }
  if (scope === "customer_intelligence") {
    const context = launchRequest.research.marketing_context;
    const current = context && Date.parse(context.expires_at) > Date.parse(hand.observed_at);
    return hand.source_artifact_sha256 === null
      && hand.trust_state === "caller_supplied_projection"
      && hand.supported_claim_ids.length === 0
      && hand.evidence_status === (current ? "sufficient" : "insufficient")
      && (current
      ? hand.source_ref === `trace-marketing-context:${context.snapshot_id}`
        && hand.source_sha256 === context.snapshot_sha256
      : hand.source_ref === "missing:caller-supplied-customer-intelligence"
        && hand.source_sha256 === "0".repeat(64));
  }
  if (!researchResult.market_proposal) {
    return hand.source_artifact_sha256 === null
      && hand.source_ref === "missing:market-research-context"
      && hand.source_sha256 === "0".repeat(64)
      && hand.trust_state === "unverified_model_proposal"
      && hand.supported_claim_ids.length === 0
      && hand.evidence_status === "insufficient";
  }
  const proposalSha256 = await canonicalSha256(researchResult.market_proposal);
  return hand.source_artifact_sha256 === proposalSha256
    && hand.source_sha256 === proposalSha256
    && hand.source_ref === `quarantined-codex-search:${proposalSha256}`
    && hand.trust_state === "unverified_model_proposal"
    && hand.supported_claim_ids.length === 0
    && hand.evidence_status === "insufficient";
}

function validDecisionClaims(decision, featurePacket) {
  if (!Array.isArray(decision.claim_ids) || decision.claim_ids.length < 1
      || decision.claim_ids.length > 16 || new Set(decision.claim_ids).size !== decision.claim_ids.length) {
    return false;
  }
  const known = new Set(featurePacket.claims.map(({ claim_id: claimId }) => claimId));
  return decision.claim_ids.every((claimId) => known.has(claimId));
}

function validStringArray(value, maximum, unique) {
  return Array.isArray(value) && value.length <= maximum
    && (!unique || new Set(value).size === value.length)
    && value.every((item) => typeof item === "string");
}

function boundedString(value, maximum) {
  return typeof value === "string" && value.length >= 1 && value.length <= maximum;
}

function isUtcTimestamp(value) {
  return typeof value === "string" && value.endsWith("Z") && !Number.isNaN(Date.parse(value));
}

async function receiptLedgerStatements(env, task, run, receiptChain, now) {
  if (run.phase === "resume") {
    return Promise.all(receiptChain.map(async (entry) => {
      const entryJson = canonicalJson(entry);
      return env.DB.prepare(
        `INSERT INTO hosted_marketing_agent_run_task_receipts
          (task_id, run_id, account_id, sequence, entry_json, entry_sha256,
           actual_cost_units, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      ).bind(
        task.task_id,
        run.run_id,
        run.account_id,
        entry.sequence,
        entryJson,
        await canonicalSha256(entry),
        entry.actual_cost_units,
        now,
      );
    }));
  }
  return Promise.all(receiptChain.map(async (entry) => {
    const entryJson = canonicalJson(entry);
    return env.DB.prepare(
      `INSERT INTO hosted_marketing_agent_run_receipts
        (run_id, account_id, task_id, sequence, action_id, scope, call_sha256,
         request_sha256, receipt_sha256, observation_sha256, actual_cost_units,
         entry_json, entry_sha256, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      run.run_id,
      run.account_id,
      task.task_id,
      entry.sequence,
      entry.action_id,
      entry.scope,
      entry.call_sha256,
      entry.request_sha256,
      entry.receipt_sha256,
      entry.observation_sha256,
      entry.actual_cost_units,
      entryJson,
      await canonicalSha256(entry),
      now,
    );
  }));
}

function assertExactKeys(value, expected, field) {
  const keys = Object.keys(value).sort();
  const required = [...expected].sort();
  if (canonicalJson(keys) !== canonicalJson(required)) {
    throw new HttpError(409, `${field} shape is invalid`);
  }
}

function completionStatement(env, task, callback, worker, resultJson, status, now) {
  if (worker) {
    return env.DB.prepare(
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
  return env.DB.prepare(
    `UPDATE hosted_workspace_capture_tasks
     SET state = ?, result_json = ?, callback_id = ?, updated_at = ?
     WHERE task_id = ? AND callback_id IS NULL`,
  ).bind(status, resultJson, callback.callback_id, now, task.task_id);
}

function admissibleContinuation(
  launchRequest,
  result,
  researchInputSha256,
  featurePacketSha256,
) {
  const continuation = result.continuation;
  if (
    result.state !== "inconclusive"
    || !continuation
    || continuation.schema_version !== "trace.research-continuation.v1"
    || continuation.continuation_id !== `continuation-${researchInputSha256.slice(0, 24)}`
    || continuation.account_id !== launchRequest.research.account_id
    || continuation.feature_packet_id !== launchRequest.research.feature_packet.packet_id
    || continuation.feature_packet_sha256 !== featurePacketSha256
    || continuation.research_session_id !== launchRequest.research.session_id
    || continuation.research_input_sha256 !== researchInputSha256
    || continuation.research_trace_sha256 !== result.trace_sha256
    || continuation.pending_scope !== "market_evidence"
    || continuation.pending_reason !== "unverified_model_proposal"
  ) return null;
  const findings = Array.isArray(result.findings) ? result.findings : [];
  const requiredScopes = launchRequest.research.required_scopes;
  if (findings.length !== requiredScopes.length) return null;
  const byScope = new Map(findings.map((finding) => [finding?.scope, finding]));
  if (byScope.size !== findings.length || requiredScopes.some((scope) => !byScope.has(scope))) {
    return null;
  }
  const product = byScope.get("product_truth");
  const market = byScope.get("market_evidence");
  if (
    !product
    || product.trust_state !== "packet_bound"
    || product.evidence_status !== "insufficient"
    || !Array.isArray(product.supported_claim_ids)
    || product.supported_claim_ids.length !== 0
    || !market
    || market.trust_state !== "unverified_model_proposal"
    || market.evidence_status !== "insufficient"
    || typeof market.source_ref !== "string"
    || !market.source_ref.startsWith("quarantined-codex-search:")
  ) return null;
  if (product.source_sha256 !== featurePacketSha256) return null;
  const completed = requiredScopes.filter((scope) => scope !== "market_evidence");
  if (canonicalJson(continuation.completed_scopes) !== canonicalJson(completed)) return null;
  if (byScope.has("customer_intelligence")
      && byScope.get("customer_intelligence")?.evidence_status !== "sufficient") return null;
  return continuation;
}

async function boundMarketResearchSeed(result) {
  const proposal = requireObject(result.market_proposal, "market proposal");
  if (proposal.schema_version !== "trace.reference-research-proposal.v1") {
    throw new HttpError(409, "market proposal schema is invalid");
  }
  const sources = boundedArray(proposal.sources, "market proposal sources", 2, 16);
  const observations = boundedArray(
    proposal.observations,
    "market proposal observations",
    2,
    24,
  );
  boundedArray(proposal.blind_spots, "market proposal blind spots", 1, 12);
  const sourceIds = new Set();
  for (const source of sources) {
    if (
      !source
      || typeof source !== "object"
      || typeof source.source_id !== "string"
      || sourceIds.has(source.source_id)
      || typeof source.url !== "string"
      || !source.url.startsWith("https://")
    ) throw new HttpError(409, "market proposal source binding is invalid");
    sourceIds.add(source.source_id);
  }
  const observationIds = new Set();
  for (const observation of observations) {
    if (
      !observation
      || typeof observation !== "object"
      || typeof observation.observation_id !== "string"
      || observationIds.has(observation.observation_id)
      || !Array.isArray(observation.source_ids)
      || observation.source_ids.length < 1
      || observation.source_ids.some((sourceId) => !sourceIds.has(sourceId))
    ) throw new HttpError(409, "market proposal observation binding is invalid");
    observationIds.add(observation.observation_id);
  }
  const sha256 = await canonicalSha256(proposal);
  const market = result.findings.find((finding) => finding?.scope === "market_evidence");
  if (
    !market
    || market.source_sha256 !== sha256
    || market.source_ref !== `quarantined-codex-search:${sha256}`
  ) throw new HttpError(409, "market proposal does not match its research finding");
  return { proposal, sha256 };
}

function boundedArray(value, field, minimum, maximum) {
  if (!Array.isArray(value) || value.length < minimum || value.length > maximum) {
    throw new HttpError(409, `${field} is invalid`);
  }
  return value;
}

function publishedPayload(task) {
  try {
    return requireObject(JSON.parse(task.task_json)?.payload, "feature launch task payload");
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(409, "feature launch task payload is invalid");
  }
}

function requireObject(value, field) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new HttpError(400, `${field} must be an object`);
  }
  return value;
}

function boundedFailureCode(value, fallback) {
  if (typeof value !== "string" || !/^[a-z0-9][a-z0-9_]{0,119}$/.test(value)) {
    return fallback === "unknown_side_effect"
      ? "feature_launch_run_side_effect_unknown"
      : "feature_launch_run_failed";
  }
  return value;
}

function isSha256(value) {
  return typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
}
