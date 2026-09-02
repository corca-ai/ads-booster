// Every hosted marketing judgment is a distinct worker tool contract. A worker advertising the
// broad `marketing_judgment` task kind is only eligible after it also advertises the exact tool
// version frozen on the task. This keeps deployment rollout from turning an older Mac into a
// permanent queue sink when the marketing agent gains a new reasoning tool.

export const MARKETING_JUDGMENT_CAPABILITIES = Object.freeze({
  shadow_strategy: "shadow_strategy_v1",
  market_research: "market_research_v1",
  creative_plan: "creative_plan_v1",
  candidate_materialization: "candidate_materialization_v2",
  experiment_evaluation: "experiment_evaluation_v1",
  learning_synthesis: "learning_synthesis_v1",
  outcome_reassessment: "outcome_reassessment_v1",
  next_experiment: "next_experiment_v1",
});

export function marketingJudgmentCapability(judgment) {
  const capability = MARKETING_JUDGMENT_CAPABILITIES[judgment];
  if (!capability) throw new TypeError(`unsupported marketing judgment: ${judgment}`);
  return capability;
}

export function marketingJudgmentCapabilityMatches(task, judgment) {
  const required = task?.required_capability;
  return required == null || required === marketingJudgmentCapability(judgment);
}

const ONLINE_WINDOW_MS = 45_000;

/**
 * Fail before queue mutation unless an active, recently-seen worker can run this exact tool.
 *
 * This is intentionally marketing-specific. Capture and candidate-generation keep their existing
 * broker admission semantics, while reasoning jobs may run on a Codex-ready worker whose Appium
 * doctor is degraded. A globally healthy worker remains compatible during a rolling upgrade.
 */
export async function hasOnlineMarketingWorker(db, judgment, now = new Date()) {
  const capability = marketingJudgmentCapability(judgment);
  const seenAfter = new Date(now.getTime() - ONLINE_WINDOW_MS).toISOString();
  const result = await db.prepare(
    `SELECT capabilities_json, doctor_json FROM mac_workers
     WHERE state = 'active' AND last_seen_at >= ?`,
  ).bind(seenAfter).all();
  return result.results.some((row) => {
    const capabilities = parseObject(row.capabilities_json);
    const doctor = parseObject(row.doctor_json);
    return workerTaskKinds(capabilities).includes("marketing_judgment")
      && capabilities[capability] === true
      && (doctor.ready === true || capabilities.marketing_reasoning_ready === true);
  });
}

function workerTaskKinds(capabilities) {
  if (typeof capabilities.task_kinds !== "string") return [];
  return capabilities.task_kinds.split(",").map((kind) => kind.trim());
}

function parseObject(value) {
  if (typeof value !== "string") return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}
