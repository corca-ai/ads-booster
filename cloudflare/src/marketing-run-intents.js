import { canonicalJson, canonicalSha256 } from "./marketing-run-capabilities.js";

export const NEXT_INTENT_INPUT_SCHEMA_SHA256 =
  "217b305284a2eeffc4c15aa244e79dd6da6fce1a7138d656a9f27c7d5477f6fc";
export const NEXT_INTENT_OUTPUT_SCHEMA_SHA256 =
  "38cf82491b68ac5d14a64a6c5e83733f5a9df58b0e4b50fbac2efab161a1a8a2";
export const NEXT_INTENT_PLANNER_PROTOCOL_SHA256 =
  "64890efb66606cc77e5facacaf4c7f62ee1cad18f60247548a1eda98f5566826";

const BASE_DESCRIPTOR = Object.freeze({
  version: "trace.feature-launch-intent.v1",
  owner_id: "trace-marketing.hosted-feature-launch-run",
  effect_class: "none",
  input_schema_sha256: NEXT_INTENT_INPUT_SCHEMA_SHA256,
  output_schema_sha256: NEXT_INTENT_OUTPUT_SCHEMA_SHA256,
  fixed_cost_units: 0,
  approval_policy: "none",
});

export async function deriveFeatureLaunchIntentSnapshot(
  runId,
  researchResult,
  researchResultSha256,
  hasExactContinuation,
  resumableScopes,
) {
  const resumable = new Set(resumableScopes);
  const insufficientScopes = researchResult.findings
    .filter((finding) => finding?.evidence_status === "insufficient"
      && resumable.has(finding.scope))
    .map((finding) => finding.scope);
  const intents = [{
    intent_id: "stop",
    ...BASE_DESCRIPTOR,
    eligibility: "always",
    precondition: "none",
    requested_scopes: [],
  }];
  if (insufficientScopes.length > 0) {
    intents.push({
      intent_id: "request_more_evidence",
      ...BASE_DESCRIPTOR,
      eligibility: "insufficient_evidence_present",
      precondition: "needs_input_terminal_projection",
      requested_scopes: insufficientScopes,
    });
  }
  if (hasExactContinuation) {
    intents.push({
      intent_id: "propose_shadow_strategy",
      ...BASE_DESCRIPTOR,
      eligibility: "exact_research_continuation_present",
      precondition: "research_continuation_required",
      requested_scopes: [],
    });
  }
  const snapshot = {
    schema_version: "trace.feature-launch-intent-snapshot.v1",
    run_id: runId,
    research_result_sha256: researchResultSha256,
    intents,
  };
  return { snapshot, sha256: await canonicalSha256(snapshot) };
}

export async function expectedNextIntentPlannerReceipt(
  runId,
  researchResult,
  researchResultSha256,
  intentSnapshot,
  modelId,
) {
  const context = {
    schema_version: "trace.feature-launch-next-intent-context.v1",
    run_id: runId,
    research_result_sha256: researchResultSha256,
    research_state: researchResult.state,
    findings: researchResult.findings,
    continuation: researchResult.continuation ?? null,
    intent_snapshot: intentSnapshot,
  };
  const prompt = "Choose exactly one no-effect next intent from the supplied host snapshot. "
    + "Return only JSON matching the schema. Never claim that this worker independently proves "
    + "the Codex invocation occurred. request_more_evidence must select one offered requested "
    + "scope; all other intents require requested_scope null. "
    + "Prompt contract: trace.feature-launch-next-intent-planner.v1.\n\n"
    + `Context:\n${canonicalJson(context)}`;
  return {
    schema_version: "trace.planner-invocation-receipt.v1",
    provider_id: "official-codex-cli",
    model_id: modelId,
    prompt_sha256: await textSha256(prompt),
    context_sha256: await canonicalSha256(context),
    output_schema_sha256: NEXT_INTENT_OUTPUT_SCHEMA_SHA256,
    planner_protocol_sha256: NEXT_INTENT_PLANNER_PROTOCOL_SHA256,
  };
}

async function textSha256(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}
