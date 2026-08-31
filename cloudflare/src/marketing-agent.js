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
const CREATIVE_PLANNING_CAPABILITIES = Object.freeze([
  "capture.native_png",
  "record.screen",
  "compose.explanation",
  "design.figma",
  "copy.text",
]);
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
      return agentJson(await createShadowCampaign(env, account, await readJson(request)), 202);
    }
    if (request.method === "GET" && url.pathname === "/api/marketing-agent/campaigns") {
      return agentJson({ campaigns: await listCampaigns(env, account.account_id) });
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
    const campaignRoute = url.pathname.match(/^\/api\/marketing-agent\/campaigns\/([^/]+)$/);
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
  const row = await env.DB.prepare(
    `SELECT campaign.campaign_id, campaign.account_id, campaign.feature_packet_id,
            campaign.feature_packet_sha256, campaign.mode, campaign.state,
            campaign.projection_revision, packet.packet_json, brief.brief_id,
            brief.brief_json, brief.brief_sha256
     FROM hosted_marketing_campaigns AS campaign
     JOIN hosted_marketing_feature_packets AS packet
       ON packet.packet_id = campaign.feature_packet_id
      AND packet.packet_sha256 = campaign.feature_packet_sha256
     JOIN hosted_marketing_strategy_briefs AS brief ON brief.campaign_id = campaign.campaign_id
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
    const knowledgeSnapshotSha256 = await canonicalSha256({ principles: SHADOW_PRINCIPLES });
    const capabilitySnapshotSha256 = await canonicalSha256({
      capabilities: CREATIVE_PLANNING_CAPABILITIES,
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
        canonical_principles: [...SHADOW_PRINCIPLES],
        knowledge_snapshot_sha256: knowledgeSnapshotSha256,
        available_capabilities: [...CREATIVE_PLANNING_CAPABILITIES],
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
  if (results[1]?.meta?.changes !== 1 || results[2]?.meta?.changes !== 1) {
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
            experiment.experiment_id, plan.plan_id, plan.plan_sha256, plan.state AS plan_state,
            plan.publication_allowed, treatment.hypothesis_id AS treatment_hypothesis_id,
            candidate.revision, candidate.status, candidate.marketing_assignment_id,
            existing.hypothesis_id AS existing_hypothesis_id,
            existing.treatment_id AS existing_treatment_id,
            existing.eligible_block_id AS existing_block_id
     FROM hosted_marketing_campaigns AS campaign
     JOIN hosted_marketing_experiments AS experiment ON experiment.campaign_id = campaign.campaign_id
     JOIN hosted_marketing_media_plans AS plan ON plan.campaign_id = campaign.campaign_id
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
       )`,
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
  const assignment = {
    assignment_id: assignmentId,
    campaign_id: campaignId,
    experiment_id: row.experiment_id,
    hypothesis_id: hypothesisId,
    treatment_id: treatmentId,
    candidate_id: candidateId,
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
         candidate_id, eligible_block_id, assignment_json, assignment_sha256, assigned_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      assignmentId,
      campaignId,
      row.experiment_id,
      hypothesisId,
      treatmentId,
      candidateId,
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
            brief.brief_id, brief.brief_json, brief.brief_sha256,
            plan.plan_id, plan.plan_json, plan.plan_sha256, plan.state AS plan_state
     FROM hosted_marketing_campaigns AS campaign
     LEFT JOIN hosted_workspace_capture_tasks AS task
       ON task.account_id = campaign.account_id AND task.run_id = campaign.campaign_id
      AND task.kind = 'marketing_judgment'
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
    media_plan: row.plan_id ? {
      plan_id: row.plan_id,
      sha256: row.plan_sha256,
      state: row.plan_state,
      value: JSON.parse(row.plan_json),
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
