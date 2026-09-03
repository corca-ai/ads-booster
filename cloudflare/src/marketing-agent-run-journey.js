import { MarketingAgentRunError } from "./marketing-agent-runs.js";

const MAX_JOURNEY_DEPTH = 16;
const MAX_JOURNEY_NODES = 100;

export async function marketingAgentRunJourney(database, accountId, runId) {
  const run = await database.prepare(
    `SELECT run_id, account_id, campaign_id, state, created_at, updated_at
     FROM hosted_marketing_agent_runs WHERE account_id = ? AND run_id = ?`,
  ).bind(accountId, runId).first();
  if (!run) throw new MarketingAgentRunError(404, "marketing agent run을 찾을 수 없습니다.");

  const root = await journeyRoot(database, accountId, runId);
  if (!root) {
    const integrityState = run.state === "campaign_created" ? "root_missing" : "launch_pending";
    return journeyResponse(run, [], false, integrityState);
  }

  const graph = [root];
  const visited = new Set([root.campaign_id]);
  let frontier = [root.campaign_id];
  let depth = 0;
  let truncated = false;

  // Expand only the current frontier. This bounds database work independently of the tenant's
  // total campaign count: at most one limited query per depth plus one depth-limit probe.
  while (frontier.length > 0 && depth < MAX_JOURNEY_DEPTH) {
    const remaining = MAX_JOURNEY_NODES - graph.length;
    const edges = await journeyChildren(
      database,
      accountId,
      frontier,
      [...visited],
      remaining + 1,
    );
    const selectedEdges = edges.slice(0, remaining);
    truncated = edges.length > remaining;
    depth += 1;
    frontier = [];
    for (const edge of selectedEdges) {
      if (visited.has(edge.campaign_id)) continue;
      visited.add(edge.campaign_id);
      frontier.push(edge.campaign_id);
      graph.push({ ...edge, depth });
    }
    if (truncated) break;
  }

  if (!truncated && frontier.length > 0) {
    truncated = (await journeyChildren(
      database,
      accountId,
      frontier,
      [...visited],
      1,
    )).length > 0;
  }

  const nodes = await hydrateJourneyNodes(database, accountId, graph);
  return journeyResponse(run, nodes, truncated, "verified");
}

async function journeyRoot(database, accountId, runId) {
  return database.prepare(
    `SELECT campaign.campaign_id, NULL AS parent_campaign_id, 'launch_shadow' AS relation,
            0 AS depth, event.event_id AS causation_id,
            event.event_sha256 AS causation_sha256, campaign.created_at AS edge_time
     FROM hosted_marketing_agent_runs AS agent_run
     JOIN hosted_marketing_campaigns AS campaign
       ON campaign.campaign_id = agent_run.campaign_id
      AND campaign.account_id = agent_run.account_id
      AND campaign.agent_run_id = agent_run.run_id
      AND campaign.mode = 'shadow'
     JOIN hosted_marketing_run_events AS event
       ON event.campaign_id = campaign.campaign_id
      AND event.sequence = 1
      AND event.idempotency_key = 'campaign:' || campaign.campaign_id || ':create'
     WHERE agent_run.account_id = ? AND agent_run.run_id = ?`,
  ).bind(accountId, runId).first();
}

async function journeyChildren(database, accountId, parentIds, visitedIds, limit) {
  if (parentIds.length === 0 || limit < 1) return [];
  const parentJson = JSON.stringify(parentIds);
  // A back-edge can consume at most one row per already visited campaign in either relation.
  // Fetch that fixed allowance plus one truncation sentinel, then deduplicate in memory.
  const sourceLimit = limit + visitedIds.length;
  const [assisted, successors] = await Promise.all([
    database.prepare(
      `SELECT child.origin_campaign_id AS parent_campaign_id,
              child.campaign_id AS campaign_id,
              'assisted_execution' AS relation,
              event.event_id AS causation_id,
              event.event_sha256 AS causation_sha256,
              child.created_at AS edge_time
       FROM json_each(?) AS parent
       CROSS JOIN hosted_marketing_campaigns AS child
         INDEXED BY hosted_marketing_campaigns_assisted_origin
       JOIN hosted_marketing_run_events AS event
         ON event.campaign_id = child.campaign_id
        AND event.sequence = 1
        AND event.idempotency_key = 'campaign:' || child.campaign_id || ':create'
       WHERE child.account_id = ? AND child.mode = 'assisted'
         AND child.origin_campaign_id = parent.value
       LIMIT ?`,
    ).bind(parentJson, accountId, sourceLimit).all(),
    database.prepare(
      `SELECT activation.source_campaign_id AS parent_campaign_id,
              activation.successor_campaign_id AS campaign_id,
              'outcome_successor' AS relation,
              activation.activation_id AS causation_id,
              activation.activation_sha256 AS causation_sha256,
              activation.updated_at AS edge_time
       FROM json_each(?) AS parent
       CROSS JOIN hosted_marketing_successor_activations AS activation
         INDEXED BY hosted_marketing_successor_activations_source_state
       JOIN hosted_marketing_campaigns AS successor
         ON successor.campaign_id = activation.successor_campaign_id
        AND successor.account_id = activation.account_id
        AND successor.mode = 'shadow'
       WHERE activation.account_id = ? AND activation.state = 'activated'
         AND activation.source_campaign_id = parent.value
       LIMIT ?`,
    ).bind(parentJson, accountId, sourceLimit).all(),
  ]);
  const visited = new Set(visitedIds);
  const unseen = new Map();
  for (const edge of [...(assisted.results ?? []), ...(successors.results ?? [])]) {
    if (!visited.has(edge.campaign_id) && !unseen.has(edge.campaign_id)) {
      unseen.set(edge.campaign_id, edge);
    }
  }
  return [...unseen.values()].sort(compareJourneyEdges).slice(0, limit);
}

async function hydrateJourneyNodes(database, accountId, graph) {
  if (graph.length === 0) return [];
  const campaignIds = graph.map((node) => node.campaign_id);
  const results = [];
  for (let offset = 0; offset < campaignIds.length; offset += 99) {
    const chunk = campaignIds.slice(offset, offset + 99);
    const result = await database.prepare(
    `SELECT campaign.campaign_id, campaign.mode, campaign.state,
            (SELECT evaluation.evaluation_id
             FROM hosted_marketing_experiment_evaluations AS evaluation
             WHERE evaluation.campaign_id = campaign.campaign_id
             ORDER BY evaluation.evaluated_at DESC, evaluation.evaluation_id DESC LIMIT 1)
              AS evaluation_id,
            (SELECT evaluation.state
             FROM hosted_marketing_experiment_evaluations AS evaluation
             WHERE evaluation.campaign_id = campaign.campaign_id
             ORDER BY evaluation.evaluated_at DESC, evaluation.evaluation_id DESC LIMIT 1)
              AS evaluation_state,
            (SELECT reassessment.reassessment_id
             FROM hosted_marketing_outcome_reassessments AS reassessment
             WHERE reassessment.campaign_id = campaign.campaign_id
             ORDER BY reassessment.created_at DESC, reassessment.reassessment_id DESC LIMIT 1)
              AS reassessment_id,
            (SELECT reassessment.state
             FROM hosted_marketing_outcome_reassessments AS reassessment
             WHERE reassessment.campaign_id = campaign.campaign_id
             ORDER BY reassessment.created_at DESC, reassessment.reassessment_id DESC LIMIT 1)
              AS reassessment_state,
            (SELECT request.request_id
             FROM hosted_marketing_next_experiment_requests AS request
             WHERE request.source_campaign_id = campaign.campaign_id
             ORDER BY request.created_at DESC, request.request_id DESC LIMIT 1)
              AS next_experiment_request_id,
            (SELECT request.state
             FROM hosted_marketing_next_experiment_requests AS request
             WHERE request.source_campaign_id = campaign.campaign_id
             ORDER BY request.created_at DESC, request.request_id DESC LIMIT 1)
              AS next_experiment_state,
            (SELECT activation.activation_id
             FROM hosted_marketing_successor_activations AS activation
             WHERE activation.source_campaign_id = campaign.campaign_id
             ORDER BY activation.created_at DESC, activation.activation_id DESC LIMIT 1)
              AS successor_activation_id,
            (SELECT activation.state
             FROM hosted_marketing_successor_activations AS activation
             WHERE activation.source_campaign_id = campaign.campaign_id
             ORDER BY activation.created_at DESC, activation.activation_id DESC LIMIT 1)
              AS successor_activation_state,
            (SELECT learning.learning_id
             FROM hosted_marketing_learning_candidates AS learning
             WHERE learning.campaign_id = campaign.campaign_id
             ORDER BY learning.created_at DESC, learning.learning_id DESC LIMIT 1)
              AS learning_id,
            (SELECT learning.state
             FROM hosted_marketing_learning_candidates AS learning
             WHERE learning.campaign_id = campaign.campaign_id
             ORDER BY learning.created_at DESC, learning.learning_id DESC LIMIT 1)
              AS learning_state
     FROM hosted_marketing_campaigns AS campaign
     WHERE campaign.account_id = ? AND campaign.campaign_id IN (${placeholders(chunk)})`,
    ).bind(accountId, ...chunk).all();
    results.push(...(result.results ?? []));
  }
  const hydrated = new Map(results.map((row) => [row.campaign_id, row]));
  return graph.flatMap((node) => {
    const campaign = hydrated.get(node.campaign_id);
    return campaign ? [publicJourneyNode({ ...node, ...campaign })] : [];
  });
}

function journeyResponse(run, nodes, truncated, integrityState) {
  const latestNodeEnteredAt = nodes.reduce(
    (latest, node) => node.entered_at > latest ? node.entered_at : latest,
    run.updated_at,
  );
  return {
    schema_version: "trace.marketing-agent-run-journey.v1",
    run_id: run.run_id,
    account_id: run.account_id,
    launch_state: run.state,
    integrity_state: integrityState,
    nodes,
    truncated,
    limits: {
      max_depth: MAX_JOURNEY_DEPTH,
      max_nodes: MAX_JOURNEY_NODES,
    },
    links: {
      run: `/api/marketing-agent/runs/${encodeURIComponent(run.run_id)}`,
      review_queue: "/api/marketing-agent/review-queue",
    },
    created_at: run.created_at,
    latest_node_entered_at: latestNodeEnteredAt,
  };
}

function publicJourneyNode(row) {
  const campaignId = row.campaign_id;
  return {
    campaign_id: campaignId,
    parent_campaign_id: row.parent_campaign_id ?? null,
    relation: row.relation,
    depth: Number(row.depth),
    mode: row.mode,
    state: row.state,
    causation: {
      id: row.causation_id,
      sha256: row.causation_sha256,
    },
    outcome: {
      evaluation_id: row.evaluation_id ?? null,
      evaluation_state: row.evaluation_state ?? null,
      reassessment_id: row.reassessment_id ?? null,
      reassessment_state: row.reassessment_state ?? null,
      next_experiment_request_id: row.next_experiment_request_id ?? null,
      next_experiment_state: row.next_experiment_state ?? null,
      successor_activation_id: row.successor_activation_id ?? null,
      successor_activation_state: row.successor_activation_state ?? null,
      learning_id: row.learning_id ?? null,
      learning_state: row.learning_state ?? null,
    },
    links: {
      campaign: `/api/marketing-agent/campaigns/${encodeURIComponent(campaignId)}`,
      review_queue: "/api/marketing-agent/review-queue",
    },
    entered_at: row.edge_time,
  };
}

function placeholders(values) {
  return values.map(() => "?").join(", ");
}

function compareJourneyEdges(left, right) {
  return left.edge_time.localeCompare(right.edge_time)
    || left.campaign_id.localeCompare(right.campaign_id)
    || left.parent_campaign_id.localeCompare(right.parent_campaign_id)
    || left.relation.localeCompare(right.relation);
}
