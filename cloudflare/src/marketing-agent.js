import { hasWorkerForTaskKind } from "./mac-workers.js";

export const MARKETING_JUDGMENT_PIPELINE = "hosted_marketing_judgment_v1";
const MAX_CAMPAIGNS = 100;
const MAX_WORKER_PAYLOAD_BYTES = 16 * 1024;
const SHADOW_PRINCIPLES = Object.freeze([
  "한 게시물은 한 사람의 한 상황과 한 가지 믿음 변화에 집중한다.",
  "제품 주장을 먼저 잠그고 그 주장을 증명할 proof를 매체보다 먼저 선택한다.",
  "외부 레퍼런스는 아이디어 원본이 아니라 포화도, 반증, 모방 충돌 검사에 사용한다.",
  "한 실험은 하나의 manipulated component를 가지며 결론 불가 조건을 미리 적는다.",
  "단일 게시물의 성과를 장기 마케팅 원칙으로 승격하지 않는다.",
]);
const SHADOW_CAPABILITIES = Object.freeze(["strategy.shadow"]);
const FEATURE_LIFECYCLES = new Set([
  "source_candidate",
  "build_candidate",
  "installed_confirmed",
  "released",
  "retracted",
]);
const CLAIM_STATUSES = new Set([
  "proposed",
  "source_supported",
  "build_bound",
  "installed_confirmed",
  "contradicted",
  "unsupported",
  "stale",
  "retracted",
]);
const EVIDENCE_KINDS = new Set([
  "source_blob",
  "source_diff",
  "test_definition",
  "test_run",
  "specification",
  "documentation",
  "commit_context",
  "pull_request_context",
  "build_attestation",
  "install_receipt",
  "runtime_observation",
  "screenshot",
  "video",
]);
const EVIDENCE_RESULTS = new Set(["pass", "fail", "observed", "absent", "inconclusive"]);

export async function handleHostedMarketingAgent(request, env, account) {
  const url = new URL(request.url);
  if (!url.pathname.startsWith("/api/marketing-agent/")) return null;
  try {
    if (request.method === "POST" && url.pathname === "/api/marketing-agent/campaigns") {
      return Response.json(await createShadowCampaign(env, account, await readJson(request)), {
        status: 202,
      });
    }
    if (request.method === "GET" && url.pathname === "/api/marketing-agent/campaigns") {
      return Response.json({ campaigns: await listCampaigns(env, account.account_id) });
    }
    const campaignRoute = url.pathname.match(/^\/api\/marketing-agent\/campaigns\/([^/]+)$/);
    if (request.method === "GET" && campaignRoute) {
      let campaignId;
      try {
        campaignId = decodeURIComponent(campaignRoute[1]);
      } catch {
        throw new MarketingAgentHttpError(400, "campaign_id encoding이 올바르지 않습니다.");
      }
      return Response.json(
        await campaignStatus(env, account.account_id, safeId(campaignId, "campaign_id")),
      );
    }
    return null;
  } catch (error) {
    const status = Number.isInteger(error?.status) ? error.status : 500;
    return Response.json(
      { detail: status === 500 ? "마케팅 에이전트 요청을 처리하지 못했습니다." : error.message },
      { status },
    );
  }
}

export async function createShadowCampaign(env, account, input) {
  if (!(await hasWorkerForTaskKind(env.DB, "marketing_judgment"))) {
    throw new MarketingAgentHttpError(
      503,
      "연결된 Mac 워커가 마케팅 전략 판단을 지원하지 않습니다. 워커를 업데이트해 주세요.",
    );
  }
  const campaignId = safeId(input?.campaign_id, "campaign_id");
  const businessOutcome = requiredString(input?.business_outcome, "business_outcome", 1000);
  const currentControl = requiredString(input?.current_control, "current_control", 4000);
  const packet = normalizeFeaturePacket(input?.feature_packet);
  if (packet.gate.publication_allowed !== false) {
    throw new MarketingAgentHttpError(400, "shadow campaign의 publication gate는 닫혀 있어야 합니다.");
  }
  const packetSha256 = await canonicalSha256(packet);
  const knowledgeSnapshotSha256 = await canonicalSha256({ principles: SHADOW_PRINCIPLES });
  const capabilitySnapshotSha256 = await canonicalSha256({ capabilities: SHADOW_CAPABILITIES });
  const existing = await env.DB.prepare(
    `SELECT campaign_id, feature_packet_sha256, business_outcome, mode, state
     FROM hosted_marketing_campaigns WHERE account_id = ? AND campaign_id = ?`,
  ).bind(account.account_id, campaignId).first();
  if (existing) {
    if (
      existing.feature_packet_sha256 !== packetSha256
      || existing.business_outcome !== businessOutcome
      || existing.mode !== "shadow"
    ) {
      throw new MarketingAgentHttpError(409, "campaign_id가 다른 shadow 요청에 이미 사용됐습니다.");
    }
    return campaignStatus(env, account.account_id, campaignId);
  }
  const existingPacket = await env.DB.prepare(
    `SELECT packet_sha256 FROM hosted_marketing_feature_packets WHERE packet_id = ?`,
  ).bind(packet.packet_id).first();
  if (existingPacket && existingPacket.packet_sha256 !== packetSha256) {
    throw new MarketingAgentHttpError(
      409,
      "packet_id가 다른 feature evidence에 이미 사용됐습니다.",
    );
  }

  const taskId = crypto.randomUUID();
  const now = new Date().toISOString();
  const task = {
    schema_version: "1",
    task_id: taskId,
    run_id: campaignId,
    account_id: account.account_id,
    kind: "marketing_judgment",
    idempotency_key: `marketing-judgment:${account.account_id}:${campaignId}`,
    payload: {
      pipeline: MARKETING_JUDGMENT_PIPELINE,
      judgment: "shadow_strategy",
      campaign_id: campaignId,
      feature_packet: packet,
      feature_packet_sha256: packetSha256,
      account: {
        account_id: account.account_id,
        country: account.country,
        language: account.language,
        timezone: account.timezone,
      },
      business_outcome: businessOutcome,
      current_control: currentControl,
      canonical_principles: [...SHADOW_PRINCIPLES],
      knowledge_snapshot_sha256: knowledgeSnapshotSha256,
      available_capabilities: [...SHADOW_CAPABILITIES],
      capability_snapshot_sha256: capabilitySnapshotSha256,
      requested_by: "hosted_workspace",
    },
    created_at: now,
    credential_ref: null,
  };
  const eventDetail = {
    campaign_id: campaignId,
    mode: "shadow",
    feature_packet_id: packet.packet_id,
    feature_packet_sha256: packetSha256,
    business_outcome: businessOutcome,
    task_id: taskId,
  };
  const taskJson = JSON.stringify(task);
  if (new TextEncoder().encode(taskJson).byteLength > MAX_WORKER_PAYLOAD_BYTES) {
    throw new MarketingAgentHttpError(
      413,
      `marketing judgment task가 ${MAX_WORKER_PAYLOAD_BYTES} bytes를 초과합니다.`,
    );
  }
  const eventId = crypto.randomUUID();
  await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO hosted_marketing_feature_packets
        (packet_id, feature_id, schema_version, lifecycle, repository, mutable_ref,
         resolved_commit_sha, tree_sha, packet_json, packet_sha256, publication_allowed,
         observed_at, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
       ON CONFLICT(packet_id, packet_sha256) DO NOTHING`,
    ).bind(
      packet.packet_id,
      packet.feature_id,
      packet.schema_version,
      packet.lifecycle,
      packet.repository,
      packet.mutable_ref,
      packet.resolved_commit_sha,
      packet.tree_sha,
      canonicalJson(packet),
      packetSha256,
      packet.observed_at,
      now,
    ),
    env.DB.prepare(
      `INSERT INTO hosted_marketing_campaigns
        (campaign_id, account_id, feature_packet_id, feature_packet_sha256, runtime_epoch,
         mode, state, projection_revision, business_outcome, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'agent_v1', 'shadow', 'strategy_requested', 1, ?, ?, ?)`,
    ).bind(
      campaignId,
      account.account_id,
      packet.packet_id,
      packetSha256,
      businessOutcome,
      now,
      now,
    ),
    env.DB.prepare(
      `INSERT INTO hosted_marketing_run_events
        (event_id, campaign_id, sequence, prior_revision, resulting_revision, event_type,
         event_json, event_sha256, idempotency_key, causation_id, correlation_id,
         event_time, observed_at, actor_type)
       VALUES (?, ?, 1, 0, 1, 'shadow_strategy_requested', ?, ?, ?, NULL, ?, ?, ?, 'human')`,
    ).bind(
      eventId,
      campaignId,
      canonicalJson(eventDetail),
      await canonicalSha256(eventDetail),
      `campaign:${campaignId}:create`,
      campaignId,
      now,
      now,
    ),
    env.DB.prepare(
      `INSERT INTO hosted_workspace_capture_tasks
        (task_id, run_id, account_id, candidate_id, candidate_revision, idempotency_key,
         task_json, state, dispatch_mode, kind, required_capability, created_at, updated_at)
       VALUES (?, ?, ?, '', 1, ?, ?, 'queued', 'worker_broker', 'marketing_judgment',
               NULL, ?, ?)`,
    ).bind(taskId, campaignId, account.account_id, task.idempotency_key, taskJson, now, now),
  ]);
  return {
    campaign_id: campaignId,
    task_id: taskId,
    mode: "shadow",
    state: "strategy_requested",
    feature_packet_id: packet.packet_id,
    feature_packet_sha256: packetSha256,
    publication_allowed: false,
    created_at: now,
  };
}

async function listCampaigns(env, accountId) {
  const result = await env.DB.prepare(
    `SELECT campaign_id, feature_packet_id, feature_packet_sha256, mode, state,
            projection_revision, business_outcome, created_at, updated_at
     FROM hosted_marketing_campaigns WHERE account_id = ?
     ORDER BY created_at DESC LIMIT ?`,
  ).bind(accountId, MAX_CAMPAIGNS).all();
  return result.results;
}

async function campaignStatus(env, accountId, campaignId) {
  const row = await env.DB.prepare(
    `SELECT campaign.campaign_id, campaign.feature_packet_id, campaign.feature_packet_sha256,
            campaign.mode, campaign.state, campaign.projection_revision,
            campaign.business_outcome, campaign.created_at, campaign.updated_at,
            task.task_id, task.state AS task_state, task.result_json,
            brief.brief_id, brief.brief_json, brief.brief_sha256
     FROM hosted_marketing_campaigns AS campaign
     LEFT JOIN hosted_workspace_capture_tasks AS task
       ON task.account_id = campaign.account_id AND task.run_id = campaign.campaign_id
      AND task.kind = 'marketing_judgment'
     LEFT JOIN hosted_marketing_strategy_briefs AS brief
       ON brief.campaign_id = campaign.campaign_id
     WHERE campaign.account_id = ? AND campaign.campaign_id = ?`,
  ).bind(accountId, campaignId).first();
  if (!row) throw new MarketingAgentHttpError(404, "마케팅 캠페인을 찾을 수 없습니다.");
  return {
    campaign_id: row.campaign_id,
    feature_packet_id: row.feature_packet_id,
    feature_packet_sha256: row.feature_packet_sha256,
    mode: row.mode,
    state: row.state,
    projection_revision: row.projection_revision,
    business_outcome: row.business_outcome,
    task: row.task_id ? {
      task_id: row.task_id,
      state: row.task_state,
      result: row.result_json ? JSON.parse(row.result_json) : null,
    } : null,
    strategy_brief: row.brief_id ? {
      brief_id: row.brief_id,
      sha256: row.brief_sha256,
      value: JSON.parse(row.brief_json),
    } : null,
    publication_allowed: false,
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

export function normalizeFeaturePacket(value) {
  const packet = requireObject(value, "feature_packet");
  if (packet.schema_version !== "trace.feature-evidence.v1") {
    throw new MarketingAgentHttpError(400, "feature packet schema가 올바르지 않습니다.");
  }
  const claims = requireArray(packet.claims, "claims", 1, 64).map((claim) => {
    const item = requireObject(claim, "claim");
    const status = requiredString(item.status, "claim.status", 40);
    if (!CLAIM_STATUSES.has(status)) {
      throw new MarketingAgentHttpError(400, "feature claim status가 올바르지 않습니다.");
    }
    return {
      claim_id: safeId(item.claim_id, "claim_id"),
      text: requiredString(item.text, "claim.text", 2000),
      status,
      evidence_ids: requireArray(item.evidence_ids ?? [], "claim.evidence_ids", 0, 32)
        .map((id) => safeId(id, "evidence_id")),
    };
  });
  if (new Set(claims.map((claim) => claim.claim_id)).size !== claims.length) {
    throw new MarketingAgentHttpError(400, "feature claim ID는 중복될 수 없습니다.");
  }
  const evidence = requireArray(packet.evidence ?? [], "evidence", 0, 128).map((record) => {
    const item = requireObject(record, "evidence record");
    const kind = requiredString(item.kind, "evidence.kind", 80);
    const result = requiredString(item.result, "evidence.result", 40);
    if (!EVIDENCE_KINDS.has(kind) || !EVIDENCE_RESULTS.has(result)) {
      throw new MarketingAgentHttpError(400, "feature evidence 분류가 올바르지 않습니다.");
    }
    return {
      evidence_id: safeId(item.evidence_id, "evidence_id"),
      kind,
      source_uri: requiredString(item.source_uri, "evidence.source_uri", 2000),
      immutable_ref: requiredString(item.immutable_ref, "evidence.immutable_ref", 500),
      content_sha256: sha256Digest(item.content_sha256, "evidence.content_sha256"),
      result,
      collected_at: isoTimestamp(item.collected_at, "evidence.collected_at"),
    };
  });
  const evidenceIds = new Set(evidence.map((record) => record.evidence_id));
  if (evidenceIds.size !== evidence.length) {
    throw new MarketingAgentHttpError(400, "feature evidence ID는 중복될 수 없습니다.");
  }
  if (claims.some((claim) => claim.evidence_ids.some((id) => !evidenceIds.has(id)))) {
    throw new MarketingAgentHttpError(400, "feature claim이 packet 밖의 evidence를 참조합니다.");
  }
  const gate = requireObject(packet.gate, "gate");
  if (typeof gate.publication_allowed !== "boolean") {
    throw new MarketingAgentHttpError(400, "gate.publication_allowed는 boolean이어야 합니다.");
  }
  const allowedClaimIds = requireArray(gate.allowed_claim_ids ?? [], "allowed_claim_ids", 0, 64)
    .map((id) => safeId(id, "claim_id"));
  const blockedClaimIds = requireArray(gate.blocked_claim_ids ?? [], "blocked_claim_ids", 0, 64)
    .map((id) => safeId(id, "claim_id"));
  const claimIds = new Set(claims.map((claim) => claim.claim_id));
  if ([...allowedClaimIds, ...blockedClaimIds].some((id) => !claimIds.has(id))) {
    throw new MarketingAgentHttpError(400, "feature gate가 packet 밖의 claim을 참조합니다.");
  }
  if (allowedClaimIds.some((id) => blockedClaimIds.includes(id))) {
    throw new MarketingAgentHttpError(400, "feature gate claim은 허용과 차단에 동시에 속할 수 없습니다.");
  }
  const claimStatuses = new Map(claims.map((claim) => [claim.claim_id, claim.status]));
  if (allowedClaimIds.some((id) => claimStatuses.get(id) !== "installed_confirmed")) {
    throw new MarketingAgentHttpError(
      400,
      "publication claim은 installed_confirmed 상태여야 합니다.",
    );
  }
  if (gate.publication_allowed && allowedClaimIds.length === 0) {
    throw new MarketingAgentHttpError(400, "열린 publication gate에는 허용 claim이 필요합니다.");
  }
  if (!gate.publication_allowed && allowedClaimIds.length > 0) {
    throw new MarketingAgentHttpError(400, "닫힌 publication gate에는 허용 claim이 없어야 합니다.");
  }
  const lifecycle = requiredString(packet.lifecycle, "lifecycle", 40);
  if (!FEATURE_LIFECYCLES.has(lifecycle)) {
    throw new MarketingAgentHttpError(400, "feature lifecycle이 올바르지 않습니다.");
  }
  return {
    schema_version: "trace.feature-evidence.v1",
    packet_id: safeId(packet.packet_id, "packet_id"),
    feature_id: safeId(packet.feature_id, "feature_id"),
    title: requiredString(packet.title, "title", 200),
    lifecycle,
    repository: requiredString(packet.repository, "repository", 300),
    mutable_ref: requiredString(packet.mutable_ref, "mutable_ref", 300),
    resolved_commit_sha: commitSha(packet.resolved_commit_sha, "resolved_commit_sha"),
    tree_sha: commitSha(packet.tree_sha, "tree_sha"),
    claims,
    evidence,
    limitations: requireArray(packet.limitations ?? [], "limitations", 0, 32)
      .map((item) => requiredString(item, "limitation", 2000)),
    gate: {
      publication_allowed: gate.publication_allowed === true,
      allowed_claim_ids: allowedClaimIds,
      blocked_claim_ids: blockedClaimIds,
      reasons: requireArray(gate.reasons ?? [], "gate.reasons", 0, 32)
        .map((item) => requiredString(item, "gate reason", 2000)),
    },
    observed_at: isoTimestamp(packet.observed_at, "observed_at"),
  };
}

function requireObject(value, field) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new MarketingAgentHttpError(400, `${field} 형식이 올바르지 않습니다.`);
  }
  return value;
}

function requireArray(value, field, minimum, maximum) {
  if (!Array.isArray(value) || value.length < minimum || value.length > maximum) {
    throw new MarketingAgentHttpError(400, `${field} 개수가 올바르지 않습니다.`);
  }
  return value;
}

function requiredString(value, field, maximum) {
  if (typeof value !== "string" || !value.trim() || value.length > maximum) {
    throw new MarketingAgentHttpError(400, `${field} 값이 올바르지 않습니다.`);
  }
  return value.trim();
}

function safeId(value, field) {
  const result = requiredString(value, field, 128);
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(result)) {
    throw new MarketingAgentHttpError(400, `${field}는 안전한 식별자여야 합니다.`);
  }
  return result;
}

function commitSha(value, field) {
  const result = requiredString(value, field, 40);
  if (!/^[a-f0-9]{40}$/.test(result)) {
    throw new MarketingAgentHttpError(400, `${field}는 고정된 commit SHA여야 합니다.`);
  }
  return result;
}

function sha256Digest(value, field) {
  const result = requiredString(value, field, 64);
  if (!/^[a-f0-9]{64}$/.test(result)) {
    throw new MarketingAgentHttpError(400, `${field}는 SHA-256이어야 합니다.`);
  }
  return result;
}

function isoTimestamp(value, field) {
  const result = requiredString(value, field, 80);
  const match = result.match(
    /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?Z$/,
  );
  if (!match || !Number.isFinite(Date.parse(result))) {
    throw new MarketingAgentHttpError(400, `${field}는 UTC ISO timestamp여야 합니다.`);
  }
  const fraction = match[2]?.padEnd(6, "0") ?? "";
  return fraction && fraction !== "000000" ? `${match[1]}.${fraction}Z` : `${match[1]}Z`;
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

class MarketingAgentHttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}
