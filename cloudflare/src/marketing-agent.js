import { hasWorkerForTaskKind } from "./mac-workers.js";
import {
  assertCurrentCapabilityBinding,
  MarketingCapabilityError,
  resolveCreativeCapabilityBindings,
} from "./marketing-adapter-capabilities.js";
import { listMarketingReviewQueue, marketingReviewPacket } from "./marketing-review.js";

export const MARKETING_JUDGMENT_PIPELINE = "hosted_marketing_judgment_v1";
const MAX_CAMPAIGNS = 100;
const MAX_WORKER_PAYLOAD_BYTES = 64 * 1024;
const MAX_AGENT_REQUEST_BYTES = 64 * 1024;
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
const PRODUCT_EVENT_TYPES = new Set([
  "first_open",
  "feature_start",
  "generation_completed",
  "scheduling_completed",
  "setup_completed",
]);

export async function handleHostedMarketingAgent(request, env, account) {
  const url = new URL(request.url);
  if (!url.pathname.startsWith("/api/marketing-agent/")) return null;
  try {
    const variantRedirect = url.pathname.match(/^\/api\/marketing-agent\/v\/([^/]+)$/);
    if (request.method === "GET" && variantRedirect) {
      return resolveVariantLink(env, decodeURIComponent(variantRedirect[1]));
    }
    if (request.method === "POST" && url.pathname === "/api/marketing-agent/product-events") {
      requireEventIngestAuthority(request, env);
      return agentJson(await ingestProductEvent(env, await readJson(request)), 202);
    }
    if (request.method === "POST" && url.pathname === "/api/marketing-agent/customer-signals") {
      requireMarketingAuthority(request, env);
      return agentJson(await importCustomerSignal(env, account, await readJson(request)), 201);
    }
    if (request.method === "GET" && url.pathname === "/api/marketing-agent/customer-signals") {
      requireMarketingAuthority(request, env);
      return agentJson({ signals: await listCustomerSignals(env, account.account_id) });
    }
    const signalApprovalRoute = url.pathname.match(
      /^\/api\/marketing-agent\/customer-signals\/([^/]+)\/approval$/,
    );
    if (request.method === "POST" && signalApprovalRoute) {
      requireMarketingAuthority(request, env);
      return agentJson(await decideCustomerSignal(
        env,
        account,
        decodedRouteId(signalApprovalRoute[1]),
        await readJson(request),
      ));
    }
    if (request.method === "POST" && url.pathname === "/api/marketing-agent/context-snapshots") {
      requireMarketingAuthority(request, env);
      return agentJson(await createMarketingContextSnapshot(env, account, await readJson(request)), 201);
    }
    const contextSnapshotRoute = url.pathname.match(
      /^\/api\/marketing-agent\/context-snapshots\/([^/]+)$/,
    );
    if (request.method === "GET" && contextSnapshotRoute) {
      requireMarketingAuthority(request, env);
      return agentJson(await marketingContextSnapshotStatus(
        env,
        account.account_id,
        decodedRouteId(contextSnapshotRoute[1]),
      ));
    }
    if (request.method === "POST" && url.pathname === "/api/marketing-agent/campaigns") {
      const input = await readJson(request);
      if (
        (input?.mode ?? "shadow") !== "shadow"
        || input?.marketing_context_snapshot_id != null
      ) {
        requireMarketingAuthority(request, env);
      }
      return agentJson(await createShadowCampaign(env, account, input), 202);
    }
    if (request.method === "GET" && url.pathname === "/api/marketing-agent/campaigns") {
      return agentJson({ campaigns: await listCampaigns(env, account.account_id) });
    }
    if (request.method === "GET" && url.pathname === "/api/marketing-agent/review-queue") {
      requireMarketingAuthority(request, env);
      return agentJson(await listMarketingReviewQueue(env, account.account_id));
    }
    if (request.method === "POST" && url.pathname === "/api/marketing-agent/learning-syntheses") {
      requireMarketingAuthority(request, env);
      return agentJson(await requestLearningSynthesis(env, account, await readJson(request)), 202);
    }
    const learningApprovalRoute = url.pathname.match(
      /^\/api\/marketing-agent\/learning-candidates\/([^/]+)\/approval$/,
    );
    if (request.method === "POST" && learningApprovalRoute) {
      requireMarketingAuthority(request, env);
      return agentJson(await decideLearningCandidate(
        env,
        account,
        safeId(decodeURIComponent(learningApprovalRoute[1]), "learning_id"),
        await readJson(request),
      ));
    }
    const strategyApprovalRoute = url.pathname.match(
      /^\/api\/marketing-agent\/campaigns\/([^/]+)\/strategy-approval$/,
    );
    if (request.method === "POST" && strategyApprovalRoute) {
      requireMarketingAuthority(request, env);
      let campaignId;
      try {
        campaignId = decodeURIComponent(strategyApprovalRoute[1]);
      } catch {
        throw new MarketingAgentHttpError(400, "campaign_id encoding이 올바르지 않습니다.");
      }
      return agentJson(
        await decideStrategyAndRequestCreative(
          env,
          account,
          safeId(campaignId, "campaign_id"),
          await readJson(request),
        ),
        202,
      );
    }
    const mediaApprovalRoute = url.pathname.match(
      /^\/api\/marketing-agent\/campaigns\/([^/]+)\/media-approval$/,
    );
    if (request.method === "POST" && mediaApprovalRoute) {
      requireMarketingAuthority(request, env);
      return agentJson(await decideMediaPlan(
        env,
        account,
        decodedRouteId(mediaApprovalRoute[1]),
        await readJson(request),
      ));
    }
    const assignmentRoute = url.pathname.match(
      /^\/api\/marketing-agent\/campaigns\/([^/]+)\/assignments$/,
    );
    if (request.method === "POST" && assignmentRoute) {
      requireMarketingAuthority(request, env);
      return agentJson(await bindCandidateAssignment(
        env,
        account,
        decodedRouteId(assignmentRoute[1]),
        await readJson(request),
      ), 201);
    }
    const artifactRoute = url.pathname.match(
      /^\/api\/marketing-agent\/campaigns\/([^/]+)\/artifacts$/,
    );
    if (request.method === "POST" && artifactRoute) {
      requireMarketingAuthority(request, env);
      return agentJson(await registerArtifactManifest(
        env,
        account,
        decodedRouteId(artifactRoute[1]),
        await readJson(request),
      ), 201);
    }
    const materializationRoute = url.pathname.match(
      /^\/api\/marketing-agent\/campaigns\/([^/]+)\/materializations$/,
    );
    if (request.method === "POST" && materializationRoute) {
      requireMarketingAuthority(request, env);
      return agentJson(await requestCandidateMaterialization(
        env,
        account,
        decodedRouteId(materializationRoute[1]),
        await readJson(request),
      ), 202);
    }
    const variantRoute = url.pathname.match(
      /^\/api\/marketing-agent\/campaigns\/([^/]+)\/assignments\/([^/]+)\/variant-link$/,
    );
    if (request.method === "POST" && variantRoute) {
      requireMarketingAuthority(request, env);
      return agentJson(await createVariantLink(
        env,
        account,
        decodedRouteId(variantRoute[1]),
        safeId(decodeURIComponent(variantRoute[2]), "assignment_id"),
        await readJson(request),
      ), 201);
    }
    const evaluationRoute = url.pathname.match(
      /^\/api\/marketing-agent\/campaigns\/([^/]+)\/evaluations$/,
    );
    if (request.method === "POST" && evaluationRoute) {
      requireMarketingAuthority(request, env);
      return agentJson(await requestExperimentEvaluation(
        env,
        account,
        decodedRouteId(evaluationRoute[1]),
        await readJson(request),
      ), 202);
    }
    const campaignRoute = url.pathname.match(/^\/api\/marketing-agent\/campaigns\/([^/]+)$/);
    const reviewPacketRoute = url.pathname.match(
      /^\/api\/marketing-agent\/campaigns\/([^/]+)\/review-packet$/,
    );
    if (request.method === "GET" && reviewPacketRoute) {
      requireMarketingAuthority(request, env);
      return agentJson(await marketingReviewPacket(
        env,
        account.account_id,
        decodedRouteId(reviewPacketRoute[1]),
      ));
    }
    if (request.method === "GET" && campaignRoute) {
      let campaignId;
      try {
        campaignId = decodeURIComponent(campaignRoute[1]);
      } catch {
        throw new MarketingAgentHttpError(400, "campaign_id encoding이 올바르지 않습니다.");
      }
      return agentJson(
        await campaignStatus(env, account.account_id, safeId(campaignId, "campaign_id")),
      );
    }
    return null;
  } catch (error) {
    const status = Number.isInteger(error?.status) ? error.status : 500;
    return agentJson(
      { detail: status === 500 ? "마케팅 에이전트 요청을 처리하지 못했습니다." : error.message },
      status,
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
  const marketingContext = await resolveMarketingContextProjection(
    env.DB,
    account.account_id,
    input?.marketing_context_snapshot_id,
  );
  const researchEnabled = input?.research_enabled !== false;
  const mode = input?.mode ?? "shadow";
  if (!["shadow", "assisted"].includes(mode)) {
    throw new MarketingAgentHttpError(400, "mode는 shadow 또는 assisted여야 합니다.");
  }
  if (mode === "shadow" && packet.gate.publication_allowed !== false) {
    throw new MarketingAgentHttpError(400, "shadow campaign의 publication gate는 닫혀 있어야 합니다.");
  }
  const truthReview = mode === "assisted"
    ? normalizeProductTruthReview(input?.product_truth_review, packet)
    : null;
  const originCampaignId = mode === "assisted"
    ? safeId(input?.origin_campaign_id, "origin_campaign_id")
    : null;
  if (mode === "assisted" && !packet.gate.publication_allowed) {
    throw new MarketingAgentHttpError(
      400,
      "assisted campaign에는 installed evidence로 열린 publication gate가 필요합니다.",
    );
  }
  const packetSha256 = await canonicalSha256(packet);
  const learningApplicability = marketingLearningApplicability({
    account,
    packet,
    packetSha256,
    mode,
    marketingContext,
  });
  const canonicalPrinciples = await loadCanonicalPrinciples(env.DB, learningApplicability);
  const knowledgeSnapshotSha256 = await canonicalSha256({ principles: canonicalPrinciples });
  const capabilitySnapshotSha256 = await canonicalSha256({ capabilities: SHADOW_CAPABILITIES });
  const existing = await env.DB.prepare(
    `SELECT campaign.campaign_id, campaign.feature_packet_sha256, campaign.business_outcome,
            campaign.mode, campaign.origin_campaign_id, campaign.marketing_context_snapshot_id,
            campaign.marketing_context_snapshot_sha256, campaign.state, task.task_json
     FROM hosted_marketing_campaigns AS campaign
     LEFT JOIN hosted_workspace_capture_tasks AS task
       ON task.account_id = campaign.account_id AND task.kind = 'marketing_judgment'
      AND task.idempotency_key = ?
     WHERE campaign.account_id = ? AND campaign.campaign_id = ?`,
  ).bind(
    researchEnabled
      ? `marketing-research:${account.account_id}:${campaignId}`
      : `marketing-judgment:${account.account_id}:${campaignId}`,
    account.account_id,
    campaignId,
  ).first();
  if (existing) {
    let existingControl = null;
    let existingJudgment = null;
    try {
      const existingPayload = JSON.parse(existing.task_json)?.payload;
      existingControl = existingPayload?.current_control ?? null;
      existingJudgment = existingPayload?.judgment ?? null;
    } catch {
      throw new MarketingAgentHttpError(409, "기존 campaign task binding이 손상되었습니다.");
    }
    if (
      existing.feature_packet_sha256 !== packetSha256
      || existing.business_outcome !== businessOutcome
      || existing.mode !== mode
      || existing.origin_campaign_id !== originCampaignId
      || existing.marketing_context_snapshot_id !== (marketingContext?.snapshot_id ?? null)
      || existing.marketing_context_snapshot_sha256 !== (marketingContext?.snapshot_sha256 ?? null)
      || existingControl !== currentControl
      || existingJudgment !== (researchEnabled ? "market_research" : "shadow_strategy")
    ) {
      throw new MarketingAgentHttpError(409, "campaign_id가 다른 agent 요청에 이미 사용됐습니다.");
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
  if (originCampaignId) {
    const origin = await env.DB.prepare(
      `SELECT campaign.feature_packet_id, packet.feature_id
       FROM hosted_marketing_campaigns AS campaign
       JOIN hosted_marketing_feature_packets AS packet
         ON packet.packet_id = campaign.feature_packet_id
        AND packet.packet_sha256 = campaign.feature_packet_sha256
       WHERE campaign.account_id = ? AND campaign.campaign_id = ? AND campaign.mode = 'shadow'`,
    ).bind(account.account_id, originCampaignId).first();
    if (!origin || origin.feature_id !== packet.feature_id) {
      throw new MarketingAgentHttpError(
        409,
        "assisted campaign은 같은 feature의 same-account shadow origin이 필요합니다.",
      );
    }
  }

  const taskId = crypto.randomUUID();
  const now = new Date().toISOString();
  const task = researchEnabled ? {
    schema_version: "1",
    task_id: taskId,
    run_id: `research-${campaignId}`,
    account_id: account.account_id,
    kind: "marketing_judgment",
    idempotency_key: `marketing-research:${account.account_id}:${campaignId}`,
    payload: {
      pipeline: MARKETING_JUDGMENT_PIPELINE,
      judgment: "market_research",
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
      marketing_context: marketingContext,
      mode,
      canonical_principles: canonicalPrinciples,
      knowledge_snapshot_sha256: knowledgeSnapshotSha256,
      available_capabilities: [...SHADOW_CAPABILITIES],
      capability_snapshot_sha256: capabilitySnapshotSha256,
      query_budget: 6,
      requested_by: "hosted_workspace",
    },
    created_at: now,
    credential_ref: null,
  } : {
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
      mode,
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
      marketing_context: marketingContext,
      canonical_principles: canonicalPrinciples,
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
    mode,
    feature_packet_id: packet.packet_id,
    feature_packet_sha256: packetSha256,
    business_outcome: businessOutcome,
    task_id: taskId,
    origin_campaign_id: originCampaignId,
    marketing_context_snapshot_id: marketingContext?.snapshot_id ?? null,
    marketing_context_snapshot_sha256: marketingContext?.snapshot_sha256 ?? null,
    research_enabled: researchEnabled,
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
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
      packet.gate.publication_allowed ? 1 : 0,
      packet.observed_at,
      now,
    ),
    env.DB.prepare(
      `INSERT INTO hosted_marketing_campaigns
        (campaign_id, account_id, feature_packet_id, feature_packet_sha256, runtime_epoch,
         mode, origin_campaign_id, marketing_context_snapshot_id,
         marketing_context_snapshot_sha256, state, projection_revision, business_outcome,
         created_at, updated_at)
       VALUES (?, ?, ?, ?, 'agent_v1', ?, ?, ?, ?, 'strategy_requested', 1, ?, ?, ?)`,
    ).bind(
      campaignId,
      account.account_id,
      packet.packet_id,
      packetSha256,
      mode,
      originCampaignId,
      marketingContext?.snapshot_id ?? null,
      marketingContext?.snapshot_sha256 ?? null,
      businessOutcome,
      now,
      now,
    ),
    env.DB.prepare(
      `INSERT INTO hosted_marketing_knowledge_snapshots
        (campaign_id, schema_version, snapshot_json, snapshot_sha256, created_at)
       VALUES (?, 'trace.marketing-knowledge.v1', ?, ?, ?)`,
    ).bind(
      campaignId,
      canonicalJson({ principles: canonicalPrinciples }),
      knowledgeSnapshotSha256,
      now,
    ),
    env.DB.prepare(
      `INSERT INTO hosted_marketing_run_events
        (event_id, campaign_id, sequence, prior_revision, resulting_revision, event_type,
         event_json, event_sha256, idempotency_key, causation_id, correlation_id,
         event_time, observed_at, actor_type)
       VALUES (?, ?, 1, 0, 1, ?, ?, ?, ?, NULL, ?, ?, ?, 'human')`,
    ).bind(
      eventId,
      campaignId,
      researchEnabled
        ? "market_research_requested"
        : (mode === "shadow" ? "shadow_strategy_requested" : "strategy_requested"),
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
    ).bind(taskId, task.run_id, account.account_id, task.idempotency_key, taskJson, now, now),
    ...(truthReview ? [
      env.DB.prepare(
        `INSERT INTO hosted_marketing_product_truth_approvals
          (approval_id, packet_id, packet_sha256, approved_claim_ids_json,
           decision, reviewer_id, reviewed_at)
         VALUES (?, ?, ?, ?, 'approved', ?, ?)`,
      ).bind(
        `truth-${packetSha256.slice(0, 48)}-${truthReview.reviewer_id}`,
        packet.packet_id,
        packetSha256,
        canonicalJson(truthReview.approved_claim_ids),
        truthReview.reviewer_id,
        truthReview.reviewed_at,
      ),
      env.DB.prepare(
        `INSERT INTO hosted_marketing_approval_grants
          (grant_id, campaign_id, scope, target_kind, target_id, target_sha256,
           decision, reviewer_id, reviewed_at)
         VALUES (?, ?, 'product_truth', 'feature_packet', ?, ?, 'approved', ?, ?)`,
      ).bind(
        `truth-grant-${packetSha256.slice(0, 42)}-${truthReview.reviewer_id}`,
        campaignId,
        packet.packet_id,
        packetSha256,
        truthReview.reviewer_id,
        truthReview.reviewed_at,
      ),
    ] : []),
  ]);
  return {
    campaign_id: campaignId,
    task_id: taskId,
    mode,
    origin_campaign_id: originCampaignId,
    marketing_context_snapshot_id: marketingContext?.snapshot_id ?? null,
    marketing_context_snapshot_sha256: marketingContext?.snapshot_sha256 ?? null,
    state: "strategy_requested",
    stage: researchEnabled ? "market_research" : "strategy",
    feature_packet_id: packet.packet_id,
    feature_packet_sha256: packetSha256,
    publication_allowed: packet.gate.publication_allowed,
    created_at: now,
  };
}

export async function decideStrategyAndRequestCreative(env, account, campaignId, input) {
  const briefId = safeId(input?.strategy_brief_id, "strategy_brief_id");
  const briefSha256 = sha256Digest(input?.strategy_brief_sha256, "strategy_brief_sha256");
  const reviewerId = safeId(input?.reviewer_id, "reviewer_id");
  const decision = input?.decision;
  if (!["approved", "rejected"].includes(decision)) {
    throw new MarketingAgentHttpError(400, "decision은 approved 또는 rejected여야 합니다.");
  }
  const projectionRevision = positiveInteger(input?.projection_revision, "projection_revision");
  const existingGrant = await env.DB.prepare(
    `SELECT grant_id, target_id, target_sha256, decision
     FROM hosted_marketing_approval_grants
     WHERE campaign_id = ? AND scope = 'strategy' AND reviewer_id = ?
     ORDER BY reviewed_at DESC LIMIT 1`,
  ).bind(campaignId, reviewerId).first();
  if (existingGrant) {
    if (
      existingGrant.target_id !== briefId
      || existingGrant.target_sha256 !== briefSha256
      || existingGrant.decision !== decision
    ) {
      throw new MarketingAgentHttpError(409, "reviewer의 기존 strategy 결정과 충돌합니다.");
    }
    return campaignStatus(env, account.account_id, campaignId);
  }
  const priorApproval = await env.DB.prepare(
    `SELECT grant_id FROM hosted_marketing_approval_grants
     WHERE campaign_id = ? AND scope = 'strategy' AND target_kind = 'strategy_brief'
       AND target_id = ? AND target_sha256 = ? AND decision = 'approved'
     LIMIT 1`,
  ).bind(campaignId, briefId, briefSha256).first();
  if (priorApproval && decision === "approved") {
    return campaignStatus(env, account.account_id, campaignId);
  }
  const row = await env.DB.prepare(
    `SELECT campaign.campaign_id, campaign.account_id, campaign.feature_packet_id,
            campaign.feature_packet_sha256, campaign.mode, campaign.state,
            campaign.projection_revision, packet.packet_json, brief.brief_id,
            brief.brief_json, brief.brief_sha256, knowledge.snapshot_json,
            knowledge.snapshot_sha256
     FROM hosted_marketing_campaigns AS campaign
     JOIN hosted_marketing_feature_packets AS packet
       ON packet.packet_id = campaign.feature_packet_id
      AND packet.packet_sha256 = campaign.feature_packet_sha256
     JOIN hosted_marketing_strategy_briefs AS brief ON brief.campaign_id = campaign.campaign_id
     JOIN hosted_marketing_knowledge_snapshots AS knowledge ON knowledge.campaign_id = campaign.campaign_id
     WHERE campaign.account_id = ? AND campaign.campaign_id = ? AND brief.brief_id = ?`,
  ).bind(account.account_id, campaignId, briefId).first();
  if (!row) throw new MarketingAgentHttpError(404, "승인할 strategy brief를 찾을 수 없습니다.");
  if (
    row.brief_sha256 !== briefSha256
    || row.state !== "experiment_registered"
    || Number(row.projection_revision) !== projectionRevision
  ) {
    throw new MarketingAgentHttpError(409, "strategy approval 대상이 최신 campaign과 일치하지 않습니다.");
  }
  const now = new Date().toISOString();
  const nextRevision = projectionRevision + 1;
  const grantId = `strategy-${briefSha256.slice(0, 48)}-${reviewerId}`;
  const eventType = decision === "approved" ? "strategy_approved" : "strategy_rejected";
  const eventDetail = {
    campaign_id: campaignId,
    strategy_brief_id: briefId,
    strategy_brief_sha256: briefSha256,
    decision,
    reviewer_id: reviewerId,
  };
  const statements = [
    env.DB.prepare(
      `INSERT INTO hosted_marketing_approval_grants
        (grant_id, campaign_id, scope, target_kind, target_id, target_sha256,
         decision, reviewer_id, reviewed_at)
       VALUES (?, ?, 'strategy', 'strategy_brief', ?, ?, ?, ?, ?)`,
    ).bind(grantId, campaignId, briefId, briefSha256, decision, reviewerId, now),
    env.DB.prepare(
      `UPDATE hosted_marketing_campaigns
       SET state = ?, projection_revision = ?, updated_at = ?
       WHERE account_id = ? AND campaign_id = ? AND state = 'experiment_registered'
         AND projection_revision = ?`,
    ).bind(
      decision === "approved" ? "experiment_registered" : "stopped",
      nextRevision,
      now,
      account.account_id,
      campaignId,
      projectionRevision,
    ),
    env.DB.prepare(
      `INSERT INTO hosted_marketing_run_events
        (event_id, campaign_id, sequence, prior_revision, resulting_revision, event_type,
         event_json, event_sha256, idempotency_key, causation_id, correlation_id,
         event_time, observed_at, actor_type)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'human')`,
    ).bind(
      crypto.randomUUID(),
      campaignId,
      nextRevision,
      projectionRevision,
      nextRevision,
      eventType,
      canonicalJson(eventDetail),
      await canonicalSha256(eventDetail),
      `campaign:${campaignId}:strategy-review:${briefSha256}:${reviewerId}`,
      briefId,
      campaignId,
      now,
      now,
    ),
  ];
  let taskId = null;
  if (decision === "approved") {
    if (!(await hasWorkerForTaskKind(env.DB, "marketing_judgment"))) {
      throw new MarketingAgentHttpError(
        503,
        "연결된 Mac 워커가 creative judgment를 지원하지 않습니다.",
      );
    }
    const packet = JSON.parse(row.packet_json);
    const brief = JSON.parse(row.brief_json);
    taskId = crypto.randomUUID();
    const knowledgeSnapshot = requireObject(JSON.parse(row.snapshot_json), "campaign knowledge snapshot");
    const canonicalPrinciples = requireArray(
      knowledgeSnapshot.principles,
      "campaign knowledge principles",
      1,
      100,
    ).map((principle) => requiredString(principle, "campaign knowledge principle", 2000));
    if (await canonicalSha256({ principles: canonicalPrinciples }) !== row.snapshot_sha256) {
      throw new MarketingAgentHttpError(409, "campaign knowledge snapshot이 손상되었습니다.");
    }
    const knowledgeSnapshotSha256 = row.snapshot_sha256;
    let capabilityBindings;
    try {
      capabilityBindings = await resolveCreativeCapabilityBindings(env.DB, account.account_id);
    } catch (error) {
      if (error instanceof MarketingCapabilityError) {
        throw new MarketingAgentHttpError(409, error.message);
      }
      throw error;
    }
    const capabilitySnapshotSha256 = await canonicalSha256({
      capability_bindings: capabilityBindings,
    });
    const task = {
      schema_version: "1",
      task_id: taskId,
      run_id: `creative-${taskId}`,
      account_id: account.account_id,
      kind: "marketing_judgment",
      idempotency_key: `creative-plan:${account.account_id}:${campaignId}:${briefSha256}`,
      payload: {
        pipeline: MARKETING_JUDGMENT_PIPELINE,
        judgment: "creative_plan",
        campaign_id: campaignId,
        feature_packet: packet,
        feature_packet_sha256: row.feature_packet_sha256,
        strategy_brief: brief,
        strategy_brief_sha256: briefSha256,
        account: {
          account_id: account.account_id,
          country: account.country,
          language: account.language,
          timezone: account.timezone,
        },
        canonical_principles: canonicalPrinciples,
        knowledge_snapshot_sha256: knowledgeSnapshotSha256,
        available_capabilities: capabilityBindings.map((binding) => binding.capability_id),
        capability_bindings: capabilityBindings,
        capability_snapshot_sha256: capabilitySnapshotSha256,
        requested_by: "hosted_workspace",
      },
      created_at: now,
      credential_ref: null,
    };
    const taskJson = JSON.stringify(task);
    if (new TextEncoder().encode(taskJson).byteLength > MAX_WORKER_PAYLOAD_BYTES) {
      throw new MarketingAgentHttpError(
        413,
        `creative judgment task가 ${MAX_WORKER_PAYLOAD_BYTES} bytes를 초과합니다.`,
      );
    }
    statements.push(
      env.DB.prepare(
        `INSERT INTO hosted_workspace_capture_tasks
          (task_id, run_id, account_id, candidate_id, candidate_revision, idempotency_key,
           task_json, state, dispatch_mode, kind, required_capability, created_at, updated_at)
         VALUES (?, ?, ?, '', 1, ?, ?, 'queued', 'worker_broker', 'marketing_judgment',
                 NULL, ?, ?)`,
      ).bind(
        taskId,
        task.run_id,
        account.account_id,
        task.idempotency_key,
        taskJson,
        now,
        now,
      ),
    );
  }
  const results = await env.DB.batch(statements);
  if (results[1]?.meta?.changes !== 1) {
    throw new MarketingAgentHttpError(409, "strategy approval이 최신 revision과 충돌했습니다.");
  }
  return {
    campaign_id: campaignId,
    state: decision === "approved" ? "experiment_registered" : "stopped",
    projection_revision: nextRevision,
    decision,
    creative_task_id: taskId,
    publication_allowed: false,
  };
}

export async function decideMediaPlan(env, account, campaignId, input) {
  const planId = safeId(input?.media_plan_id, "media_plan_id");
  const planSha256 = sha256Digest(input?.media_plan_sha256, "media_plan_sha256");
  const reviewerId = safeId(input?.reviewer_id, "reviewer_id");
  const decision = input?.decision;
  if (!["approved", "rejected"].includes(decision)) {
    throw new MarketingAgentHttpError(400, "decision은 approved 또는 rejected여야 합니다.");
  }
  const projectionRevision = positiveInteger(input?.projection_revision, "projection_revision");
  const row = await env.DB.prepare(
    `SELECT campaign.campaign_id, campaign.state, campaign.projection_revision,
            plan.plan_id, plan.plan_sha256, plan.state AS plan_state,
            plan.publication_allowed
     FROM hosted_marketing_campaigns AS campaign
     JOIN hosted_marketing_media_plans AS plan ON plan.campaign_id = campaign.campaign_id
     WHERE campaign.account_id = ? AND campaign.campaign_id = ? AND plan.plan_id = ?`,
  ).bind(account.account_id, campaignId, planId).first();
  if (!row) throw new MarketingAgentHttpError(404, "검수할 media plan을 찾을 수 없습니다.");
  const existingGrant = await env.DB.prepare(
    `SELECT grant_id, target_sha256, decision FROM hosted_marketing_approval_grants
     WHERE campaign_id = ? AND scope = 'creative' AND target_kind = 'media_plan'
       AND target_id = ? AND reviewer_id = ?`,
  ).bind(campaignId, planId, reviewerId).first();
  if (existingGrant) {
    if (existingGrant.target_sha256 !== planSha256 || existingGrant.decision !== decision) {
      throw new MarketingAgentHttpError(409, "reviewer의 기존 media plan 결정과 충돌합니다.");
    }
    return campaignStatus(env, account.account_id, campaignId);
  }
  if (
    row.state !== "creative_planned"
    || row.plan_state !== "proposed"
    || row.plan_sha256 !== planSha256
    || Number(row.projection_revision) !== projectionRevision
  ) {
    throw new MarketingAgentHttpError(409, "media plan 검수 대상이 최신 campaign과 일치하지 않습니다.");
  }
  const now = new Date().toISOString();
  const nextRevision = projectionRevision + 1;
  const detail = {
    campaign_id: campaignId,
    media_plan_id: planId,
    media_plan_sha256: planSha256,
    decision,
    reviewer_id: reviewerId,
  };
  const results = await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO hosted_marketing_approval_grants
        (grant_id, campaign_id, scope, target_kind, target_id, target_sha256,
         decision, reviewer_id, reviewed_at)
       VALUES (?, ?, 'creative', 'media_plan', ?, ?, ?, ?, ?)`,
    ).bind(
      `creative-${planSha256.slice(0, 48)}-${reviewerId}`,
      campaignId,
      planId,
      planSha256,
      decision,
      reviewerId,
      now,
    ),
    env.DB.prepare(
      `UPDATE hosted_marketing_media_plans SET state = ?, updated_at = ?
       WHERE plan_id = ? AND campaign_id = ? AND plan_sha256 = ? AND state = 'proposed'`,
    ).bind(decision, now, planId, campaignId, planSha256),
    env.DB.prepare(
      `UPDATE hosted_marketing_artifact_requests
       SET state = ?, updated_at = ?
       WHERE campaign_id = ? AND treatment_id IN (
         SELECT treatment_id FROM hosted_marketing_creative_treatments WHERE plan_id = ?
       ) AND state = 'planned'`,
    ).bind(decision === "approved" ? "approved" : "stale", now, campaignId, planId),
    env.DB.prepare(
      `UPDATE hosted_marketing_campaigns SET state = ?, projection_revision = ?, updated_at = ?
       WHERE account_id = ? AND campaign_id = ? AND state = 'creative_planned'
         AND projection_revision = ?`,
    ).bind(
      decision === "approved" ? "creative_planned" : "stopped",
      nextRevision,
      now,
      account.account_id,
      campaignId,
      projectionRevision,
    ),
    env.DB.prepare(
      `INSERT INTO hosted_marketing_run_events
        (event_id, campaign_id, sequence, prior_revision, resulting_revision, event_type,
         event_json, event_sha256, idempotency_key, causation_id, correlation_id,
         event_time, observed_at, actor_type)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'human')`,
    ).bind(
      crypto.randomUUID(),
      campaignId,
      nextRevision,
      projectionRevision,
      nextRevision,
      decision === "approved" ? "media_plan_approved" : "media_plan_rejected",
      canonicalJson(detail),
      await canonicalSha256(detail),
      `campaign:${campaignId}:media-review:${planSha256}:${reviewerId}`,
      planId,
      campaignId,
      now,
      now,
    ),
  ]);
  if (results[1]?.meta?.changes !== 1 || results[3]?.meta?.changes !== 1) {
    throw new MarketingAgentHttpError(409, "media plan 검수가 최신 revision과 충돌했습니다.");
  }
  return {
    campaign_id: campaignId,
    media_plan_id: planId,
    decision,
    state: decision === "approved" ? "creative_planned" : "stopped",
    projection_revision: nextRevision,
    publication_allowed: Number(row.publication_allowed) === 1,
  };
}

export async function registerArtifactManifest(env, account, campaignId, input) {
  const manifest = normalizeArtifactManifest(input);
  if (manifest.capability_id === "capture.native_png") {
    throw new MarketingAgentHttpError(
      409,
      "native capture artifact은 검증된 hosted capture callback으로만 기록할 수 있습니다.",
    );
  }
  if (manifest.campaign_id !== campaignId) {
    throw new MarketingAgentHttpError(409, "artifact manifest campaign binding이 다릅니다.");
  }
  const existing = await env.DB.prepare(
    `SELECT manifest_sha256 FROM hosted_marketing_artifact_manifests
     WHERE manifest_id = ?`,
  ).bind(manifest.manifest_id).first();
  const manifestSha256 = await canonicalSha256(manifest);
  if (existing) {
    if (existing.manifest_sha256 !== manifestSha256) {
      throw new MarketingAgentHttpError(409, "manifest_id가 다른 artifact에 사용됐습니다.");
    }
    return { manifest_id: manifest.manifest_id, sha256: manifestSha256, duplicate: true };
  }
  const row = await env.DB.prepare(
    `SELECT request.request_id, request.capability_id, request.request_sha256,
            request.capability_binding_sha256, request.request_json, request.state AS request_state,
            treatment.treatment_id, plan.state AS plan_state,
            packet.packet_json
     FROM hosted_marketing_campaigns AS campaign
     JOIN hosted_marketing_feature_packets AS packet
       ON packet.packet_id = campaign.feature_packet_id
      AND packet.packet_sha256 = campaign.feature_packet_sha256
     JOIN hosted_marketing_artifact_requests AS request
       ON request.campaign_id = campaign.campaign_id
     JOIN hosted_marketing_creative_treatments AS treatment
       ON treatment.treatment_id = request.treatment_id
     JOIN hosted_marketing_media_plans AS plan ON plan.plan_id = treatment.plan_id
     JOIN hosted_marketing_post_assignments AS assignment
       ON assignment.assignment_id = ?
      AND assignment.campaign_id = campaign.campaign_id
      AND assignment.treatment_id = treatment.treatment_id
     WHERE campaign.account_id = ? AND campaign.campaign_id = ?
       AND request.request_id = ? AND treatment.treatment_id = ?`,
  ).bind(
    manifest.assignment_id,
    account.account_id,
    campaignId,
    manifest.request_id,
    manifest.treatment_id,
  ).first();
  if (!row) throw new MarketingAgentHttpError(404, "artifact request를 찾을 수 없습니다.");
  const requestValue = JSON.parse(row.request_json);
  const packet = JSON.parse(row.packet_json);
  const requestClaims = new Set(requestValue.claim_ids ?? []);
  const evidenceIds = new Set((packet.evidence ?? []).map((item) => item.evidence_id));
  if (
    row.plan_state !== "approved"
    || !["approved", "executing", "succeeded"].includes(row.request_state)
    || row.capability_id !== manifest.capability_id
    || row.capability_binding_sha256 !== manifest.capability_binding_sha256
    || row.request_sha256 !== manifest.input_sha256
    || manifest.claim_ids.some((id) => !requestClaims.has(id))
    || manifest.evidence_ids.some((id) => !evidenceIds.has(id))
  ) {
    throw new MarketingAgentHttpError(409, "artifact manifest가 승인된 request와 일치하지 않습니다.");
  }
  if (!manifest.capability_binding_sha256) {
    throw new MarketingAgentHttpError(409, "artifact manifest에는 capability binding이 필요합니다.");
  }
  try {
    await assertCurrentCapabilityBinding(
      env.DB,
      account.account_id,
      manifest.capability_id,
      manifest.capability_binding_sha256,
    );
  } catch (error) {
    if (error instanceof MarketingCapabilityError) {
      throw new MarketingAgentHttpError(409, error.message);
    }
    throw error;
  }
  await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO hosted_marketing_artifact_manifests
        (manifest_id, campaign_id, assignment_id, treatment_id, request_id, schema_version,
         manifest_json, manifest_sha256, artifact_uri, artifact_sha256, input_sha256,
         capability_binding_sha256, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      manifest.manifest_id,
      campaignId,
      manifest.assignment_id,
      manifest.treatment_id,
      manifest.request_id,
      manifest.schema_version,
      canonicalJson(manifest),
      manifestSha256,
      manifest.artifact_uri,
      manifest.artifact_sha256,
      manifest.input_sha256,
      manifest.capability_binding_sha256,
      manifest.created_at,
    ),
    env.DB.prepare(
      `UPDATE hosted_marketing_artifact_requests
       SET state = 'succeeded', updated_at = ?
       WHERE request_id = ? AND campaign_id = ?
         AND state IN ('approved', 'executing', 'succeeded')`,
    ).bind(manifest.created_at, manifest.request_id, campaignId),
  ]);
  return { manifest_id: manifest.manifest_id, sha256: manifestSha256, duplicate: false };
}

export async function requestCandidateMaterialization(env, account, campaignId, input) {
  const projectionRevision = positiveInteger(input?.projection_revision, "projection_revision");
  const rows = await env.DB.prepare(
    `SELECT campaign.mode, campaign.state, campaign.projection_revision, campaign.created_at,
            packet.packet_json, packet.packet_sha256,
            brief.brief_json, brief.brief_sha256,
            plan.plan_json, plan.plan_sha256, plan.state AS plan_state,
            knowledge.snapshot_json, knowledge.snapshot_sha256,
            experiment.experiment_id, experiment.registration_json,
            experiment.allocation_method, experiment.randomization_seed,
            experiment.randomization_seed_sha256,
            exposure_plan.plan_json AS exposure_plan_json,
            exposure_plan.plan_sha256 AS exposure_plan_sha256,
            treatment.treatment_id, treatment.treatment_json, treatment.treatment_sha256,
            treatment.hypothesis_id,
            (SELECT COUNT(*) FROM hosted_marketing_materialization_reservations AS reservation
             WHERE reservation.experiment_id = experiment.experiment_id
               AND reservation.state IN ('queued', 'completed')) AS assignment_count,
            (SELECT COUNT(*) FROM hosted_marketing_experiment_arms AS arm_count
             WHERE arm_count.experiment_id = experiment.experiment_id) AS arm_count
     FROM hosted_marketing_campaigns AS campaign
     JOIN hosted_marketing_feature_packets AS packet
       ON packet.packet_id = campaign.feature_packet_id
      AND packet.packet_sha256 = campaign.feature_packet_sha256
     JOIN hosted_marketing_product_truth_approvals AS truth
       ON truth.packet_id = packet.packet_id AND truth.packet_sha256 = packet.packet_sha256
      AND truth.decision = 'approved'
     JOIN hosted_marketing_strategy_briefs AS brief ON brief.campaign_id = campaign.campaign_id
     JOIN hosted_marketing_media_plans AS plan ON plan.campaign_id = campaign.campaign_id
     JOIN hosted_marketing_knowledge_snapshots AS knowledge ON knowledge.campaign_id = campaign.campaign_id
     JOIN hosted_marketing_experiments AS experiment ON experiment.campaign_id = campaign.campaign_id
     LEFT JOIN hosted_marketing_experiment_exposure_plans AS exposure_plan
       ON exposure_plan.experiment_id = experiment.experiment_id
     JOIN hosted_marketing_creative_treatments AS treatment
       ON treatment.plan_id = plan.plan_id AND treatment.experiment_id = experiment.experiment_id
     JOIN hosted_marketing_experiment_arms AS arm
       ON arm.experiment_id = experiment.experiment_id
      AND arm.hypothesis_id = treatment.hypothesis_id
     WHERE campaign.account_id = ? AND campaign.campaign_id = ?
       AND EXISTS (
         SELECT 1 FROM hosted_marketing_approval_grants AS grant
         WHERE grant.campaign_id = campaign.campaign_id
           AND grant.scope = 'creative' AND grant.target_kind = 'media_plan'
           AND grant.target_id = plan.plan_id AND grant.target_sha256 = plan.plan_sha256
           AND grant.decision = 'approved'
       )
     ORDER BY treatment.hypothesis_id`,
  ).bind(account.account_id, campaignId).all();
  if (!rows.results.length) {
    throw new MarketingAgentHttpError(404, "materialization 가능한 campaign을 찾을 수 없습니다.");
  }
  const first = rows.results[0];
  const registration = JSON.parse(first.registration_json);
  const allocationMethod = first.allocation_method ?? "balanced_complete_blocks";
  const exposurePlan = allocationMethod === "server_randomized_complete_blocks_v1"
    ? await validatedExposurePlan(first, account.account_id)
    : null;
  const assignmentCount = Number(first.assignment_count);
  const armCount = Number(first.arm_count);
  const startedAt = Date.parse(first.created_at);
  const horizonMs = Number(registration.maximum_duration_hours) * 60 * 60 * 1000;
  if (
    first.mode !== "assisted"
    || !["creative_planned", "awaiting_review"].includes(first.state)
    || first.plan_state !== "approved"
    || Number(first.projection_revision) !== projectionRevision
    || !Number.isInteger(armCount)
    || armCount < 2
    || assignmentCount >= Number(registration.maximum_posts)
    || Date.now() > startedAt + horizonMs
  ) {
    throw new MarketingAgentHttpError(409, "materialization allocation gate가 닫혀 있습니다.");
  }
  if (!(await hasWorkerForTaskKind(
    env.DB,
    "marketing_judgment",
    "candidate_materialization_v2",
  ))) {
    throw new MarketingAgentHttpError(
      503,
      "연결된 Mac 워커가 주간 후보 생성을 지원하지 않습니다. 워커를 업데이트해 주세요.",
    );
  }
  const blockNumber = Math.floor(assignmentCount / armCount) + 1;
  const blockId = `${first.experiment_id}.block-${blockNumber}`;
  const existing = await env.DB.prepare(
    `SELECT hypothesis_id, state FROM hosted_marketing_materialization_reservations
     WHERE experiment_id = ? AND eligible_block_id = ?`,
  ).bind(first.experiment_id, blockId).all();
  if (existing.results.some((row) => row.state === "failed")) {
    throw new MarketingAgentHttpError(
      409,
      "eligible block materialization이 실패했습니다. 새 arm으로 우회하지 않고 experiment를 inconclusive로 처리하세요.",
    );
  }
  const assigned = new Set(existing.results.map((row) => row.hypothesis_id));
  const allocationRows = await allocationOrderForBlock(
    rows.results,
    first.experiment_id,
    blockId,
    allocationMethod,
    first.randomization_seed,
  );
  const selected = allocationRows.find((row) => !assigned.has(row.hypothesis_id));
  if (!selected) {
    throw new MarketingAgentHttpError(409, "eligible block allocation이 이미 완료됐습니다.");
  }
  const allocationRank = allocationMethod === "server_randomized_complete_blocks_v1"
    ? allocationRows.findIndex((row) => row.hypothesis_id === selected.hypothesis_id) + 1
    : 0;
  const taskId = crypto.randomUUID();
  const assignmentId = safeId(
    input?.assignment_id ?? `assignment-${taskId}`,
    "assignment_id",
  );
  const now = new Date().toISOString();
  const knowledgeSnapshot = requireObject(
    JSON.parse(first.snapshot_json),
    "campaign knowledge snapshot",
  );
  const canonicalPrinciples = requireArray(
    knowledgeSnapshot.principles,
    "campaign knowledge principles",
    1,
    100,
  ).map((principle) => requiredString(principle, "campaign knowledge principle", 2000));
  if (await canonicalSha256({ principles: canonicalPrinciples }) !== first.snapshot_sha256) {
    throw new MarketingAgentHttpError(409, "campaign knowledge snapshot이 손상되었습니다.");
  }
  const knowledgeSnapshotSha256 = first.snapshot_sha256;
  const task = {
    schema_version: "1",
    task_id: taskId,
    run_id: `candidate-${taskId}`,
    account_id: account.account_id,
    kind: "marketing_judgment",
    idempotency_key: `candidate-materialization:${account.account_id}:${campaignId}:${assignmentId}`,
    payload: {
      pipeline: MARKETING_JUDGMENT_PIPELINE,
      judgment: "candidate_materialization",
      campaign_id: campaignId,
      assignment_id: assignmentId,
      eligible_block_id: blockId,
      allocation: {
        method: allocationMethod,
        randomization_seed_sha256: first.randomization_seed_sha256 ?? null,
        rank: allocationRank,
        posting_slot: allocationMethod === "server_randomized_complete_blocks_v1"
          ? (allocationRank === 1 ? "morning" : "evening")
          : null,
      },
      exposure_plan_sha256: exposurePlan?.plan_sha256 ?? null,
      feature_packet: JSON.parse(selected.packet_json),
      feature_packet_sha256: selected.packet_sha256,
      strategy_brief: JSON.parse(selected.brief_json),
      strategy_brief_sha256: selected.brief_sha256,
      media_plan: JSON.parse(selected.plan_json),
      media_plan_sha256: selected.plan_sha256,
      treatment: JSON.parse(selected.treatment_json),
      treatment_sha256: selected.treatment_sha256,
      account: {
        account_id: account.account_id,
        country: account.country,
        language: account.language,
        timezone: account.timezone,
      },
      canonical_principles: canonicalPrinciples,
      knowledge_snapshot_sha256: knowledgeSnapshotSha256,
      requested_by: "hosted_workspace",
    },
    created_at: now,
    credential_ref: null,
  };
  const taskJson = JSON.stringify(task);
  if (new TextEncoder().encode(taskJson).byteLength > MAX_WORKER_PAYLOAD_BYTES) {
    throw new MarketingAgentHttpError(413, "candidate materialization task가 너무 큽니다.");
  }
  await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO hosted_workspace_capture_tasks
        (task_id, run_id, account_id, candidate_id, candidate_revision, idempotency_key,
         task_json, state, dispatch_mode, kind, required_capability, created_at, updated_at)
       VALUES (?, ?, ?, '', 1, ?, ?, 'queued', 'worker_broker', 'marketing_judgment',
               'candidate_materialization_v2', ?, ?)`,
    ).bind(
      taskId,
      task.run_id,
      account.account_id,
      task.idempotency_key,
      taskJson,
      now,
      now,
    ),
    env.DB.prepare(
      `INSERT INTO hosted_marketing_materialization_reservations
        (assignment_id, campaign_id, experiment_id, hypothesis_id, treatment_id,
         eligible_block_id, allocation_rank, task_id, state, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)`,
    ).bind(
      assignmentId,
      campaignId,
      first.experiment_id,
      selected.hypothesis_id,
      selected.treatment_id,
      blockId,
      allocationRank,
      taskId,
      now,
      now,
    ),
  ]);
  return {
    campaign_id: campaignId,
    task_id: taskId,
    assignment_id: assignmentId,
    eligible_block_id: blockId,
    hypothesis_id: selected.hypothesis_id,
    treatment_id: selected.treatment_id,
    allocation_rank: allocationRank,
    exposure_plan_sha256: exposurePlan?.plan_sha256 ?? null,
    state: "queued",
  };
}

async function validatedExposurePlan(row, accountId) {
  if (!row.exposure_plan_json || !row.exposure_plan_sha256) {
    throw new MarketingAgentHttpError(409, "causal experiment exposure plan이 없습니다.");
  }
  let plan;
  try {
    plan = requireObject(JSON.parse(row.exposure_plan_json), "causal exposure plan");
  } catch {
    throw new MarketingAgentHttpError(409, "causal experiment exposure plan이 손상되었습니다.");
  }
  if (
    plan.schema_version !== "trace.experiment-exposure-plan.v1"
    || plan.experiment_id !== row.experiment_id
    || plan.account_id !== accountId
    || await canonicalSha256(plan) !== row.exposure_plan_sha256
  ) {
    throw new MarketingAgentHttpError(409, "causal experiment exposure plan이 일치하지 않습니다.");
  }
  return { ...plan, plan_sha256: row.exposure_plan_sha256 };
}

async function allocationOrderForBlock(
  rows,
  experimentId,
  blockId,
  allocationMethod,
  randomizationSeed,
) {
  if (allocationMethod === "balanced_complete_blocks") return rows;
  if (allocationMethod !== "server_randomized_complete_blocks_v1") {
    throw new MarketingAgentHttpError(409, "experiment allocation method가 유효하지 않습니다.");
  }
  if (typeof randomizationSeed !== "string" || !/^[a-f0-9]{64}$/.test(randomizationSeed)) {
    throw new MarketingAgentHttpError(409, "experiment randomization plan이 손상되었습니다.");
  }
  const rowsByHypothesis = new Map(rows.map((row) => [row.hypothesis_id, row]));
  return (await randomizedHypothesisOrder(
    [...rowsByHypothesis.keys()],
    experimentId,
    blockId,
    randomizationSeed,
  )).map((hypothesisId) => rowsByHypothesis.get(hypothesisId));
}

function validateEvaluationAssignmentAllocation(assignment, experiment, allocationMethod) {
  if (allocationMethod !== "server_randomized_complete_blocks_v1") return;
  let recorded;
  try {
    recorded = requireObject(JSON.parse(assignment.assignment_json), "assignment allocation").allocation;
  } catch {
    throw new MarketingAgentHttpError(409, "causal assignment allocation receipt가 손상되었습니다.");
  }
  if (
    !recorded
    || recorded.method !== allocationMethod
    || recorded.randomization_seed_sha256 !== experiment.randomization_seed_sha256
    || recorded.rank !== Number(assignment.allocation_rank)
    || !Number.isInteger(recorded.rank)
    || recorded.rank < 1
  ) {
    throw new MarketingAgentHttpError(409, "causal assignment allocation receipt가 일치하지 않습니다.");
  }
}

async function validateCausalAllocationBlocks(
  assignments,
  registration,
  experiment,
  allocationMethod,
) {
  if (allocationMethod !== "server_randomized_complete_blocks_v1") return;
  const expectedArmCount = registration.activated_hypothesis_ids?.length;
  if (
    !Number.isInteger(expectedArmCount)
    || expectedArmCount !== 2
    || typeof experiment.randomization_seed !== "string"
    || !/^[a-f0-9]{64}$/.test(experiment.randomization_seed)
  ) {
    throw new MarketingAgentHttpError(409, "causal experiment arm registration이 손상되었습니다.");
  }
  const assignmentsByBlock = new Map();
  for (const assignment of assignments) {
    const blockAssignments = assignmentsByBlock.get(assignment.eligible_block_id) ?? [];
    blockAssignments.push(assignment);
    assignmentsByBlock.set(assignment.eligible_block_id, blockAssignments);
  }
  for (const [blockId, blockAssignments] of assignmentsByBlock) {
    const rankedHypotheses = await randomizedHypothesisOrder(
      registration.activated_hypothesis_ids,
      experiment.experiment_id,
      blockId,
      experiment.randomization_seed,
    );
    const expectedRanks = new Map(
      rankedHypotheses.map((hypothesisId, index) => [hypothesisId, index + 1]),
    );
    const ranks = blockAssignments.map((assignment) => Number(assignment.allocation_rank));
    if (
      blockAssignments.length > expectedArmCount
      || ranks.some((rank) => !Number.isInteger(rank) || rank < 1 || rank > expectedArmCount)
      || new Set(ranks).size !== ranks.length
      || blockAssignments.some((assignment) => (
        expectedRanks.get(assignment.hypothesis_id) !== Number(assignment.allocation_rank)
      ))
    ) {
      throw new MarketingAgentHttpError(409, "causal block allocation ranks are invalid");
    }
  }
}

async function causalExposureSlotsVerified(
  assignments,
  registration,
  experiment,
  allocationMethod,
) {
  if (allocationMethod !== "server_randomized_complete_blocks_v1") return false;
  const expectedCount = Number(registration.maximum_posts);
  if (!Number.isInteger(expectedCount) || assignments.length !== expectedCount) return false;
  let exposurePlan;
  try {
    exposurePlan = requireObject(JSON.parse(experiment.exposure_plan_json), "exposure plan");
  } catch {
    return false;
  }
  if (
    exposurePlan.schema_version !== "trace.experiment-exposure-plan.v1"
    || exposurePlan.experiment_id !== experiment.experiment_id
    || await canonicalSha256(exposurePlan) !== experiment.exposure_plan_sha256
  ) return false;
  const slotIds = new Set();
  for (const assignment of assignments) {
    if (
      !assignment.exposure_slot_id
      || !assignment.exposure_commitment_json
      || !assignment.exposure_commitment_sha256
    ) {
      return false;
    }
    let commitment;
    let wallClock;
    try {
      commitment = requireObject(
        JSON.parse(assignment.exposure_commitment_json),
        "exposure commitment",
      );
      wallClock = requireObject(
        JSON.parse(assignment.exposure_wall_clock_snapshot),
        "exposure wall clock",
      );
    } catch {
      throw new MarketingAgentHttpError(409, "causal exposure commitment가 손상되었습니다.");
    }
    const expected = {
      schema_version: "trace.exposure-slot.v1",
      experiment_id: experiment.experiment_id,
      assignment_id: assignment.assignment_id,
      eligible_block_id: assignment.eligible_block_id,
      hypothesis_id: assignment.hypothesis_id,
      allocation_rank: Number(assignment.allocation_rank),
      randomization_seed_sha256: experiment.randomization_seed_sha256,
      posting_slot: assignment.exposure_posting_slot,
      exposure_plan_sha256: experiment.exposure_plan_sha256,
      profile_id_snapshot: exposurePlan.profile_id,
      threads_user_id_snapshot: exposurePlan.threads_user_id_snapshot,
      username_snapshot: exposurePlan.username_snapshot,
      timezone_snapshot: assignment.exposure_timezone_snapshot,
      wall_clock_snapshot: wallClock,
      scheduled_at: assignment.exposure_scheduled_at,
      tolerance_seconds: Number(assignment.exposure_tolerance_seconds),
    };
    if (
      canonicalJson(commitment) !== canonicalJson(expected)
      || await canonicalSha256(expected) !== assignment.exposure_commitment_sha256
      || slotIds.has(assignment.exposure_slot_id)
    ) {
      throw new MarketingAgentHttpError(409, "causal exposure commitment가 일치하지 않습니다.");
    }
    slotIds.add(assignment.exposure_slot_id);
    const scheduledAt = Date.parse(assignment.exposure_scheduled_at);
    const publishedAt = assignment.published_at ? Date.parse(assignment.published_at) : NaN;
    const toleranceMs = Number(assignment.exposure_tolerance_seconds) * 1000;
    if (
      assignment.publication_state !== "published"
      || assignment.publication_scheduled_at !== assignment.exposure_scheduled_at
      || assignment.publication_posting_slot !== assignment.exposure_posting_slot
      || assignment.exposure_plan_sha256 !== experiment.exposure_plan_sha256
      || assignment.exposure_profile_id !== exposurePlan.profile_id
      || assignment.exposure_threads_user_id !== exposurePlan.threads_user_id_snapshot
      || assignment.exposure_username !== exposurePlan.username_snapshot
      || assignment.publication_profile_id !== exposurePlan.profile_id
      || assignment.publication_threads_user_id !== exposurePlan.threads_user_id_snapshot
      || assignment.publication_timezone !== assignment.exposure_timezone_snapshot
      || assignment.publication_wall_clock !== assignment.exposure_wall_clock_snapshot
      || !Number.isFinite(scheduledAt)
      || !Number.isFinite(publishedAt)
      || !Number.isFinite(toleranceMs)
      || Math.abs(publishedAt - scheduledAt) > toleranceMs
    ) {
      return false;
    }
  }
  return slotIds.size === expectedCount;
}

async function randomizedHypothesisOrder(hypothesisIds, experimentId, blockId, randomizationSeed) {
  const scored = await Promise.all(hypothesisIds.map(async (hypothesisId) => ({
    hypothesisId,
    score: await canonicalSha256({
      schema_version: "trace.experiment-randomization.v1",
      randomization_seed: randomizationSeed,
      experiment_id: experimentId,
      eligible_block_id: blockId,
      hypothesis_id: hypothesisId,
    }),
  })));
  return scored
    .sort((left, right) => (
      left.score.localeCompare(right.score)
      || left.hypothesisId.localeCompare(right.hypothesisId)
    ))
    .map((item) => item.hypothesisId);
}

export async function createVariantLink(env, account, campaignId, assignmentId, input) {
  const destinationUri = requiredString(input?.destination_uri, "destination_uri", 2000);
  let destination;
  try {
    destination = new URL(destinationUri);
  } catch {
    throw new MarketingAgentHttpError(400, "destination_uri가 올바르지 않습니다.");
  }
  if (!["https:", "trace:"].includes(destination.protocol)) {
    throw new MarketingAgentHttpError(400, "destination_uri protocol을 허용할 수 없습니다.");
  }
  const expiresAt = isoTimestamp(input?.expires_at, "expires_at");
  const expiry = Date.parse(expiresAt);
  if (expiry <= Date.now() || expiry > Date.now() + 365 * 24 * 60 * 60 * 1000) {
    throw new MarketingAgentHttpError(400, "variant link expiry가 허용 범위를 벗어났습니다.");
  }
  const row = await env.DB.prepare(
    `SELECT assignment.experiment_id, link.variant_id
     FROM hosted_marketing_post_assignments AS assignment
     JOIN hosted_marketing_campaigns AS campaign
       ON campaign.campaign_id = assignment.campaign_id
     LEFT JOIN hosted_marketing_variant_links AS link
       ON link.assignment_id = assignment.assignment_id
     WHERE campaign.account_id = ? AND campaign.campaign_id = ?
       AND assignment.assignment_id = ?`,
  ).bind(account.account_id, campaignId, assignmentId).first();
  if (!row) throw new MarketingAgentHttpError(404, "variant assignment를 찾을 수 없습니다.");
  if (row.variant_id) {
    throw new MarketingAgentHttpError(409, "assignment에는 이미 variant link가 있습니다.");
  }
  const token = `${crypto.randomUUID()}${crypto.randomUUID().replaceAll("-", "")}`;
  const tokenSha256 = await sha256Text(token);
  const variantId = `variant-${tokenSha256.slice(0, 48)}`;
  const now = new Date().toISOString();
  await env.DB.prepare(
    `INSERT INTO hosted_marketing_variant_links
      (variant_id, campaign_id, experiment_id, assignment_id, destination_uri,
       token_sha256, created_at, expires_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
  ).bind(
    variantId,
    campaignId,
    row.experiment_id,
    assignmentId,
    destination.toString(),
    tokenSha256,
    now,
    expiresAt,
  ).run();
  return {
    variant_id: variantId,
    assignment_id: assignmentId,
    url: `/api/marketing-agent/v/${encodeURIComponent(token)}`,
    token,
    expires_at: expiresAt,
  };
}

export async function resolveVariantLink(env, token) {
  const value = requiredString(token, "variant token", 200);
  const row = await env.DB.prepare(
    `SELECT destination_uri, expires_at FROM hosted_marketing_variant_links
     WHERE token_sha256 = ?`,
  ).bind(await sha256Text(value)).first();
  if (!row || Date.parse(row.expires_at) <= Date.now()) {
    throw new MarketingAgentHttpError(404, "variant link를 찾을 수 없습니다.");
  }
  const destination = new URL(row.destination_uri);
  destination.searchParams.set("trace_marketing_variant", value);
  return new Response(null, {
    status: 302,
    headers: {
      location: destination.toString(),
      "cache-control": "no-store",
      "referrer-policy": "no-referrer",
    },
  });
}

export async function ingestProductEvent(env, input) {
  if (!env.TRACE_EVENT_HASH_SALT) {
    throw new MarketingAgentHttpError(503, "product event hashing is not configured");
  }
  const eventId = safeId(input?.event_id, "event_id");
  if (input?.event_version !== "trace.product-event.v1") {
    throw new MarketingAgentHttpError(400, "product event version이 올바르지 않습니다.");
  }
  const eventType = requiredString(input?.event_type, "event_type", 80);
  if (!PRODUCT_EVENT_TYPES.has(eventType)) {
    throw new MarketingAgentHttpError(400, "product event type이 올바르지 않습니다.");
  }
  const variantToken = requiredString(input?.variant_token, "variant_token", 200);
  const installId = requiredString(input?.install_id, "install_id", 256);
  const occurredAt = isoTimestamp(input?.occurred_at, "occurred_at");
  if (Date.parse(occurredAt) > Date.now() + 5 * 60 * 1000) {
    throw new MarketingAgentHttpError(400, "future product event는 받을 수 없습니다.");
  }
  const payload = input?.payload == null ? {} : requireObject(input.payload, "payload");
  const payloadJson = canonicalJson(payload);
  if (new TextEncoder().encode(payloadJson).byteLength > 16 * 1024) {
    throw new MarketingAgentHttpError(413, "product event payload가 너무 큽니다.");
  }
  const variant = await env.DB.prepare(
    `SELECT variant_id, expires_at FROM hosted_marketing_variant_links
     WHERE token_sha256 = ?`,
  ).bind(await sha256Text(variantToken)).first();
  if (!variant || Date.parse(variant.expires_at) <= Date.now()) {
    throw new MarketingAgentHttpError(404, "product event variant를 찾을 수 없습니다.");
  }
  const installIdSha256 = await canonicalSha256({
    salt: env.TRACE_EVENT_HASH_SALT,
    install_id: installId,
  });
  const storedPayload = {
    event_version: "trace.product-event.v1",
    event_type: eventType,
    variant_id: variant.variant_id,
    occurred_at: occurredAt,
    payload,
  };
  const payloadSha256 = await canonicalSha256(storedPayload);
  const existing = await env.DB.prepare(
    `SELECT install_id_sha256, variant_id, payload_sha256
     FROM hosted_marketing_product_events WHERE event_id = ?`,
  ).bind(eventId).first();
  if (existing) {
    if (
      existing.install_id_sha256 !== installIdSha256
      || existing.variant_id !== variant.variant_id
      || existing.payload_sha256 !== payloadSha256
    ) {
      throw new MarketingAgentHttpError(409, "event_id가 다른 product event에 사용됐습니다.");
    }
    return { accepted: true, duplicate: true, event_id: eventId };
  }
  const observedAt = new Date().toISOString();
  await env.DB.prepare(
    `INSERT INTO hosted_marketing_product_events
      (event_id, event_version, event_type, install_id_sha256, variant_id,
       occurred_at, observed_at, payload_json, payload_sha256)
     VALUES (?, 'trace.product-event.v1', ?, ?, ?, ?, ?, ?, ?)`,
  ).bind(
    eventId,
    eventType,
    installIdSha256,
    variant.variant_id,
    occurredAt,
    observedAt,
    payloadJson,
    payloadSha256,
  ).run();
  return { accepted: true, duplicate: false, event_id: eventId, observed_at: observedAt };
}

export async function requestExperimentEvaluation(env, account, campaignId, input) {
  const projectionRevision = positiveInteger(input?.projection_revision, "projection_revision");
  const experiment = await env.DB.prepare(
    `SELECT campaign.state AS campaign_state, campaign.projection_revision,
            campaign.created_at AS campaign_created_at,
            experiment.experiment_id, experiment.state AS experiment_state,
            experiment.registration_json, experiment.registration_sha256,
            experiment.allocation_method, experiment.randomization_seed,
            experiment.randomization_seed_sha256,
            exposure_plan.plan_json AS exposure_plan_json,
            exposure_plan.plan_sha256 AS exposure_plan_sha256
     FROM hosted_marketing_campaigns AS campaign
     JOIN hosted_marketing_experiments AS experiment ON experiment.campaign_id = campaign.campaign_id
     LEFT JOIN hosted_marketing_experiment_exposure_plans AS exposure_plan
       ON exposure_plan.experiment_id = experiment.experiment_id
     WHERE campaign.account_id = ? AND campaign.campaign_id = ?`,
  ).bind(account.account_id, campaignId).first();
  if (!experiment) throw new MarketingAgentHttpError(404, "평가할 experiment를 찾을 수 없습니다.");
  if (Number(experiment.projection_revision) !== projectionRevision) {
    throw new MarketingAgentHttpError(409, "evaluation projection revision이 최신이 아닙니다.");
  }
  const existingEvaluation = await env.DB.prepare(
    `SELECT evaluation_id, state FROM hosted_marketing_experiment_evaluations
     WHERE experiment_id = ? ORDER BY evaluated_at DESC LIMIT 1`,
  ).bind(experiment.experiment_id).first();
  if (existingEvaluation) {
    return {
      campaign_id: campaignId,
      experiment_id: experiment.experiment_id,
      evaluation_id: existingEvaluation.evaluation_id,
      state: existingEvaluation.state,
      duplicate: true,
    };
  }
  const pendingTask = await env.DB.prepare(
    `SELECT task_id, state FROM hosted_workspace_capture_tasks
     WHERE account_id = ? AND idempotency_key = ? AND kind = 'marketing_judgment'
     LIMIT 1`,
  ).bind(
    account.account_id,
    `experiment-evaluation:${account.account_id}:${experiment.experiment_id}`,
  ).first();
  if (pendingTask) {
    return {
      campaign_id: campaignId,
      experiment_id: experiment.experiment_id,
      evaluation_id: `${experiment.experiment_id}.final`,
      task_id: pendingTask.task_id,
      state: pendingTask.state,
      duplicate: true,
    };
  }
  const registration = JSON.parse(experiment.registration_json);
  const allocationMethod = experiment.allocation_method ?? "balanced_complete_blocks";
  if (
    registration.primary_outcome?.scope === "estimated_treatment_effect"
    && (typeof experiment.randomization_seed_sha256 !== "string"
      || !/^[a-f0-9]{64}$/.test(experiment.randomization_seed_sha256))
  ) {
    throw new MarketingAgentHttpError(409, "causal experiment randomization plan이 손상되었습니다.");
  }
  const assignments = await env.DB.prepare(
    `SELECT assignment.assignment_id, assignment.eligible_block_id, assignment.allocation_rank,
            assignment.assignment_json,
            assignment.hypothesis_id, publication.publication_id,
            publication.state AS publication_state, publication.published_at,
            publication.scheduled_at AS publication_scheduled_at,
            publication.posting_slot_snapshot AS publication_posting_slot,
            publication.profile_id AS publication_profile_id,
            publication.threads_user_id_snapshot AS publication_threads_user_id,
            publication.timezone_snapshot AS publication_timezone,
            publication.wall_clock_snapshot AS publication_wall_clock,
            exposure.slot_id AS exposure_slot_id,
            exposure.posting_slot AS exposure_posting_slot,
            exposure.exposure_plan_sha256,
            exposure.profile_id_snapshot AS exposure_profile_id,
            exposure.threads_user_id_snapshot AS exposure_threads_user_id,
            exposure.username_snapshot AS exposure_username,
            exposure.timezone_snapshot AS exposure_timezone_snapshot,
            exposure.wall_clock_snapshot AS exposure_wall_clock_snapshot,
            exposure.scheduled_at AS exposure_scheduled_at,
            exposure.tolerance_seconds AS exposure_tolerance_seconds,
            exposure.commitment_json AS exposure_commitment_json,
            exposure.commitment_sha256 AS exposure_commitment_sha256,
            variant.variant_id
     FROM hosted_marketing_post_assignments AS assignment
     LEFT JOIN hosted_threads_publications AS publication
       ON publication.marketing_assignment_id = assignment.assignment_id
     LEFT JOIN hosted_marketing_variant_links AS variant
       ON variant.assignment_id = assignment.assignment_id
     LEFT JOIN hosted_marketing_exposure_slots AS exposure
       ON exposure.assignment_id = assignment.assignment_id
      AND exposure.experiment_id = assignment.experiment_id
     WHERE assignment.campaign_id = ? AND assignment.experiment_id = ?
     ORDER BY assignment.eligible_block_id, assignment.hypothesis_id`,
  ).bind(campaignId, experiment.experiment_id).all();
  const events = await env.DB.prepare(
    `SELECT event.event_id, event.variant_id, event.event_type, event.occurred_at
     FROM hosted_marketing_product_events AS event
     JOIN hosted_marketing_variant_links AS variant ON variant.variant_id = event.variant_id
     WHERE variant.campaign_id = ? AND variant.experiment_id = ?`,
  ).bind(campaignId, experiment.experiment_id).all();
  const now = new Date();
  const windowMs = Number(registration.primary_outcome.window_hours) * 60 * 60 * 1000;
  const maximumAt = Date.parse(experiment.campaign_created_at)
    + Number(registration.maximum_duration_hours) * 60 * 60 * 1000;
  const observations = assignments.results.map((assignment) => {
    validateEvaluationAssignmentAllocation(assignment, experiment, allocationMethod);
    const publishedAt = assignment.published_at ? Date.parse(assignment.published_at) : null;
    const windowClosed = publishedAt != null && now.getTime() >= publishedAt + windowMs;
    const productEvent = assignment.variant_id && publishedAt != null
      ? events.results.find((event) => (
        event.variant_id === assignment.variant_id
        && event.event_type === registration.primary_outcome.name
        && Date.parse(event.occurred_at) >= publishedAt
        && Date.parse(event.occurred_at) <= publishedAt + windowMs
      ))
      : null;
    const observed = assignment.publication_state === "published"
      && Boolean(assignment.variant_id)
      && windowClosed;
    return {
      assignment_id: assignment.assignment_id,
      eligible_block_id: assignment.eligible_block_id,
      hypothesis_id: assignment.hypothesis_id,
      publication_id: observed ? assignment.publication_id : null,
      product_event_id: observed && productEvent ? productEvent.event_id : null,
      eligible: assignment.publication_state === "published",
      attribution_observed: observed,
      converted: observed ? Boolean(productEvent) : null,
      guardrail_failures: assignment.publication_state === "unknown_side_effect"
        ? ["publication_unknown_side_effect"]
        : [],
    };
  });
  await validateCausalAllocationBlocks(assignments.results, registration, experiment, allocationMethod);
  const causalExposureVerified = await causalExposureSlotsVerified(
    assignments.results,
    registration,
    experiment,
    allocationMethod,
  );
  const minimumAssignments = Number(registration.minimum_eligible_blocks)
    * registration.activated_hypothesis_ids.length;
  const allWindowsClosed = observations.length >= minimumAssignments
    && observations.every((item) => item.attribution_observed);
  const windowsComplete = allWindowsClosed || now.getTime() >= maximumAt;
  if (!windowsComplete) {
    throw new MarketingAgentHttpError(409, "사전등록된 observation window가 아직 끝나지 않았습니다.");
  }
  const taskId = crypto.randomUUID();
  const evaluationId = `${experiment.experiment_id}.final`;
  const evaluatedAt = now.toISOString();
  const task = {
    schema_version: "1",
    task_id: taskId,
    run_id: `evaluation-${taskId}`,
    account_id: account.account_id,
    kind: "marketing_judgment",
    idempotency_key: `experiment-evaluation:${account.account_id}:${experiment.experiment_id}`,
    payload: {
      pipeline: MARKETING_JUDGMENT_PIPELINE,
      judgment: "experiment_evaluation",
      account_id: account.account_id,
      request: {
        evaluation_id: evaluationId,
        campaign_id: campaignId,
        registration,
        observations,
        randomization_seed_sha256: experiment.randomization_seed_sha256 ?? null,
        causal_exposure_verified: causalExposureVerified,
        windows_complete: windowsComplete,
        evaluated_at: evaluatedAt,
      },
      requested_by: "hosted_workspace",
    },
    created_at: evaluatedAt,
    credential_ref: null,
  };
  await env.DB.prepare(
    `INSERT INTO hosted_workspace_capture_tasks
      (task_id, run_id, account_id, candidate_id, candidate_revision, idempotency_key,
       task_json, state, dispatch_mode, kind, required_capability, created_at, updated_at)
     VALUES (?, ?, ?, '', 1, ?, ?, 'queued', 'worker_broker', 'marketing_judgment',
             NULL, ?, ?)`,
  ).bind(
    taskId,
    task.run_id,
    account.account_id,
    task.idempotency_key,
    JSON.stringify(task),
    evaluatedAt,
    evaluatedAt,
  ).run();
  return {
    campaign_id: campaignId,
    experiment_id: experiment.experiment_id,
    evaluation_id: evaluationId,
    task_id: taskId,
    state: "queued",
    windows_complete: windowsComplete,
  };
}

export async function runDueMarketingEvaluations(env) {
  const campaigns = await env.DB.prepare(
    `SELECT campaign.campaign_id, campaign.projection_revision,
            account.account_id, account.country, account.language, account.timezone
     FROM hosted_marketing_campaigns AS campaign
     JOIN hosted_workspace_accounts AS account ON account.account_id = campaign.account_id
     JOIN hosted_marketing_experiments AS experiment ON experiment.campaign_id = campaign.campaign_id
     LEFT JOIN hosted_marketing_experiment_evaluations AS evaluation
       ON evaluation.experiment_id = experiment.experiment_id
     WHERE campaign.mode = 'assisted'
       AND campaign.state IN ('creative_planned', 'awaiting_review', 'approved_for_publish',
                              'scheduled', 'publishing', 'published', 'outcome_unknown',
                              'observing')
       AND evaluation.evaluation_id IS NULL
     ORDER BY campaign.updated_at
     LIMIT 50`,
  ).all();
  let queued = 0;
  let notDue = 0;
  let failed = 0;
  for (const row of campaigns.results) {
    try {
      const result = await requestExperimentEvaluation(
        env,
        {
          account_id: row.account_id,
          country: row.country,
          language: row.language,
          timezone: row.timezone,
        },
        row.campaign_id,
        { projection_revision: Number(row.projection_revision) },
      );
      if (result.duplicate) notDue += 1;
      else queued += 1;
    } catch (error) {
      if (error instanceof MarketingAgentHttpError && error.status === 409) notDue += 1;
      else failed += 1;
    }
  }
  return { inspected: campaigns.results.length, queued, not_due: notDue, failed };
}

export async function requestLearningSynthesis(env, account, input) {
  const learningId = safeId(input?.learning_id, "learning_id");
  const evaluationIds = requireArray(input?.evaluation_ids, "evaluation_ids", 2, 32)
    .map((id) => safeId(id, "evaluation_id"));
  if (new Set(evaluationIds).size !== evaluationIds.length) {
    throw new MarketingAgentHttpError(400, "learning evaluation IDs는 고유해야 합니다.");
  }
  const placeholders = evaluationIds.map(() => "?").join(",");
  const result = await env.DB.prepare(
    `SELECT evaluation.evaluation_id, evaluation.evaluation_json,
            campaign.campaign_id, campaign.mode, campaign.feature_packet_sha256,
            campaign.marketing_context_snapshot_sha256,
            packet.feature_id, account.country, account.language,
            hypothesis.hypothesis_json, treatment.treatment_json
     FROM hosted_marketing_experiment_evaluations AS evaluation
     JOIN hosted_marketing_campaigns AS campaign
       ON campaign.campaign_id = evaluation.campaign_id
     JOIN hosted_workspace_accounts AS account ON account.account_id = campaign.account_id
     JOIN hosted_marketing_feature_packets AS packet
       ON packet.packet_id = campaign.feature_packet_id
      AND packet.packet_sha256 = campaign.feature_packet_sha256
     JOIN hosted_marketing_hypotheses AS hypothesis
       ON hypothesis.campaign_id = campaign.campaign_id
      AND hypothesis.hypothesis_id = json_extract(
        evaluation.evaluation_json, '$.winner_hypothesis_id'
      )
     JOIN hosted_marketing_creative_treatments AS treatment
       ON treatment.campaign_id = campaign.campaign_id
      AND treatment.hypothesis_id = hypothesis.hypothesis_id
     WHERE campaign.account_id = ? AND evaluation.state = 'evaluated'
       AND evaluation.evaluation_id IN (${placeholders})`,
  ).bind(account.account_id, ...evaluationIds).all();
  const byId = new Map(result.results.map((row) => [row.evaluation_id, row]));
  if (byId.size !== evaluationIds.length) {
    throw new MarketingAgentHttpError(409, "독립 evaluated lineage를 모두 찾을 수 없습니다.");
  }
  const ordered = evaluationIds.map((id) => byId.get(id));
  if (new Set(ordered.map((row) => row.campaign_id)).size !== ordered.length) {
    throw new MarketingAgentHttpError(409, "learning에는 독립 campaign이 필요합니다.");
  }
  const applicability = learningApplicabilityFromLineages(account.account_id, ordered);
  const existing = await env.DB.prepare(
    `SELECT learning_id FROM hosted_marketing_learning_candidates WHERE learning_id = ?`,
  ).bind(learningId).first();
  if (existing) {
    return { learning_id: learningId, state: "candidate", duplicate: true };
  }
  const taskId = crypto.randomUUID();
  const now = new Date().toISOString();
  const targetCampaignId = ordered.at(-1).campaign_id;
  const task = {
    schema_version: "1",
    task_id: taskId,
    run_id: `learning-${taskId}`,
    account_id: account.account_id,
    kind: "marketing_judgment",
    idempotency_key: `learning-synthesis:${account.account_id}:${learningId}`,
    payload: {
      pipeline: MARKETING_JUDGMENT_PIPELINE,
      judgment: "learning_synthesis",
      learning_id: learningId,
      target_campaign_id: targetCampaignId,
      account_id: account.account_id,
      applicability,
      lineages: ordered.map((row) => ({
        evaluation: JSON.parse(row.evaluation_json),
        winner_hypothesis: JSON.parse(row.hypothesis_json),
        winner_treatment: JSON.parse(row.treatment_json),
      })),
      requested_by: "hosted_workspace",
    },
    created_at: now,
    credential_ref: null,
  };
  await env.DB.prepare(
    `INSERT INTO hosted_workspace_capture_tasks
      (task_id, run_id, account_id, candidate_id, candidate_revision, idempotency_key,
       task_json, state, dispatch_mode, kind, required_capability, created_at, updated_at)
     VALUES (?, ?, ?, '', 1, ?, ?, 'queued', 'worker_broker', 'marketing_judgment',
             NULL, ?, ?)`,
  ).bind(
    taskId,
    task.run_id,
    account.account_id,
    task.idempotency_key,
    JSON.stringify(task),
    now,
    now,
  ).run();
  return {
    learning_id: learningId,
    task_id: taskId,
    target_campaign_id: targetCampaignId,
    evaluation_ids: evaluationIds,
    state: "queued",
  };
}

export async function decideLearningCandidate(env, account, learningId, input) {
  const candidateSha256 = sha256Digest(input?.candidate_sha256, "candidate_sha256");
  const reviewerId = safeId(input?.reviewer_id, "reviewer_id");
  const decision = input?.decision;
  if (!["approved", "rejected"].includes(decision)) {
    throw new MarketingAgentHttpError(400, "learning decision이 올바르지 않습니다.");
  }
  const row = await env.DB.prepare(
    `SELECT learning.learning_id, learning.campaign_id, learning.candidate_json,
            learning.candidate_sha256, learning.state,
            campaign.projection_revision
     FROM hosted_marketing_learning_candidates AS learning
     JOIN hosted_marketing_campaigns AS campaign ON campaign.campaign_id = learning.campaign_id
     WHERE campaign.account_id = ? AND learning.learning_id = ?`,
  ).bind(account.account_id, learningId).first();
  if (!row) throw new MarketingAgentHttpError(404, "learning candidate를 찾을 수 없습니다.");
  if (row.candidate_sha256 !== candidateSha256 || row.state !== "candidate") {
    throw new MarketingAgentHttpError(409, "learning approval 대상이 최신 candidate가 아닙니다.");
  }
  const now = new Date().toISOString();
  const grantId = `learning-${candidateSha256.slice(0, 48)}-${reviewerId}`;
  const statements = [
    env.DB.prepare(
      `INSERT INTO hosted_marketing_approval_grants
        (grant_id, campaign_id, scope, target_kind, target_id, target_sha256,
         decision, reviewer_id, reviewed_at)
       VALUES (?, ?, 'learning', 'learning_candidate', ?, ?, ?, ?, ?)`,
    ).bind(
      grantId,
      row.campaign_id,
      learningId,
      candidateSha256,
      decision,
      reviewerId,
      now,
    ),
    env.DB.prepare(
      `UPDATE hosted_marketing_learning_candidates
       SET state = ?, updated_at = ? WHERE learning_id = ? AND state = 'candidate'`,
    ).bind(decision, now, learningId),
  ];
  let principleId = null;
  if (decision === "approved") {
    const candidate = JSON.parse(row.candidate_json);
    if (!isMarketingLearningApplicability(candidate.applicability)) {
      throw new MarketingAgentHttpError(
        409,
        "legacy learning candidate에는 구조화된 applicability가 필요합니다.",
      );
    }
    const principle = {
      schema_version: "trace.marketing-principle.v1",
      principle_id: `principle-${candidateSha256.slice(0, 48)}`,
      learning_id: learningId,
      statement: candidate.statement,
      scope: candidate.scope,
      applicability: candidate.applicability,
      independent_lineage_ids: candidate.independent_lineage_ids,
      state: "provisional",
      created_at: now,
    };
    principleId = principle.principle_id;
    statements.push(
      env.DB.prepare(
        `INSERT INTO hosted_marketing_principles
          (principle_id, learning_id, approval_grant_id, principle_json,
           principle_sha256, state, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, 'provisional', ?, ?)`,
      ).bind(
        principleId,
        learningId,
        grantId,
        canonicalJson(principle),
        await canonicalSha256(principle),
        now,
        now,
      ),
    );
  }
  const nextRevision = Number(row.projection_revision) + 1;
  const event = {
    campaign_id: row.campaign_id,
    learning_id: learningId,
    candidate_sha256: candidateSha256,
    decision,
    principle_id: principleId,
    reviewer_id: reviewerId,
  };
  statements.push(
    env.DB.prepare(
      `UPDATE hosted_marketing_campaigns
       SET state = 'completed', projection_revision = ?, updated_at = ?
       WHERE campaign_id = ? AND state = 'learning_candidate' AND projection_revision = ?`,
    ).bind(nextRevision, now, row.campaign_id, row.projection_revision),
    env.DB.prepare(
      `INSERT INTO hosted_marketing_run_events
        (event_id, campaign_id, sequence, prior_revision, resulting_revision, event_type,
         event_json, event_sha256, idempotency_key, causation_id, correlation_id,
         event_time, observed_at, actor_type)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'human')`,
    ).bind(
      crypto.randomUUID(),
      row.campaign_id,
      nextRevision,
      row.projection_revision,
      nextRevision,
      decision === "approved" ? "learning_approved" : "learning_rejected",
      canonicalJson(event),
      await canonicalSha256(event),
      `campaign:${row.campaign_id}:learning-review:${candidateSha256}:${reviewerId}`,
      learningId,
      row.campaign_id,
      now,
      now,
    ),
  );
  const results = await env.DB.batch(statements);
  if (results.some((result) => result?.meta?.changes !== 1)) {
    throw new MarketingAgentHttpError(409, "learning approval이 다른 요청과 충돌했습니다.");
  }
  return {
    learning_id: learningId,
    decision,
    state: decision,
    principle_id: principleId,
  };
}

export async function bindCandidateAssignment(env, account, campaignId, input) {
  const assignmentId = safeId(input?.assignment_id, "assignment_id");
  const candidateId = safeId(input?.candidate_id, "candidate_id");
  const hypothesisId = safeId(input?.hypothesis_id, "hypothesis_id");
  const treatmentId = safeId(input?.treatment_id, "treatment_id");
  const blockId = safeId(input?.eligible_block_id, "eligible_block_id");
  const candidateRevision = positiveInteger(input?.candidate_revision, "candidate_revision");
  const projectionRevision = positiveInteger(input?.projection_revision, "projection_revision");
  const row = await env.DB.prepare(
    `SELECT campaign.mode, campaign.state, campaign.projection_revision,
            experiment.experiment_id, experiment.allocation_method,
            plan.plan_id, plan.plan_sha256, plan.state AS plan_state,
            plan.publication_allowed, treatment.hypothesis_id AS treatment_hypothesis_id,
            candidate.revision, candidate.status, candidate.marketing_assignment_id,
            candidate.caption, candidate.hypothesis, candidate.appium_prompt,
            candidate.image_inputs_json, candidate.context_snapshot_json, candidate.persona_id,
            existing.hypothesis_id AS existing_hypothesis_id,
            existing.treatment_id AS existing_treatment_id,
            existing.eligible_block_id AS existing_block_id
     FROM hosted_marketing_campaigns AS campaign
     JOIN hosted_marketing_experiments AS experiment ON experiment.campaign_id = campaign.campaign_id
     JOIN hosted_marketing_media_plans AS plan ON plan.campaign_id = campaign.campaign_id
     JOIN hosted_marketing_feature_packets AS packet
       ON packet.packet_id = campaign.feature_packet_id
      AND packet.packet_sha256 = campaign.feature_packet_sha256
     JOIN hosted_marketing_product_truth_approvals AS truth
       ON truth.packet_id = packet.packet_id AND truth.packet_sha256 = packet.packet_sha256
      AND truth.decision = 'approved'
     JOIN hosted_marketing_creative_treatments AS treatment
       ON treatment.plan_id = plan.plan_id AND treatment.experiment_id = experiment.experiment_id
     JOIN hosted_workspace_candidates AS candidate ON candidate.account_id = campaign.account_id
     LEFT JOIN hosted_marketing_post_assignments AS existing
       ON existing.assignment_id = candidate.marketing_assignment_id
     WHERE campaign.account_id = ? AND campaign.campaign_id = ?
       AND candidate.candidate_id = ? AND treatment.treatment_id = ?
       AND EXISTS (
         SELECT 1 FROM hosted_marketing_approval_grants AS grant
         WHERE grant.campaign_id = campaign.campaign_id
           AND grant.scope = 'creative' AND grant.target_kind = 'media_plan'
           AND grant.target_id = plan.plan_id AND grant.target_sha256 = plan.plan_sha256
           AND grant.decision = 'approved'
       )
       AND packet.publication_allowed = 1`,
  ).bind(account.account_id, campaignId, candidateId, treatmentId).first();
  if (!row) throw new MarketingAgentHttpError(404, "assignment 대상을 찾을 수 없습니다.");
  if (row.marketing_assignment_id === assignmentId) {
    if (
      row.existing_hypothesis_id !== hypothesisId
      || row.existing_treatment_id !== treatmentId
      || row.existing_block_id !== blockId
    ) {
      throw new MarketingAgentHttpError(409, "assignment_id가 다른 배정에 이미 사용됐습니다.");
    }
    return { assignment_id: assignmentId, campaign_id: campaignId, duplicate: true };
  }
  if (
    row.mode === "shadow"
    || row.allocation_method === "server_randomized_complete_blocks_v1"
    || !["assisted", "live"].includes(row.mode)
    || !["creative_planned", "awaiting_review"].includes(row.state)
    || row.plan_state !== "approved"
    || Number(row.publication_allowed) !== 1
    || row.treatment_hypothesis_id !== hypothesisId
    || Number(row.revision) !== candidateRevision
    || Number(row.projection_revision) !== projectionRevision
    || row.status !== "awaiting_review"
    || row.marketing_assignment_id
    || candidateRevision < 1
  ) {
    throw new MarketingAgentHttpError(409, "candidate assignment gate가 닫혀 있습니다.");
  }
  const now = new Date().toISOString();
  const candidateContent = {
    caption: row.caption,
    hypothesis: row.hypothesis,
    appium_prompt: row.appium_prompt,
    image_inputs: JSON.parse(row.image_inputs_json),
    context_snapshot: row.context_snapshot_json ? JSON.parse(row.context_snapshot_json) : null,
    persona_id: row.persona_id ?? null,
  };
  const candidateContentSha256 = await canonicalSha256(candidateContent);
  const assignment = {
    assignment_id: assignmentId,
    campaign_id: campaignId,
    experiment_id: row.experiment_id,
    hypothesis_id: hypothesisId,
    treatment_id: treatmentId,
    candidate_id: candidateId,
    candidate_revision: candidateRevision,
    candidate_content_sha256: candidateContentSha256,
    eligible_block_id: blockId,
    media_plan_sha256: row.plan_sha256,
    assigned_at: now,
  };
  const assignmentSha256 = await canonicalSha256(assignment);
  const nextRevision = projectionRevision + 1;
  const results = await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO hosted_marketing_post_assignments
        (assignment_id, campaign_id, experiment_id, hypothesis_id, treatment_id,
         candidate_id, candidate_revision, candidate_content_sha256, eligible_block_id,
         assignment_json, assignment_sha256, assigned_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      assignmentId,
      campaignId,
      row.experiment_id,
      hypothesisId,
      treatmentId,
      candidateId,
      candidateRevision,
      candidateContentSha256,
      blockId,
      canonicalJson(assignment),
      assignmentSha256,
      now,
    ),
    env.DB.prepare(
      `UPDATE hosted_workspace_candidates
       SET marketing_campaign_id = ?, marketing_experiment_id = ?,
           marketing_hypothesis_id = ?, marketing_treatment_id = ?,
           marketing_assignment_id = ?, marketing_assignment_sha256 = ?,
           revision = revision + 1, updated_at = ?
       WHERE account_id = ? AND candidate_id = ? AND revision = ?
         AND status = 'awaiting_review' AND marketing_assignment_id IS NULL`,
    ).bind(
      campaignId,
      row.experiment_id,
      hypothesisId,
      treatmentId,
      assignmentId,
      assignmentSha256,
      Date.now() / 1000,
      account.account_id,
      candidateId,
      candidateRevision,
    ),
    env.DB.prepare(
      `UPDATE hosted_marketing_campaigns
       SET state = 'awaiting_review', projection_revision = ?, updated_at = ?
       WHERE account_id = ? AND campaign_id = ?
         AND state IN ('creative_planned', 'awaiting_review')
         AND projection_revision = ?`,
    ).bind(nextRevision, now, account.account_id, campaignId, projectionRevision),
    env.DB.prepare(
      `INSERT INTO hosted_marketing_run_events
        (event_id, campaign_id, sequence, prior_revision, resulting_revision, event_type,
         event_json, event_sha256, idempotency_key, causation_id, correlation_id,
         event_time, observed_at, actor_type)
       VALUES (?, ?, ?, ?, ?, 'candidate_assigned', ?, ?, ?, ?, ?, ?, ?, 'human')`,
    ).bind(
      crypto.randomUUID(),
      campaignId,
      nextRevision,
      projectionRevision,
      nextRevision,
      canonicalJson(assignment),
      assignmentSha256,
      `campaign:${campaignId}:assignment:${assignmentId}`,
      candidateId,
      campaignId,
      now,
      now,
    ),
  ]);
  if (results[1]?.meta?.changes !== 1 || results[2]?.meta?.changes !== 1) {
    throw new MarketingAgentHttpError(409, "candidate assignment가 최신 상태와 충돌했습니다.");
  }
  return {
    assignment_id: assignmentId,
    assignment_sha256: assignmentSha256,
    campaign_id: campaignId,
    experiment_id: row.experiment_id,
    candidate_id: candidateId,
    candidate_revision: candidateRevision + 1,
    projection_revision: nextRevision,
    duplicate: false,
  };
}

async function listCampaigns(env, accountId) {
  const result = await env.DB.prepare(
    `SELECT campaign_id, feature_packet_id, feature_packet_sha256, mode, origin_campaign_id, state,
            projection_revision, business_outcome, marketing_context_snapshot_id,
            marketing_context_snapshot_sha256, created_at, updated_at
     FROM hosted_marketing_campaigns WHERE account_id = ?
     ORDER BY created_at DESC LIMIT ?`,
  ).bind(accountId, MAX_CAMPAIGNS).all();
  return result.results;
}

export async function importCustomerSignal(env, account, input) {
  const signal = normalizeCustomerSignal(input, account.account_id);
  const signalSha256 = await canonicalSha256(signal);
  const existing = await env.DB.prepare(
    `SELECT signal_sha256, review_state FROM hosted_marketing_customer_signals
     WHERE account_id = ? AND signal_id = ?`,
  ).bind(account.account_id, signal.signal_id).first();
  if (existing) {
    if (existing.signal_sha256 !== signalSha256) {
      throw new MarketingAgentHttpError(409, "signal_id가 다른 customer signal에 이미 사용됐습니다.");
    }
    return {
      signal_id: signal.signal_id,
      signal_sha256: signalSha256,
      review_state: existing.review_state,
      duplicate: true,
    };
  }
  const now = new Date().toISOString();
  await env.DB.prepare(
    `INSERT INTO hosted_marketing_customer_signals
      (signal_id, account_id, schema_version, source_kind, source_ref, source_sha256,
       audience_segment_id, signal_kind, consent_status, confidence_basis_points,
       observed_at, fresh_until, retention_until, review_state, reviewer_id, reviewed_at,
       signal_json, signal_sha256, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, ?, ?)`,
  ).bind(
    signal.signal_id,
    account.account_id,
    signal.schema_version,
    signal.source_kind,
    signal.source_ref,
    signal.source_sha256,
    signal.audience_segment_id,
    signal.kind,
    signal.consent_status,
    signal.confidence_basis_points,
    signal.observed_at,
    signal.fresh_until,
    signal.retention_until,
    canonicalJson(signal),
    signalSha256,
    now,
  ).run();
  return {
    signal_id: signal.signal_id,
    signal_sha256: signalSha256,
    review_state: "pending",
    duplicate: false,
  };
}

export async function decideCustomerSignal(env, account, signalId, input) {
  assertExactKeys(input, ["decision", "reviewer_id"], "customer signal approval");
  const decision = input?.decision;
  if (!['approved', 'rejected'].includes(decision)) {
    throw new MarketingAgentHttpError(400, "customer signal decision은 approved 또는 rejected여야 합니다.");
  }
  const reviewerId = safeId(input?.reviewer_id, "reviewer_id");
  const existing = await env.DB.prepare(
    `SELECT signal_sha256, review_state, reviewer_id FROM hosted_marketing_customer_signals
     WHERE account_id = ? AND signal_id = ?`,
  ).bind(account.account_id, signalId).first();
  if (!existing) throw new MarketingAgentHttpError(404, "customer signal을 찾을 수 없습니다.");
  if (existing.review_state !== "pending") {
    if (existing.review_state === decision && existing.reviewer_id === reviewerId) {
      return {
        signal_id: signalId,
        signal_sha256: existing.signal_sha256,
        review_state: existing.review_state,
        duplicate: true,
      };
    }
    throw new MarketingAgentHttpError(409, "customer signal의 검수 결정은 이미 고정되었습니다.");
  }
  const reviewedAt = new Date().toISOString();
  const result = await env.DB.prepare(
    `UPDATE hosted_marketing_customer_signals
     SET review_state = ?, reviewer_id = ?, reviewed_at = ?
     WHERE account_id = ? AND signal_id = ? AND review_state = 'pending'`,
  ).bind(decision, reviewerId, reviewedAt, account.account_id, signalId).run();
  if (result.meta.changes !== 1) {
    throw new MarketingAgentHttpError(409, "customer signal 검수가 최신 상태와 충돌했습니다.");
  }
  return {
    signal_id: signalId,
    signal_sha256: existing.signal_sha256,
    review_state: decision,
    reviewer_id: reviewerId,
    reviewed_at: reviewedAt,
    duplicate: false,
  };
}

export async function createMarketingContextSnapshot(env, account, input) {
  assertExactKeys(input, [
    "snapshot_id",
    "brand_guardrails",
    "audience_context",
    "channel_policy_ids",
    "signal_ids",
    "reviewer_id",
    "expires_at",
  ], "marketing context snapshot");
  const snapshotId = safeId(input?.snapshot_id, "snapshot_id");
  const signalIds = requireArray(input?.signal_ids, "signal_ids", 1, 24)
    .map((signalId) => safeId(signalId, "signal_id"));
  if (new Set(signalIds).size !== signalIds.length) {
    throw new MarketingAgentHttpError(400, "marketing context의 signal_id는 중복될 수 없습니다.");
  }
  const brandGuardrails = normalizedStringList(input?.brand_guardrails, "brand_guardrails", 1, 16, 1200);
  const audienceContext = normalizedStringList(input?.audience_context, "audience_context", 1, 16, 1200);
  const channelPolicyIds = requireArray(input?.channel_policy_ids ?? [], "channel_policy_ids", 0, 16)
    .map((policyId) => safeId(policyId, "channel_policy_id"));
  if (new Set(channelPolicyIds).size !== channelPolicyIds.length) {
    throw new MarketingAgentHttpError(400, "channel_policy_id는 중복될 수 없습니다.");
  }
  const reviewerId = safeId(input?.reviewer_id, "reviewer_id");
  const expiresAt = isoTimestamp(input?.expires_at, "expires_at");
  const existing = await env.DB.prepare(
    `SELECT snapshot_json, snapshot_sha256 FROM hosted_marketing_context_snapshots
     WHERE account_id = ? AND snapshot_id = ?`,
  ).bind(account.account_id, snapshotId).first();
  if (existing) {
    const storedSnapshot = await parseMarketingContextSnapshot(
      existing,
      account.account_id,
      snapshotId,
    );
    const requestedIntent = marketingContextIntent({
      brand_guardrails: brandGuardrails,
      audience_context: audienceContext,
      channel_policy_ids: channelPolicyIds,
      signal_ids: signalIds,
      reviewer_id: reviewerId,
      expires_at: expiresAt,
    });
    const storedIntent = marketingContextIntent({
      brand_guardrails: storedSnapshot.brand_guardrails,
      audience_context: storedSnapshot.audience_context,
      channel_policy_ids: storedSnapshot.channel_policy_ids,
      signal_ids: requireArray(storedSnapshot.customer_signals, "stored context signals", 1, 24)
        .map((signal) => safeId(requireObject(signal, "stored context signal").signal_id, "signal_id")),
      reviewer_id: storedSnapshot.approved_by,
      expires_at: storedSnapshot.expires_at,
    });
    if (canonicalJson(requestedIntent) !== canonicalJson(storedIntent)) {
      throw new MarketingAgentHttpError(409, "snapshot_id가 다른 marketing context에 이미 사용됐습니다.");
    }
    return {
      snapshot_id: snapshotId,
      snapshot_sha256: existing.snapshot_sha256,
      projection: marketingContextPlanningProjection(storedSnapshot, existing.snapshot_sha256),
      duplicate: true,
    };
  }
  const now = new Date().toISOString();
  if (Date.parse(expiresAt) <= Date.parse(now)) {
    throw new MarketingAgentHttpError(400, "marketing context는 미래에 만료되어야 합니다.");
  }
  const signals = [];
  for (const signalId of signalIds) {
    const row = await env.DB.prepare(
      `SELECT signal_json, signal_sha256, review_state, consent_status, fresh_until, retention_until
       FROM hosted_marketing_customer_signals WHERE account_id = ? AND signal_id = ?`,
    ).bind(account.account_id, signalId).first();
    if (
      !row
      || row.review_state !== "approved"
      || row.consent_status !== "confirmed"
      || Date.parse(row.fresh_until) < Date.parse(expiresAt)
      || Date.parse(row.retention_until) < Date.parse(expiresAt)
    ) {
      throw new MarketingAgentHttpError(409, "승인·동의·보존 기간이 유효한 customer signal만 context에 넣을 수 있습니다.");
    }
    let signal;
    try {
      signal = requireObject(JSON.parse(row.signal_json), "stored customer signal");
    } catch (error) {
      if (error instanceof MarketingAgentHttpError) throw error;
      throw new MarketingAgentHttpError(409, "stored customer signal이 손상되었습니다.");
    }
    if (await canonicalSha256(signal) !== row.signal_sha256) {
      throw new MarketingAgentHttpError(409, "stored customer signal digest가 일치하지 않습니다.");
    }
    signals.push(customerSignalPlanningProjection(signal, row.signal_sha256));
  }
  const snapshot = {
    schema_version: "trace.marketing-context.v1",
    snapshot_id: snapshotId,
    account_id: account.account_id,
    brand_guardrails: brandGuardrails,
    audience_context: audienceContext,
    channel_policy_ids: channelPolicyIds,
    customer_signals: signals,
    approved_by: reviewerId,
    approved_at: now,
    expires_at: expiresAt,
  };
  const snapshotSha256 = await canonicalSha256(snapshot);
  await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO hosted_marketing_context_snapshots
        (snapshot_id, account_id, schema_version, snapshot_json, snapshot_sha256,
         approved_by, approved_at, expires_at, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      snapshotId,
      account.account_id,
      snapshot.schema_version,
      canonicalJson(snapshot),
      snapshotSha256,
      reviewerId,
      now,
      expiresAt,
      now,
    ),
    ...signals.map((signal) => env.DB.prepare(
      `INSERT INTO hosted_marketing_context_snapshot_signals
        (snapshot_id, signal_id, signal_sha256) VALUES (?, ?, ?)`,
    ).bind(snapshotId, signal.signal_id, signal.signal_sha256)),
  ]);
  return {
    snapshot_id: snapshotId,
    snapshot_sha256: snapshotSha256,
    projection: marketingContextPlanningProjection(snapshot, snapshotSha256),
    duplicate: false,
  };
}

async function listCustomerSignals(env, accountId) {
  const now = new Date().toISOString();
  const result = await env.DB.prepare(
    `SELECT signal_id, signal_sha256, review_state, audience_segment_id, signal_kind,
            confidence_basis_points, observed_at, fresh_until, retention_until, reviewed_at
     FROM hosted_marketing_customer_signals WHERE account_id = ? AND retention_until > ?
     ORDER BY observed_at DESC LIMIT 100`,
  ).bind(accountId, now).all();
  return result.results;
}

async function marketingContextSnapshotStatus(env, accountId, snapshotId) {
  const row = await env.DB.prepare(
    `SELECT snapshot_json, snapshot_sha256, approved_by, approved_at, expires_at
     FROM hosted_marketing_context_snapshots WHERE account_id = ? AND snapshot_id = ?`,
  ).bind(accountId, snapshotId).first();
  if (!row) throw new MarketingAgentHttpError(404, "marketing context snapshot을 찾을 수 없습니다.");
  if (Date.parse(row.expires_at) <= Date.now()) {
    throw new MarketingAgentHttpError(410, "marketing context snapshot이 만료되었습니다.");
  }
  const snapshot = await parseMarketingContextSnapshot(row, accountId, snapshotId);
  return {
    snapshot_id: snapshot.snapshot_id,
    snapshot_sha256: row.snapshot_sha256,
    approved_by: row.approved_by,
    approved_at: row.approved_at,
    expires_at: row.expires_at,
    expired: Date.parse(row.expires_at) < Date.now(),
    projection: marketingContextPlanningProjection(snapshot, row.snapshot_sha256),
  };
}

export async function resolveMarketingContextProjection(database, accountId, snapshotId) {
  if (snapshotId == null) return null;
  const safeSnapshotId = safeId(snapshotId, "marketing_context_snapshot_id");
  const row = await database.prepare(
    `SELECT snapshot_json, snapshot_sha256, expires_at
     FROM hosted_marketing_context_snapshots WHERE account_id = ? AND snapshot_id = ?`,
  ).bind(accountId, safeSnapshotId).first();
  if (!row || Date.parse(row.expires_at) <= Date.now()) {
    throw new MarketingAgentHttpError(409, "유효한 marketing context snapshot을 찾을 수 없습니다.");
  }
  const snapshot = await parseMarketingContextSnapshot(row, accountId, safeSnapshotId);
  return marketingContextPlanningProjection(snapshot, row.snapshot_sha256);
}

async function parseMarketingContextSnapshot(row, accountId, snapshotId) {
  let snapshot;
  try {
    snapshot = requireObject(JSON.parse(row.snapshot_json), "marketing context snapshot");
  } catch (error) {
    if (error instanceof MarketingAgentHttpError) throw error;
    throw new MarketingAgentHttpError(409, "marketing context snapshot이 손상되었습니다.");
  }
  if (
    snapshot.schema_version !== "trace.marketing-context.v1"
    || snapshot.snapshot_id !== snapshotId
    || snapshot.account_id !== accountId
    || await canonicalSha256(snapshot) !== row.snapshot_sha256
  ) {
    throw new MarketingAgentHttpError(409, "marketing context snapshot binding이 유효하지 않습니다.");
  }
  return snapshot;
}

function marketingContextIntent(value) {
  return {
    brand_guardrails: value.brand_guardrails,
    audience_context: value.audience_context,
    channel_policy_ids: value.channel_policy_ids,
    signal_ids: value.signal_ids,
    reviewer_id: value.reviewer_id,
    expires_at: value.expires_at,
  };
}

function normalizeCustomerSignal(value, accountId) {
  assertExactKeys(value, [
    "schema_version",
    "signal_id",
    "source_kind",
    "source_ref",
    "source_sha256",
    "audience_segment_id",
    "kind",
    "summary",
    "caveats",
    "confidence_basis_points",
    "consent_status",
    "observed_at",
    "fresh_until",
    "retention_until",
  ], "customer signal");
  if (value?.schema_version !== "trace.customer-signal.v1") {
    throw new MarketingAgentHttpError(400, "customer signal schema가 올바르지 않습니다.");
  }
  if (value.source_kind !== "manual_normalized") {
    throw new MarketingAgentHttpError(400, "v1 customer signal은 manual_normalized source만 허용합니다.");
  }
  if (!["need", "objection", "desired_outcome", "audience_language", "behavior"].includes(value.kind)) {
    throw new MarketingAgentHttpError(400, "customer signal kind가 올바르지 않습니다.");
  }
  if (value.consent_status !== "confirmed") {
    throw new MarketingAgentHttpError(400, "customer signal에는 confirmed consent가 필요합니다.");
  }
  const observedAt = isoTimestamp(value.observed_at, "observed_at");
  const freshUntil = isoTimestamp(value.fresh_until, "fresh_until");
  const retentionUntil = isoTimestamp(value.retention_until, "retention_until");
  if (!(Date.parse(observedAt) <= Date.parse(freshUntil) && Date.parse(freshUntil) <= Date.parse(retentionUntil))) {
    throw new MarketingAgentHttpError(400, "customer signal의 freshness/retention 시간이 올바르지 않습니다.");
  }
  const caveats = normalizedStringList(value.caveats ?? [], "caveats", 0, 8, 1200);
  return {
    schema_version: "trace.customer-signal.v1",
    signal_id: safeId(value.signal_id, "signal_id"),
    account_id: accountId,
    source_kind: "manual_normalized",
    source_ref: requiredString(value.source_ref, "source_ref", 500),
    source_sha256: sha256Digest(value.source_sha256, "source_sha256"),
    audience_segment_id: safeId(value.audience_segment_id, "audience_segment_id"),
    kind: value.kind,
    summary: requiredString(value.summary, "summary", 1200),
    caveats,
    confidence_basis_points: basisPoints(value.confidence_basis_points, "confidence_basis_points"),
    consent_status: "confirmed",
    observed_at: observedAt,
    fresh_until: freshUntil,
    retention_until: retentionUntil,
  };
}

function customerSignalPlanningProjection(signal, signalSha256) {
  return {
    schema_version: "trace.customer-signal-projection.v1",
    signal_id: signal.signal_id,
    signal_sha256: signalSha256,
    audience_segment_id: signal.audience_segment_id,
    kind: signal.kind,
    summary: signal.summary,
    caveats: signal.caveats,
    confidence_basis_points: signal.confidence_basis_points,
    observed_at: signal.observed_at,
    fresh_until: signal.fresh_until,
  };
}

function marketingContextPlanningProjection(snapshot, snapshotSha256) {
  return {
    schema_version: "trace.marketing-context-projection.v1",
    snapshot_id: snapshot.snapshot_id,
    snapshot_sha256: snapshotSha256,
    account_id: snapshot.account_id,
    brand_guardrails: snapshot.brand_guardrails,
    audience_context: snapshot.audience_context,
    channel_policy_ids: snapshot.channel_policy_ids,
    customer_signals: snapshot.customer_signals,
    expires_at: snapshot.expires_at,
  };
}

function marketingLearningApplicability({ account, packet, packetSha256, mode, marketingContext }) {
  return {
    schema_version: "trace.marketing-learning-applicability.v1",
    account_id: account.account_id,
    feature_id: packet.feature_id,
    feature_packet_sha256: packetSha256,
    country: account.country,
    language: account.language,
    mode,
    marketing_context_snapshot_sha256: marketingContext?.snapshot_sha256 ?? null,
  };
}

function learningApplicabilityFromLineages(accountId, lineages) {
  const [first] = lineages;
  const applicability = marketingLearningApplicability({
    account: {
      account_id: accountId,
      country: first.country,
      language: first.language,
    },
    packet: { feature_id: first.feature_id },
    packetSha256: first.feature_packet_sha256,
    mode: first.mode,
    marketingContext: first.marketing_context_snapshot_sha256
      ? { snapshot_sha256: first.marketing_context_snapshot_sha256 }
      : null,
  });
  if (!lineages.every((lineage) => canonicalJson(marketingLearningApplicability({
    account: {
      account_id: accountId,
      country: lineage.country,
      language: lineage.language,
    },
    packet: { feature_id: lineage.feature_id },
    packetSha256: lineage.feature_packet_sha256,
    mode: lineage.mode,
    marketingContext: lineage.marketing_context_snapshot_sha256
      ? { snapshot_sha256: lineage.marketing_context_snapshot_sha256 }
      : null,
  })) === canonicalJson(applicability))) {
    throw new MarketingAgentHttpError(
      409,
      "learning lineage의 구조화된 applicability가 일치하지 않습니다.",
    );
  }
  return applicability;
}

export async function rederiveLearningApplicability(db, accountId, evaluationIds) {
  if (!Array.isArray(evaluationIds) || evaluationIds.length < 2) {
    throw new MarketingAgentHttpError(409, "learning applicability requires replication lineages");
  }
  if (new Set(evaluationIds).size !== evaluationIds.length) {
    throw new MarketingAgentHttpError(409, "learning applicability lineages must be unique");
  }
  const placeholders = evaluationIds.map(() => "?").join(",");
  const result = await db.prepare(
    `SELECT evaluation.evaluation_id, campaign.campaign_id, campaign.mode,
            campaign.feature_packet_sha256, campaign.marketing_context_snapshot_sha256,
            packet.feature_id, account.country, account.language
     FROM hosted_marketing_experiment_evaluations AS evaluation
     JOIN hosted_marketing_campaigns AS campaign
       ON campaign.campaign_id = evaluation.campaign_id
     JOIN hosted_workspace_accounts AS account ON account.account_id = campaign.account_id
     JOIN hosted_marketing_feature_packets AS packet
       ON packet.packet_id = campaign.feature_packet_id
      AND packet.packet_sha256 = campaign.feature_packet_sha256
     WHERE campaign.account_id = ? AND evaluation.state = 'evaluated'
       AND evaluation.evaluation_id IN (${placeholders})`,
  ).bind(accountId, ...evaluationIds).all();
  const byId = new Map(result.results.map((row) => [row.evaluation_id, row]));
  if (byId.size !== evaluationIds.length) {
    throw new MarketingAgentHttpError(409, "learning applicability lineages are stale");
  }
  const ordered = evaluationIds.map((evaluationId) => byId.get(evaluationId));
  if (new Set(ordered.map((lineage) => lineage.campaign_id)).size !== ordered.length) {
    throw new MarketingAgentHttpError(409, "learning applicability requires independent campaigns");
  }
  return learningApplicabilityFromLineages(accountId, ordered);
}

function isMarketingLearningApplicability(value) {
  return value
    && typeof value === "object"
    && !Array.isArray(value)
    && value.schema_version === "trace.marketing-learning-applicability.v1"
    && typeof value.account_id === "string"
    && typeof value.feature_id === "string"
    && typeof value.feature_packet_sha256 === "string"
    && /^[a-f0-9]{64}$/.test(value.feature_packet_sha256)
    && /^[A-Z]{2}$/.test(value.country)
    && /^[a-z]{2,3}$/.test(value.language)
    && ["shadow", "assisted"].includes(value.mode)
    && (value.marketing_context_snapshot_sha256 === null
      || (typeof value.marketing_context_snapshot_sha256 === "string"
        && /^[a-f0-9]{64}$/.test(value.marketing_context_snapshot_sha256)));
}

async function loadCanonicalPrinciples(db, applicability) {
  const result = await db.prepare(
    `SELECT principle.principle_id, principle.principle_json
     FROM hosted_marketing_principles AS principle
     JOIN hosted_marketing_learning_candidates AS learning
       ON learning.learning_id = principle.learning_id
     JOIN hosted_marketing_campaigns AS campaign ON campaign.campaign_id = learning.campaign_id
     WHERE campaign.account_id = ? AND principle.state IN ('provisional', 'durable')
       AND json_extract(principle.principle_json, '$.applicability.schema_version') = ?
       AND json_extract(principle.principle_json, '$.applicability.account_id') = ?
       AND json_extract(principle.principle_json, '$.applicability.feature_id') = ?
       AND json_extract(principle.principle_json, '$.applicability.feature_packet_sha256') = ?
       AND json_extract(principle.principle_json, '$.applicability.country') = ?
       AND json_extract(principle.principle_json, '$.applicability.language') = ?
       AND json_extract(principle.principle_json, '$.applicability.mode') = ?
       AND json_extract(principle.principle_json, '$.applicability.marketing_context_snapshot_sha256') IS ?
     ORDER BY principle.principle_id
     LIMIT 100`,
  ).bind(
    applicability.account_id,
    applicability.schema_version,
    applicability.account_id,
    applicability.feature_id,
    applicability.feature_packet_sha256,
    applicability.country,
    applicability.language,
    applicability.mode,
    applicability.marketing_context_snapshot_sha256,
  ).all();
  const learned = [];
  for (const row of result.results) {
    try {
      const principle = JSON.parse(row.principle_json);
      const statement = principle?.statement;
      if (
        isMarketingLearningApplicability(principle?.applicability)
        && canonicalJson(principle.applicability) === canonicalJson(applicability)
        && typeof statement === "string"
        && statement.trim()
        && statement.length <= 2000
      ) {
        learned.push(statement.trim());
      }
    } catch {
      throw new MarketingAgentHttpError(409, "승인된 marketing principle이 손상되었습니다.");
    }
  }
  return [...new Set([...SHADOW_PRINCIPLES, ...learned])];
}

async function campaignStatus(env, accountId, campaignId) {
  const row = await env.DB.prepare(
    `SELECT campaign.campaign_id, campaign.feature_packet_id, campaign.feature_packet_sha256,
            campaign.mode, campaign.state, campaign.projection_revision,
            campaign.origin_campaign_id,
            campaign.business_outcome, campaign.marketing_context_snapshot_id,
            campaign.marketing_context_snapshot_sha256, campaign.created_at, campaign.updated_at,
            packet.publication_allowed,
            research_task.task_id AS research_task_id,
            research_task.state AS research_task_state,
            research_task.result_json AS research_task_result_json,
            reference.snapshot_id AS reference_snapshot_id,
            reference.snapshot_sha256 AS reference_snapshot_sha256,
            task.task_id, task.state AS task_state, task.result_json,
            creative_task.task_id AS creative_task_id,
            creative_task.state AS creative_task_state,
            creative_task.result_json AS creative_task_result_json,
            reassessment_task.task_id AS reassessment_task_id,
            reassessment_task.state AS reassessment_task_state,
            reassessment_task.result_json AS reassessment_task_result_json,
            reassessment.reassessment_id, reassessment.situation AS reassessment_situation,
            reassessment.reassessment_sha256, reassessment.reassessment_json,
            reassessment.state AS reassessment_state,
            brief.brief_id, brief.brief_json, brief.brief_sha256,
            plan.plan_id, plan.plan_json, plan.plan_sha256, plan.state AS plan_state
     FROM hosted_marketing_campaigns AS campaign
     JOIN hosted_marketing_feature_packets AS packet
       ON packet.packet_id = campaign.feature_packet_id
      AND packet.packet_sha256 = campaign.feature_packet_sha256
     LEFT JOIN hosted_workspace_capture_tasks AS task
       ON task.account_id = campaign.account_id AND task.run_id = campaign.campaign_id
      AND task.kind = 'marketing_judgment'
      AND json_extract(task.task_json, '$.payload.judgment') = 'shadow_strategy'
     LEFT JOIN hosted_workspace_capture_tasks AS research_task
       ON research_task.account_id = campaign.account_id
      AND research_task.kind = 'marketing_judgment'
      AND json_extract(research_task.task_json, '$.payload.campaign_id') = campaign.campaign_id
      AND json_extract(research_task.task_json, '$.payload.judgment') = 'market_research'
     LEFT JOIN hosted_marketing_reference_snapshots AS reference
       ON reference.campaign_id = campaign.campaign_id
     LEFT JOIN hosted_workspace_capture_tasks AS creative_task
       ON creative_task.account_id = campaign.account_id
      AND creative_task.kind = 'marketing_judgment'
      AND json_extract(creative_task.task_json, '$.payload.campaign_id') = campaign.campaign_id
      AND json_extract(creative_task.task_json, '$.payload.judgment') = 'creative_plan'
     LEFT JOIN hosted_workspace_capture_tasks AS reassessment_task
       ON reassessment_task.account_id = campaign.account_id
      AND reassessment_task.kind = 'marketing_judgment'
      AND json_extract(reassessment_task.task_json, '$.payload.campaign_id') = campaign.campaign_id
      AND json_extract(reassessment_task.task_json, '$.payload.judgment') = 'outcome_reassessment'
     LEFT JOIN hosted_marketing_outcome_reassessments AS reassessment
       ON reassessment.campaign_id = campaign.campaign_id
     LEFT JOIN hosted_marketing_strategy_briefs AS brief
       ON brief.campaign_id = campaign.campaign_id
     LEFT JOIN hosted_marketing_media_plans AS plan
       ON plan.campaign_id = campaign.campaign_id
     WHERE campaign.account_id = ? AND campaign.campaign_id = ?`,
  ).bind(accountId, campaignId).first();
  if (!row) throw new MarketingAgentHttpError(404, "마케팅 캠페인을 찾을 수 없습니다.");
  return {
    campaign_id: row.campaign_id,
    feature_packet_id: row.feature_packet_id,
    feature_packet_sha256: row.feature_packet_sha256,
    mode: row.mode,
    origin_campaign_id: row.origin_campaign_id,
    state: row.state,
    projection_revision: row.projection_revision,
    business_outcome: row.business_outcome,
    marketing_context_snapshot: row.marketing_context_snapshot_id ? {
      snapshot_id: row.marketing_context_snapshot_id,
      sha256: row.marketing_context_snapshot_sha256,
    } : null,
    research_task: row.research_task_id ? {
      task_id: row.research_task_id,
      state: row.research_task_state,
      result: row.research_task_result_json ? JSON.parse(row.research_task_result_json) : null,
    } : null,
    reference_snapshot: row.reference_snapshot_id ? {
      snapshot_id: row.reference_snapshot_id,
      sha256: row.reference_snapshot_sha256,
    } : null,
    task: row.task_id ? {
      task_id: row.task_id,
      state: row.task_state,
      result: row.result_json ? JSON.parse(row.result_json) : null,
    } : null,
    creative_task: row.creative_task_id ? {
      task_id: row.creative_task_id,
      state: row.creative_task_state,
      result: row.creative_task_result_json ? JSON.parse(row.creative_task_result_json) : null,
    } : null,
    outcome_reassessment_task: row.reassessment_task_id ? {
      task_id: row.reassessment_task_id,
      state: row.reassessment_task_state,
      result: row.reassessment_task_result_json
        ? JSON.parse(row.reassessment_task_result_json)
        : null,
    } : null,
    outcome_reassessment: row.reassessment_id ? {
      reassessment_id: row.reassessment_id,
      situation: row.reassessment_situation,
      sha256: row.reassessment_sha256,
      state: row.reassessment_state,
      value: JSON.parse(row.reassessment_json),
    } : null,
    strategy_brief: row.brief_id ? {
      brief_id: row.brief_id,
      sha256: row.brief_sha256,
      value: JSON.parse(row.brief_json),
    } : null,
    media_plan: row.plan_id ? {
      plan_id: row.plan_id,
      sha256: row.plan_sha256,
      state: row.plan_state,
      value: JSON.parse(row.plan_json),
    } : null,
    publication_allowed: Number(row.publication_allowed) === 1,
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
  const evidenceById = new Map(evidence.map((item) => [item.evidence_id, item]));
  for (const claim of claims.filter((item) => allowedClaimIds.includes(item.claim_id))) {
    const hasInstalledProof = claim.evidence_ids.some((id) => {
      const item = evidenceById.get(id);
      return ["install_receipt", "runtime_observation"].includes(item?.kind)
        && ["pass", "observed"].includes(item?.result);
    });
    if (!hasInstalledProof) {
      throw new MarketingAgentHttpError(
        400,
        "publication claim에는 통과한 install receipt 또는 runtime observation이 필요합니다.",
      );
    }
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
  if (gate.publication_allowed && !["installed_confirmed", "released"].includes(lifecycle)) {
    throw new MarketingAgentHttpError(
      400,
      "publication에는 installed_confirmed 또는 released lifecycle이 필요합니다.",
    );
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

function normalizeProductTruthReview(value, packet) {
  const review = requireObject(value, "product_truth_review");
  if (review.decision !== "approved") {
    throw new MarketingAgentHttpError(400, "product truth는 명시적으로 approved여야 합니다.");
  }
  const approvedClaimIds = requireArray(
    review.approved_claim_ids,
    "approved_claim_ids",
    1,
    64,
  ).map((id) => safeId(id, "claim_id"));
  const expected = [...packet.gate.allowed_claim_ids].sort();
  if (
    new Set(approvedClaimIds).size !== approvedClaimIds.length
    || approvedClaimIds.slice().sort().join("\u0000") !== expected.join("\u0000")
  ) {
    throw new MarketingAgentHttpError(
      400,
      "product truth 승인은 publication gate의 claim 집합과 정확히 일치해야 합니다.",
    );
  }
  return {
    reviewer_id: safeId(review.reviewer_id, "reviewer_id"),
    approved_claim_ids: approvedClaimIds,
    reviewed_at: isoTimestamp(review.reviewed_at, "reviewed_at"),
  };
}

function normalizeArtifactManifest(value) {
  const manifest = requireObject(value, "artifact_manifest");
  if (manifest.schema_version !== "trace.artifact-manifest.v1") {
    throw new MarketingAgentHttpError(400, "artifact manifest schema가 올바르지 않습니다.");
  }
  const artifactUri = requiredString(manifest.artifact_uri, "artifact_uri", 2000);
  let protocol;
  try {
    protocol = new URL(artifactUri).protocol;
  } catch {
    throw new MarketingAgentHttpError(400, "artifact_uri가 올바르지 않습니다.");
  }
  if (!["https:", "r2:", "artifact:"].includes(protocol)) {
    throw new MarketingAgentHttpError(400, "artifact_uri protocol을 허용할 수 없습니다.");
  }
  return {
    schema_version: "trace.artifact-manifest.v1",
    manifest_id: safeId(manifest.manifest_id, "manifest_id"),
    campaign_id: safeId(manifest.campaign_id, "campaign_id"),
    assignment_id: safeId(manifest.assignment_id, "assignment_id"),
    treatment_id: safeId(manifest.treatment_id, "treatment_id"),
    request_id: safeId(manifest.request_id, "request_id"),
    capability_id: safeId(manifest.capability_id, "capability_id"),
    capability_binding_sha256: sha256Digest(
      manifest.capability_binding_sha256,
      "capability_binding_sha256",
    ),
    artifact_uri: artifactUri,
    artifact_sha256: sha256Digest(manifest.artifact_sha256, "artifact_sha256"),
    input_sha256: sha256Digest(manifest.input_sha256, "input_sha256"),
    execution_id: manifest.execution_id == null
      ? null
      : safeId(manifest.execution_id, "execution_id"),
    claim_ids: requireArray(manifest.claim_ids ?? [], "claim_ids", 0, 16)
      .map((id) => safeId(id, "claim_id")),
    evidence_ids: requireArray(manifest.evidence_ids ?? [], "evidence_ids", 0, 32)
      .map((id) => safeId(id, "evidence_id")),
    created_at: isoTimestamp(manifest.created_at, "created_at"),
  };
}

function requireObject(value, field) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new MarketingAgentHttpError(400, `${field} 형식이 올바르지 않습니다.`);
  }
  return value;
}

function assertExactKeys(value, allowed, field) {
  const object = requireObject(value, field);
  const unexpected = Object.keys(object).filter((key) => !allowed.includes(key));
  if (unexpected.length) {
    throw new MarketingAgentHttpError(400, `${field}에 허용되지 않은 field가 있습니다.`);
  }
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

function normalizedStringList(value, field, minimum, maximum, itemMaximum) {
  const items = requireArray(value, field, minimum, maximum)
    .map((item) => requiredString(item, field, itemMaximum));
  if (new Set(items).size !== items.length) {
    throw new MarketingAgentHttpError(400, `${field}는 중복될 수 없습니다.`);
  }
  return items;
}

function basisPoints(value, field) {
  if (!Number.isInteger(value) || value < 0 || value > 10_000) {
    throw new MarketingAgentHttpError(400, `${field}는 0부터 10000 사이의 정수여야 합니다.`);
  }
  return value;
}

function positiveInteger(value, field) {
  if (!Number.isInteger(value) || value < 0) {
    throw new MarketingAgentHttpError(400, `${field}는 0 이상의 정수여야 합니다.`);
  }
  return value;
}

function decodedRouteId(value) {
  try {
    return safeId(decodeURIComponent(value), "campaign_id");
  } catch (error) {
    if (error instanceof MarketingAgentHttpError) throw error;
    throw new MarketingAgentHttpError(400, "campaign_id encoding이 올바르지 않습니다.");
  }
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

async function sha256Text(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

class MarketingAgentHttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function agentJson(payload, status = 200) {
  return Response.json(payload, {
    status,
    headers: { "cache-control": "no-store" },
  });
}

function requireMarketingAuthority(request, env) {
  if (
    !env.CONTROL_PLANE_TOKEN
    || request.headers.get("authorization") !== `Bearer ${env.CONTROL_PLANE_TOKEN}`
  ) {
    throw new MarketingAgentHttpError(401, "control plane authorization required");
  }
}

function requireEventIngestAuthority(request, env) {
  if (
    !env.TRACE_EVENT_INGEST_TOKEN
    || request.headers.get("authorization") !== `Bearer ${env.TRACE_EVENT_INGEST_TOKEN}`
  ) {
    throw new MarketingAgentHttpError(401, "product event ingest authorization required");
  }
}

async function readJson(request) {
  const body = await request.text();
  if (new TextEncoder().encode(body).byteLength > MAX_AGENT_REQUEST_BYTES) {
    throw new MarketingAgentHttpError(
      413,
      `marketing agent request가 ${MAX_AGENT_REQUEST_BYTES} bytes를 초과합니다.`,
    );
  }
  try {
    return JSON.parse(body);
  } catch {
    throw new MarketingAgentHttpError(400, "JSON 요청 본문이 올바르지 않습니다.");
  }
}
