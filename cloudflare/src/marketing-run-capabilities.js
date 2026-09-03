const RESEARCH_SCOPES = Object.freeze({
  product_truth: 1,
  customer_intelligence: 1,
  market_evidence: 3,
});

export const RESEARCH_SKILL_ID = "evidence_research.v1";
export const RESEARCH_SKILL_SHA256 =
  "bf9cdcf6b326ad0307bda1ff55c4351972770d714a2f0a3de0a8a8206cbc9e7f";
export const RESEARCH_PLANNER_PROTOCOL_SHA256 =
  "d371ebdbe67a70e92113ecdd7617b27f0b357513aca3408630c2e9cd6e5006c6";
export const RESEARCH_TOOL_REQUEST_SCHEMA_SHA256 =
  "c765b4846605c55fab8f8ed5fe7b03f09cd2d8788e218e033099decbd81529b3";

export async function deriveResearchCapabilitySnapshot(requiredScopes) {
  if (
    !Array.isArray(requiredScopes)
    || requiredScopes.length < 1
    || requiredScopes.length > 3
    || new Set(requiredScopes).size !== requiredScopes.length
    || requiredScopes.some((scope) => !Object.hasOwn(RESEARCH_SCOPES, scope))
  ) {
    throw new TypeError("required research scopes are invalid");
  }
  const snapshot = {
    schema_version: "trace.research-capability-snapshot.v1",
    skill_id: RESEARCH_SKILL_ID,
    skill_sha256: RESEARCH_SKILL_SHA256,
    planner_protocol_sha256: RESEARCH_PLANNER_PROTOCOL_SHA256,
    capabilities: requiredScopes.map((scope) => ({
      action_id: `observe.${scope}`,
      scope,
      capability_id: `observe.${scope}`,
      owner_id: "trace-marketing.dynamic-evidence-research",
      effect_class: "observe",
      request_schema_sha256: RESEARCH_TOOL_REQUEST_SCHEMA_SHA256,
      worst_case_cost_units: RESEARCH_SCOPES[scope],
      approval_policy: "none",
      configuration_bounds: {
        claim_ids_max: 16,
        question_max_chars: 1000,
        counter_evidence_question_max_chars: 1000,
      },
    })),
  };
  return { snapshot, sha256: await canonicalSha256(snapshot) };
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
  const bytes = new TextEncoder().encode(canonicalJson(value));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
