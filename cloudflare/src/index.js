import { DurableObject, WorkflowEntrypoint } from "cloudflare:workers";

import { handleHostedWorkspace, runHostedWorkspaceSchedules } from "./hosted-workspace.js";
import { receiveHostedCreativePlanCallback } from "./hosted-creative-plan-callback.js";
import { receiveHostedCandidateMaterializationCallback } from "./hosted-candidate-materialization-callback.js";
import { receiveHostedExperimentEvaluationCallback } from "./hosted-experiment-evaluation-callback.js";
import { receiveHostedLearningSynthesisCallback } from "./hosted-learning-synthesis-callback.js";
import { receiveHostedNextExperimentCallback } from "./hosted-next-experiment-callback.js";
import { receiveHostedOutcomeReassessmentCallback } from "./hosted-outcome-reassessment-callback.js";
import { receiveHostedReferenceResearchCallback } from "./hosted-reference-research-callback.js";
import { receiveHostedGenerationCallback } from "./hosted-generation-callback.js";
import { receiveHostedMarketingJudgmentCallback } from "./hosted-marketing-judgment-callback.js";
import { HttpError } from "./http-error.js";
import { MarketingCapabilityError } from "./marketing-adapter-capabilities.js";
import {
  prepareMarketingCaptureManifests,
  recordMarketingCaptureManifests,
} from "./hosted-capture-manifests.js";
import {
  MAX_HOSTED_CAPTURE_CALLBACK_BYTES,
  prepareHostedCaptureResult,
} from "./hosted-capture-result.js";
import {
  WORKSPACE_CONTEXT,
  WORKSPACE_CONTEXT_PROFILES,
} from "./generated-workspace-context.js";
import {
  assertHostedCallbackTransport,
  handleMacWorkerRequest,
  reserveWorkerTaskCallback,
} from "./mac-workers.js";
import { deploymentHealth } from "./deployment-health.js";
import { runHostedThreadsEngagement } from "./threads/engagement.js";
import { dispatchHostedThreadsPublication } from "./threads/publication.js";
import { runHostedThreadsPublications } from "./threads/scheduling.js";
import { threadsConfigurationState } from "./threads/config.js";
import { runDueMarketingEvaluations } from "./marketing-agent.js";
import { runDueNextExperimentRequests } from "./marketing-next-experiment.js";
import { runDueSuccessorActivations } from "./marketing-successor-activation.js";

import {
  accountName,
  approvalPhase,
  assertRunnableAdapterMode,
  assertTransition,
  hostedSimulationOutput,
  normalizeCandidateIds,
  observationSchedule,
  selectedCandidateIds,
  taskExecutionBoundary,
  taskCompletionEventType,
} from "./state-machine.js";

const TERMINAL_TASK_FAILURES = new Set(["failed", "unknown_side_effect"]);
const MAX_INSTRUCTION_BYTES = 32 * 1024;
const MAX_CALLBACK_RESULT_BYTES = 96 * 1024;

export class MarketingAccountAgent extends DurableObject {
  async contextSnapshot(account, sharedInstruction) {
    const memories = (await this.ctx.storage.get("memories")) ?? [];
    return {
      account,
      shared_instruction: sharedInstruction,
      private_memory: memories.slice(-50),
    };
  }

  async commitMemory(entry) {
    const memories = (await this.ctx.storage.get("memories")) ?? [];
    const next = [...memories, entry].slice(-200);
    await this.ctx.storage.put("memories", next);
    return { account_id: this.ctx.id.name, memory_count: next.length };
  }
}

export class MarketingWorkflow extends WorkflowEntrypoint {
  async run(event, step) {
    const { run_id: runId, account_id: accountId } = event.payload;
    const account = await step.do("load-account", async () => loadAccount(this.env.DB, accountId));
    const instruction = await step.do("load-shared-instruction", async () =>
      loadInstruction(this.env.DB, account.instruction_revision),
    );
    const snapshot = await step.do("snapshot-account-context", async () => {
      const agent = this.env.ACCOUNT_AGENT.getByName(accountId);
      const value = await agent.contextSnapshot(account, instruction);
      const canonical = JSON.stringify(value);
      const digest = await sha256(canonical);
      await this.env.ARTIFACTS.put(`runs/${runId}/context.json`, canonical, {
        customMetadata: { sha256: digest, account_id: accountId },
      });
      await transition(this.env.DB, runId, "scheduled", "context_snapshot", {
        context_digest: digest,
      });
      return { value, digest };
    });

    const research = await this.runTask(step, runId, account, "research", {
      context_digest: snapshot.digest,
      country: account.country,
    });
    await step.do("mark-research", () =>
      transition(this.env.DB, runId, "context_snapshot", "research", research),
    );
    await step.do("mark-planning", () =>
      transition(this.env.DB, runId, "research", "planning", {}),
    );

    const candidates = await this.runTask(step, runId, account, "generate_candidates", {
      context_digest: snapshot.digest,
      workspace_id: account.workspace_id,
      shared_instruction: snapshot.value.shared_instruction,
      private_memory: snapshot.value.private_memory,
      research,
    });
    await step.do("mark-candidate-generation", () =>
      transition(this.env.DB, runId, "planning", "candidate_generation", candidates),
    );
    await step.do("await-candidate-approval", () =>
      transition(this.env.DB, runId, "candidate_generation", "awaiting_candidate_approval", {}),
    );
    let candidateApproval;
    try {
      candidateApproval = await step.waitForEvent("human candidate approval", {
        type: "candidate_approval",
        timeout: "7 days",
      });
    } catch (error) {
      await step.do("record-candidate-approval-timeout", () =>
        transition(this.env.DB, runId, "awaiting_candidate_approval", "failed", {
          failure_code: "candidate_approval_timeout",
        }),
      );
      throw error;
    }
    if (candidateApproval.payload?.decision !== "approved") {
      await step.do("record-candidate-rejection", () =>
        transition(
          this.env.DB,
          runId,
          "awaiting_candidate_approval",
          "rejected",
          { ...candidateApproval.payload, phase: "candidates" },
        ),
      );
      return { run_id: runId, state: "rejected" };
    }
    let candidateIds;
    try {
      candidateIds = selectedCandidateIds(
        candidateApproval.payload?.candidate_ids,
        candidates.candidate_ids,
      );
    } catch (error) {
      await step.do("record-invalid-candidate-approval", () =>
        transition(this.env.DB, runId, "awaiting_candidate_approval", "failed", {
          failure_code: "candidate_approval_invalid",
        }),
      );
      throw error;
    }
    await step.do("record-candidate-approval", () =>
      transition(this.env.DB, runId, "awaiting_candidate_approval", "candidates_approved", {
        phase: "candidates",
        candidate_ids: candidateIds,
      }),
    );
    await step.do("mark-capture-requested", () =>
      transition(this.env.DB, runId, "candidates_approved", "capture_requested", {
        candidate_ids: candidateIds,
      }),
    );

    const capture = await this.runTask(step, runId, account, "capture", {
      workspace_id: account.workspace_id,
      candidate_ids: candidateIds,
      candidates,
    });
    await step.do("mark-capture-completed", () =>
      transition(this.env.DB, runId, "capture_requested", "capture_completed", capture),
    );
    await step.do("automatic-quality-check", () =>
      transition(this.env.DB, runId, "capture_completed", "automatic_quality_check", {
        result: "pass",
      }),
    );
    await step.do("await-human-approval", () =>
      transition(
        this.env.DB,
        runId,
        "automatic_quality_check",
        "awaiting_human_approval",
        {},
      ),
    );

    let approval;
    try {
      approval = await step.waitForEvent("human publication approval", {
        type: "human_approval",
        timeout: "7 days",
      });
    } catch (error) {
      await step.do("record-publication-approval-timeout", () =>
        transition(this.env.DB, runId, "awaiting_human_approval", "failed", {
          failure_code: "publication_approval_timeout",
        }),
      );
      throw error;
    }
    if (approval.payload?.decision !== "approved") {
      await step.do("record-rejection", () =>
        transition(this.env.DB, runId, "awaiting_human_approval", "rejected", approval.payload ?? {}),
      );
      return { run_id: runId, state: "rejected" };
    }
    await step.do("record-approval", () =>
      transition(this.env.DB, runId, "awaiting_human_approval", "approved", approval.payload),
    );
    await step.do("schedule-publication", () =>
      transition(this.env.DB, runId, "approved", "scheduled_for_publish", {}),
    );
    await step.do("mark-publishing", () =>
      transition(this.env.DB, runId, "scheduled_for_publish", "publishing", {}),
    );

    const publication = await this.runTask(step, runId, account, "publish", {
      workspace_id: account.workspace_id,
      candidate_ids: candidateIds,
      candidates,
      capture,
      adapter_mode: account.adapter_mode,
    });
    await step.do("record-publication", () =>
      transition(this.env.DB, runId, "publishing", "published", publication),
    );
    await step.do("begin-observation", () =>
      transition(this.env.DB, runId, "published", "observing", {}),
    );

    const samples = [];
    const schedule = observationSchedule(this.env.OBSERVATION_MINUTES);
    for (const { minute, delay_minutes: delayMinutes } of schedule) {
      await step.sleep(`wait-${minute}-minute-sample`, `${delayMinutes} minutes`);
      samples.push(
        await this.runTask(step, runId, account, "sample_metrics", {
          publication,
          minute,
        }),
      );
    }
    const evaluation = await step.do("evaluate-observations", () => evaluate(samples));
    await step.do("record-evaluation", () =>
      transition(this.env.DB, runId, "observing", "evaluated", { evaluation }),
    );
    await step.do("commit-private-memory", async () => {
      const agent = this.env.ACCOUNT_AGENT.getByName(accountId);
      await agent.commitMemory({ run_id: runId, evaluation, created_at: new Date().toISOString() });
      await transition(this.env.DB, runId, "evaluated", "memory_committed", {});
    });
    await step.do("complete-run", () =>
      transition(this.env.DB, runId, "memory_committed", "completed", {}),
    );
    return { run_id: runId, state: "completed", evaluation };
  }

  async runTask(step, runId, account, kind, payload) {
    const task = await step.do(`dispatch-${kind}`, async () => {
      const idempotencyKey = `${runId}:${kind}:${payload.minute ?? "once"}`;
      const existing = await this.env.DB.prepare(
        "SELECT task_id, created_at FROM marketing_tasks WHERE idempotency_key = ?",
      )
        .bind(idempotencyKey)
        .first();
      const taskId = existing?.task_id ?? crypto.randomUUID();
      const createdAt = existing?.created_at ?? new Date().toISOString();
      const body = {
        schema_version: "1",
        task_id: taskId,
        run_id: runId,
        account_id: account.account_id,
        kind,
        idempotency_key: idempotencyKey,
        payload,
        created_at: createdAt,
        credential_ref: account.credential_ref,
      };
      const inserted = await this.env.DB.prepare(
        `INSERT INTO marketing_tasks
          (task_id, run_id, account_id, kind, idempotency_key, state, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
         ON CONFLICT(idempotency_key) DO NOTHING`,
      )
        .bind(taskId, runId, account.account_id, kind, body.idempotency_key, createdAt, createdAt)
        .run();
      if (inserted.meta.changes === 0 && !existing) {
        throw new Error(`concurrent task dispatch for ${idempotencyKey}`);
      }
      if (taskExecutionBoundary(account) === "mac-bridge") {
        await this.env.TASK_QUEUE.send(JSON.stringify(body), { contentType: "text" });
      }
      return body;
    });
    if (taskExecutionBoundary(account) === "hosted-simulation") {
      return step.do(`complete-hosted-${kind}`, async () => {
        const output = await hostedSimulationOutput(task);
        const artifact = JSON.stringify({ simulation: true, task, output });
        const digest = await sha256(artifact);
        const key = `runs/${runId}/tasks/${task.task_id}.json`;
        await this.env.ARTIFACTS.put(key, artifact, {
          customMetadata: {
            sha256: digest,
            account_id: account.account_id,
            task_id: task.task_id,
          },
        });
        const result = {
          status: "succeeded",
          output,
          artifacts: [{ uri: `r2://ARTIFACTS/${key}`, sha256: digest }],
        };
        const updated = await this.env.DB.prepare(
          `UPDATE marketing_tasks
           SET state = 'succeeded', result_json = ?, callback_id = ?, updated_at = ?
           WHERE task_id = ? AND callback_id IS NULL`,
        )
          .bind(
            JSON.stringify(result),
            `${task.task_id}:hosted`,
            new Date().toISOString(),
            task.task_id,
          )
          .run();
        if (updated.meta.changes !== 1) {
          const existing = await this.env.DB.prepare(
            "SELECT state, result_json FROM marketing_tasks WHERE task_id = ?",
          )
            .bind(task.task_id)
            .first();
          if (existing?.state !== "succeeded" || !existing.result_json) {
            throw new Error(`hosted simulation task ${task.task_id} did not complete`);
          }
          return JSON.parse(existing.result_json).output;
        }
        return output;
      });
    }
    let completion;
    try {
      completion = await step.waitForEvent(`wait-${kind}-${task.task_id}`, {
        type: taskCompletionEventType(kind, task.task_id),
        timeout: "12 hours",
      });
    } catch (error) {
      await step.do(`record-${kind}-callback-timeout`, async () => {
        const current = await currentRunState(this.env.DB, runId);
        await transition(this.env.DB, runId, current, "failed", {
          failure_code: `${kind}_callback_timeout`,
          task_id: task.task_id,
        });
      });
      throw error;
    }
    if (completion.payload?.task_id !== task.task_id) {
      throw new Error(`workflow received a callback for the wrong ${kind} task`);
    }
    const result = completion.payload.result;
    if (TERMINAL_TASK_FAILURES.has(result?.status)) {
      await step.do(`record-${kind}-failure`, async () => {
        const current = await currentRunState(this.env.DB, runId);
        const target = result.status === "unknown_side_effect" ? "unknown_side_effect" : "failed";
        await transition(this.env.DB, runId, current, target, {
          failure_code: result.failure_code,
          task_id: task.task_id,
        });
      });
      throw new Error(`${kind} failed: ${result.failure_code}`);
    }
    return result?.output ?? {};
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      if (request.method === "GET" && url.pathname === "/health") {
        return Response.json(deploymentHealth(env));
      }
      const macWorkerResponse = await handleMacWorkerRequest(
        request,
        env,
        (callback, worker) => receiveCallback(env, callback, worker),
      );
      if (macWorkerResponse) return macWorkerResponse;
      const workspaceResponse = await handleHostedWorkspace(
        request,
        env,
        WORKSPACE_CONTEXT,
        WORKSPACE_CONTEXT_PROFILES,
      );
      if (workspaceResponse) return workspaceResponse;
      authorize(
        request,
        ["/v1/task-callbacks", "/v1/review-events"].includes(url.pathname)
          ? env.WORKER_CALLBACK_TOKEN
          : env.CONTROL_PLANE_TOKEN,
      );
      if (request.method === "POST" && url.pathname === "/v1/instructions") {
        return Response.json(await createInstruction(env.DB, await request.json()), { status: 201 });
      }
      if (request.method === "POST" && url.pathname === "/v1/accounts") {
        return Response.json(await upsertAccount(env.DB, await request.json()), { status: 200 });
      }
      if (request.method === "GET" && url.pathname === "/v1/accounts") {
        const result = await env.DB.prepare(
          "SELECT config_json FROM marketing_accounts ORDER BY account_id",
        ).all();
        return Response.json({ accounts: result.results.map((row) => JSON.parse(row.config_json)) });
      }
      const runStart = url.pathname.match(/^\/v1\/accounts\/([^/]+)\/runs$/);
      if (request.method === "POST" && runStart) {
        return Response.json(await startRun(env, decodeURIComponent(runStart[1])), { status: 202 });
      }
      const runRead = url.pathname.match(/^\/v1\/runs\/([^/]+)$/);
      if (request.method === "GET" && runRead) {
        return Response.json(await readRun(env.DB, decodeURIComponent(runRead[1])));
      }
      const approval = url.pathname.match(/^\/v1\/runs\/([^/]+)\/approval$/);
      if (request.method === "POST" && approval) {
        const runId = decodeURIComponent(approval[1]);
        const body = await request.json();
        if (!["approved", "rejected"].includes(body.decision)) {
          return Response.json({ error: "decision must be approved or rejected" }, { status: 400 });
        }
        const state = await currentRunState(env.DB, runId);
        let phase;
        try {
          phase = approvalPhase(state, body.phase);
        } catch (error) {
          throw new HttpError(409, error.message);
        }
        const payload = { ...body, phase };
        if (phase === "candidates" && body.decision === "approved") {
          try {
            payload.candidate_ids = normalizeCandidateIds(body.candidate_ids);
          } catch (error) {
            throw new HttpError(400, error.message);
          }
        }
        const instance = await env.MARKETING_WORKFLOW.get(runId);
        await instance.sendEvent({
          type: phase === "candidates" ? "candidate_approval" : "human_approval",
          payload,
        });
        return Response.json({ accepted: true, run_id: runId, phase }, { status: 202 });
      }
      if (request.method === "POST" && url.pathname === "/v1/task-callbacks") {
        return Response.json(await receiveCallback(env, await request.json()), { status: 202 });
      }
      if (request.method === "POST" && url.pathname === "/v1/review-events") {
        return Response.json(await receiveReviewEvent(env, await request.json()), { status: 202 });
      }
      return Response.json({ error: "not_found" }, { status: 404 });
    } catch (error) {
      const status = error instanceof HttpError ? error.status : 500;
      return Response.json({ error: error.message }, { status });
    }
  },

  async scheduled(_event, env, ctx) {
    const threadsTasks = threadsConfigurationState(env) === "ready"
      ? [
          runHostedThreadsPublications(env, dispatchHostedThreadsPublication),
          runHostedThreadsEngagement(env),
        ]
      : [];
    ctx.waitUntil(Promise.all([
      startDueRuns(env),
      runHostedWorkspaceSchedules(env, WORKSPACE_CONTEXT, WORKSPACE_CONTEXT_PROFILES),
      runDueMarketingEvaluations(env),
      runDueNextExperimentRequests(env),
      runDueSuccessorActivations(env),
      ...threadsTasks,
    ]));
  },
};

function authorize(request, token) {
  if (!token || request.headers.get("authorization") !== `Bearer ${token}`) {
    throw new HttpError(401, "unauthorized");
  }
}

async function createInstruction(db, input) {
  if (typeof input.body !== "string" || !input.body.trim()) {
    throw new HttpError(400, "instruction body is required");
  }
  if (new TextEncoder().encode(input.body).byteLength > MAX_INSTRUCTION_BYTES) {
    throw new HttpError(413, `instruction body exceeds ${MAX_INSTRUCTION_BYTES} bytes`);
  }
  const digest = await sha256(input.body);
  const now = new Date().toISOString();
  const existing = await db
    .prepare("SELECT revision FROM shared_instructions WHERE body_sha256 = ?")
    .bind(digest)
    .first();
  if (existing) {
    await db.batch([
      db.prepare("UPDATE shared_instructions SET active = 0 WHERE active = 1"),
      db.prepare("UPDATE shared_instructions SET active = 1 WHERE revision = ?").bind(existing.revision),
    ]);
    return db
      .prepare("SELECT revision, body_sha256, active FROM shared_instructions WHERE revision = ?")
      .bind(existing.revision)
      .first();
  }
  await db.batch([
    db.prepare("UPDATE shared_instructions SET active = 0 WHERE active = 1"),
    db
      .prepare(
        "INSERT INTO shared_instructions (body, body_sha256, active, created_at) VALUES (?, ?, 1, ?)",
      )
      .bind(input.body, digest, now),
  ]);
  return db.prepare("SELECT revision, body_sha256, active FROM shared_instructions WHERE body_sha256 = ?")
    .bind(digest)
    .first();
}

async function upsertAccount(db, input) {
  const accountId = accountName(input.account_id);
  const instructionRevision = Number(input.instruction_revision);
  const instruction = await db
    .prepare("SELECT revision FROM shared_instructions WHERE revision = ?")
    .bind(instructionRevision)
    .first();
  if (!instruction) throw new HttpError(400, "unknown instruction_revision");
  const config = {
    account_id: accountId,
    channel: input.channel ?? "threads",
    country: input.country ?? "KR",
    timezone: input.timezone ?? "Asia/Seoul",
    schedule_minutes: Number(input.schedule_minutes ?? 1440),
    instruction_revision: instructionRevision,
    workspace_id: input.workspace_id ?? null,
    credential_ref: input.credential_ref ?? null,
    adapter_mode: input.adapter_mode ?? "simulation",
    enabled: input.enabled !== false,
  };
  if (!Number.isInteger(config.schedule_minutes) || config.schedule_minutes < 1) {
    throw new HttpError(400, "schedule_minutes must be a positive integer");
  }
  if (
    config.workspace_id !== null &&
    (typeof config.workspace_id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(config.workspace_id))
  ) {
    throw new HttpError(400, "workspace_id must be a safe opaque identifier");
  }
  try {
    assertRunnableAdapterMode(config.adapter_mode);
  } catch (error) {
    throw new HttpError(config.adapter_mode === "live" ? 409 : 400, error.message);
  }
  const now = new Date().toISOString();
  await db
    .prepare(
      `INSERT INTO marketing_accounts
        (account_id, channel, country, timezone, schedule_minutes, instruction_revision,
         credential_ref, adapter_mode, enabled, next_run_at, config_json, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(account_id) DO UPDATE SET
         channel=excluded.channel, country=excluded.country, timezone=excluded.timezone,
         schedule_minutes=excluded.schedule_minutes,
         instruction_revision=excluded.instruction_revision,
         credential_ref=excluded.credential_ref, adapter_mode=excluded.adapter_mode,
         enabled=excluded.enabled, config_json=excluded.config_json, updated_at=excluded.updated_at`,
    )
    .bind(
      accountId,
      config.channel,
      config.country,
      config.timezone,
      config.schedule_minutes,
      config.instruction_revision,
      config.credential_ref,
      config.adapter_mode,
      Number(config.enabled),
      now,
      JSON.stringify(config),
      now,
      now,
    )
    .run();
  return config;
}

async function startRun(env, accountId) {
  accountName(accountId);
  const account = await loadAccount(env.DB, accountId);
  if (!account.enabled) throw new HttpError(409, "account is disabled");
  const active = await activeRun(env.DB, accountId);
  if (active) {
    throw new HttpError(409, `account already has active run ${active.run_id}`);
  }
  const runId = crypto.randomUUID();
  const now = new Date().toISOString();
  try {
    await env.DB.prepare(
      `INSERT INTO marketing_runs
        (run_id, account_id, workflow_instance_id, state, created_at, updated_at)
       VALUES (?, ?, ?, 'scheduled', ?, ?)`,
    )
      .bind(runId, accountId, runId, now, now)
      .run();
  } catch (error) {
    const concurrent = await activeRun(env.DB, accountId);
    if (concurrent) {
      throw new HttpError(409, `account already has active run ${concurrent.run_id}`);
    }
    throw error;
  }
  await recordEvent(env.DB, runId, "scheduled", { trigger: "api" });
  try {
    await env.MARKETING_WORKFLOW.create({
      id: runId,
      params: { run_id: runId, account_id: accountId },
    });
  } catch (error) {
    await transition(env.DB, runId, "scheduled", "failed", {
      failure_code: "workflow_create_failed",
    });
    throw error;
  }
  return { run_id: runId, state: "scheduled" };
}

async function startDueRuns(env) {
  const now = new Date();
  const due = await env.DB.prepare(
    "SELECT account_id, schedule_minutes FROM marketing_accounts WHERE enabled = 1 AND next_run_at <= ? LIMIT 50",
  )
    .bind(now.toISOString())
    .all();
  for (const row of due.results) {
    const next = new Date(now.getTime() + Number(row.schedule_minutes) * 60_000).toISOString();
    const claimed = await env.DB.prepare(
      "UPDATE marketing_accounts SET next_run_at = ?, updated_at = ? WHERE account_id = ? AND next_run_at <= ?",
    )
      .bind(next, now.toISOString(), row.account_id, now.toISOString())
      .run();
    if (claimed.meta.changes === 1) {
      try {
        await startRun(env, row.account_id);
      } catch (error) {
        if (!(error instanceof HttpError && error.status === 409)) throw error;
      }
    }
  }
}

export async function receiveCallback(env, callback, worker = null) {
  if (
    typeof callback.callback_id !== "string" ||
    callback.callback_id.length < 1 ||
    callback.callback_id.length > 256
  ) {
    throw new HttpError(400, "callback_id must be a non-empty identifier");
  }
  const resultJson = JSON.stringify(callback.result);
  const resultBytes = new TextEncoder().encode(resultJson).byteLength;
  if (resultBytes > MAX_HOSTED_CAPTURE_CALLBACK_BYTES) {
    throw new HttpError(413, `callback result exceeds ${MAX_HOSTED_CAPTURE_CALLBACK_BYTES} bytes`);
  }
  const hostedTask = await env.DB.prepare(
    `SELECT task_id, run_id, account_id, candidate_id, candidate_revision,
            state, callback_id, result_json, dispatch_mode, worker_id, lease_id,
            execution_started_at, callback_reservation_id, kind, persona_id, task_json,
            required_capability, created_at
     FROM hosted_workspace_capture_tasks WHERE task_id = ?`,
  )
    .bind(callback.task_id)
    .first();
  if (hostedTask?.kind === "generate_candidates") {
    return receiveHostedGenerationCallback(env, hostedTask, callback, worker);
  }
  if (hostedTask?.kind === "marketing_judgment") {
    let judgment = null;
    try {
      judgment = JSON.parse(hostedTask.task_json)?.payload?.judgment ?? null;
    } catch {
      // The strategy callback owns the stable malformed-payload error for this task kind.
    }
    if (judgment === "creative_plan") {
      return receiveHostedCreativePlanCallback(env, hostedTask, callback, worker);
    }
    if (judgment === "candidate_materialization") {
      return receiveHostedCandidateMaterializationCallback(env, hostedTask, callback, worker);
    }
    if (judgment === "experiment_evaluation") {
      return receiveHostedExperimentEvaluationCallback(env, hostedTask, callback, worker);
    }
    if (judgment === "learning_synthesis") {
      return receiveHostedLearningSynthesisCallback(env, hostedTask, callback, worker);
    }
    if (judgment === "outcome_reassessment") {
      return receiveHostedOutcomeReassessmentCallback(env, hostedTask, callback, worker);
    }
    if (judgment === "next_experiment") {
      return receiveHostedNextExperimentCallback(env, hostedTask, callback, worker);
    }
    if (judgment === "market_research") {
      return receiveHostedReferenceResearchCallback(env, hostedTask, callback, worker);
    }
    return receiveHostedMarketingJudgmentCallback(env, hostedTask, callback, worker);
  }
  if (hostedTask) return receiveHostedCaptureCallback(env, hostedTask, callback, worker);
  if (resultBytes > MAX_CALLBACK_RESULT_BYTES) {
    throw new HttpError(413, `callback result exceeds ${MAX_CALLBACK_RESULT_BYTES} bytes`);
  }
  const task = await env.DB.prepare(
    "SELECT run_id, account_id, kind, state, callback_id, result_json FROM marketing_tasks WHERE task_id = ?",
  )
    .bind(callback.task_id)
    .first();
  if (!task) throw new HttpError(404, "task not found");
  if (task.run_id !== callback.run_id || task.account_id !== callback.account_id || task.kind !== callback.kind) {
    throw new HttpError(409, "callback scope does not match task");
  }
  if (callback.callback_id !== `${callback.task_id}:completed`) {
    throw new HttpError(409, "callback_id does not match task");
  }
  if (task.callback_id) {
    if (task.callback_id !== callback.callback_id) throw new HttpError(409, "conflicting callback");
    if (task.result_json !== resultJson) throw new HttpError(409, "callback result changed");
    await sendTaskCompletion(env, task, callback);
    return { accepted: true, duplicate: true };
  }
  const status = callback.result?.status;
  if (!["succeeded", "failed", "unknown_side_effect"].includes(status)) {
    throw new HttpError(400, "invalid task result status");
  }
  const now = new Date().toISOString();
  const updated = await env.DB.prepare(
    "UPDATE marketing_tasks SET state = ?, result_json = ?, callback_id = ?, updated_at = ? WHERE task_id = ? AND callback_id IS NULL",
  )
    .bind(status, resultJson, callback.callback_id, now, callback.task_id)
    .run();
  if (updated.meta.changes !== 1) {
    const winner = await env.DB.prepare(
      "SELECT callback_id, result_json FROM marketing_tasks WHERE task_id = ?",
    )
      .bind(callback.task_id)
      .first();
    if (winner?.callback_id !== callback.callback_id) {
      throw new HttpError(409, "conflicting callback");
    }
    if (winner.result_json !== resultJson) throw new HttpError(409, "callback result changed");
    await sendTaskCompletion(env, task, callback);
    return { accepted: true, duplicate: true };
  }
  await sendTaskCompletion(env, task, callback);
  return { accepted: true, duplicate: false };
}

async function receiveHostedCaptureCallback(env, task, callback, worker = null) {
  assertHostedCallbackTransport(task, worker);
  if (
    task.run_id !== callback.run_id ||
    task.account_id !== callback.account_id ||
    callback.kind !== "capture"
  ) {
    throw new HttpError(409, "callback scope does not match hosted capture task");
  }
  if (callback.callback_id !== `${callback.task_id}:completed`) {
    throw new HttpError(409, "callback_id does not match hosted capture task");
  }
  let prepared;
  try {
    prepared = await prepareHostedCaptureResult(callback.result);
  } catch (error) {
    throw new HttpError(error.status ?? 400, error.message);
  }
  const { status, image, image_digest: imageDigest, stored_result: storedResult } = prepared;
  let publishedPayload = {};
  try {
    publishedPayload = JSON.parse(task.task_json)?.payload ?? {};
  } catch {
    throw new HttpError(409, "hosted capture task payload is invalid");
  }
  if (status === "succeeded" && publishedPayload.feedback_context_sha256 && (
    storedResult?.output?.feedback_application_sha256
      !== publishedPayload.feedback_context_sha256
  )) {
    throw new HttpError(409, "hosted capture feedback receipt does not match task");
  }
  let imageKey = null;
  if (status === "succeeded") {
    imageKey = `workspace/${task.account_id}/candidates/${task.candidate_id}/${task.task_id}.png`;
  }
  const storedResultJson = JSON.stringify(storedResult);
  if (task.callback_id) {
    if (task.callback_id !== callback.callback_id) throw new HttpError(409, "conflicting callback");
    if (task.result_json !== storedResultJson) throw new HttpError(409, "callback result changed");
    return { accepted: true, duplicate: true };
  }

  const now = new Date().toISOString();
  let marketingCaptureManifests = [];
  if (status === "succeeded") {
    try {
      marketingCaptureManifests = await prepareMarketingCaptureManifests(
        env,
        task,
        imageKey,
        imageDigest,
        storedResult.output,
      );
    } catch (error) {
      if (error instanceof MarketingCapabilityError) {
        throw new HttpError(409, error.message);
      }
      throw error;
    }
  }
  if (worker) {
    const reservation = await reserveWorkerTaskCallback(
      env.DB, worker, task, callback.callback_id, storedResultJson,
    );
    if (reservation.duplicate) return { accepted: true, duplicate: true };
  }

  if (status === "succeeded") {
    await env.ARTIFACTS.put(imageKey, image, {
      httpMetadata: { contentType: "image/png" },
      customMetadata: {
        sha256: imageDigest,
        account_id: task.account_id,
        candidate_id: task.candidate_id,
        task_id: task.task_id,
        source: storedResult.output.capture_source,
        artifact_role: storedResult.output.artifact_role,
        source_trace_artifact_sha256:
          storedResult.output.source_trace_artifact_sha256 ?? imageDigest,
      },
    });
    const applied = await env.DB.prepare(
      `UPDATE hosted_workspace_candidates
       SET status = 'image_awaiting_review', image_key = ?, image_sha256 = ?,
           capture_state = NULL, capture_error = NULL,
           capture_feedback_application_sha256 = ?,
           revision = revision + 1, updated_at = ?
       WHERE account_id = ? AND candidate_id = ? AND status = 'caption_approved'
         AND capture_state = 'queued' AND capture_task_id = ? AND revision = ?`,
    )
      .bind(
        imageKey,
        imageDigest,
        storedResult.output.feedback_application_sha256 ?? null,
        Date.now() / 1000,
        task.account_id,
        task.candidate_id,
        task.task_id,
        task.candidate_revision,
      )
      .run();
    if (applied.meta.changes !== 1) {
      const candidate = await env.DB.prepare(
        `SELECT image_key, image_sha256 FROM hosted_workspace_candidates
         WHERE account_id = ? AND candidate_id = ?`,
      )
        .bind(task.account_id, task.candidate_id)
        .first();
      if (candidate?.image_key !== imageKey || candidate?.image_sha256 !== imageDigest) {
        await env.ARTIFACTS.delete(imageKey);
      }
    }
    await recordMarketingCaptureManifests(env, marketingCaptureManifests);
  } else {
    const failureCode = typeof callback.result?.failure_code === "string"
      ? callback.result.failure_code.slice(0, 200)
      : "native_capture_failed";
    await env.DB.prepare(
      `UPDATE hosted_workspace_candidates
       SET capture_state = 'failed', capture_error = ?, revision = revision + 1, updated_at = ?
       WHERE account_id = ? AND candidate_id = ? AND status = 'caption_approved'
         AND capture_state = 'queued' AND capture_task_id = ? AND revision = ?`,
    )
      .bind(
        failureCode,
        Date.now() / 1000,
        task.account_id,
        task.candidate_id,
        task.task_id,
        task.candidate_revision,
      )
      .run();
  }
  const completion = worker
    ? env.DB.prepare(
      `UPDATE hosted_workspace_capture_tasks
       SET state = ?, result_json = ?, callback_id = ?, updated_at = ?
       WHERE task_id = ? AND callback_id IS NULL AND worker_id = ? AND lease_id = ?
         AND callback_reservation_id = ?`,
    ).bind(
      status, storedResultJson, callback.callback_id, now, task.task_id,
      worker.worker_id, task.lease_id, callback.callback_id,
    )
    : env.DB.prepare(
      `UPDATE hosted_workspace_capture_tasks
       SET state = ?, result_json = ?, callback_id = ?, updated_at = ?
       WHERE task_id = ? AND callback_id IS NULL`,
    ).bind(status, storedResultJson, callback.callback_id, now, task.task_id);
  const updated = await completion.run();
  if (updated.meta.changes !== 1) {
    const winner = await env.DB.prepare(
      "SELECT callback_id, result_json FROM hosted_workspace_capture_tasks WHERE task_id = ?",
    )
      .bind(task.task_id)
      .first();
    if (winner?.callback_id !== callback.callback_id || winner.result_json !== storedResultJson) {
      throw new HttpError(409, "conflicting hosted capture callback");
    }
    return { accepted: true, duplicate: true };
  }
  return { accepted: true, duplicate: false };
}

async function receiveReviewEvent(env, input) {
  if (
    typeof input.approval_id !== "string" ||
    input.approval_id.length < 1 ||
    input.approval_id.length > 320
  ) {
    throw new HttpError(400, "approval_id must be a non-empty identifier");
  }
  if (typeof input.run_id !== "string" || typeof input.account_id !== "string") {
    throw new HttpError(400, "review event scope is required");
  }
  if (!["approved", "rejected"].includes(input.decision)) {
    throw new HttpError(400, "decision must be approved or rejected");
  }
  if (!["candidates", "publication"].includes(input.phase)) {
    throw new HttpError(400, "review event phase is invalid");
  }
  if (input.approval_id !== `${input.run_id}:${input.phase}`) {
    throw new HttpError(409, "approval_id does not match review event scope");
  }
  const run = await env.DB.prepare(
    "SELECT account_id, state FROM marketing_runs WHERE run_id = ?",
  )
    .bind(input.run_id)
    .first();
  if (!run) throw new HttpError(404, "run not found");
  if (run.account_id !== input.account_id) {
    throw new HttpError(409, "review event account does not match run");
  }
  const candidateIds =
    input.phase === "candidates" && input.decision === "approved"
      ? normalizeCandidateIds(input.candidate_ids)
      : [];
  if (input.phase === "publication" && Array.isArray(input.candidate_ids) && input.candidate_ids.length) {
    throw new HttpError(400, "publication review does not accept candidate_ids");
  }
  const payload = {
    decision: input.decision,
    phase: input.phase,
    ...(candidateIds.length ? { candidate_ids: candidateIds } : {}),
    source: "workspace_bridge",
  };
  const bodyJson = JSON.stringify(payload);
  const existing = await env.DB.prepare(
    `SELECT body_json, delivered_at FROM marketing_review_event_receipts
     WHERE approval_id = ?`,
  )
    .bind(input.approval_id)
    .first();
  if (existing) {
    if (existing.body_json !== bodyJson) throw new HttpError(409, "review event changed");
    if (existing.delivered_at || reviewEventWasApplied(input.phase, run.state)) {
      if (!existing.delivered_at) {
        const now = new Date().toISOString();
        await env.DB.prepare(
          `UPDATE marketing_review_event_receipts SET delivered_at = ?, updated_at = ?
           WHERE approval_id = ?`,
        )
          .bind(now, now, input.approval_id)
          .run();
      }
      return { accepted: true, duplicate: true };
    }
  } else {
    try {
      approvalPhase(run.state, input.phase);
    } catch (error) {
      throw new HttpError(409, error.message);
    }
    const now = new Date().toISOString();
    await env.DB.prepare(
      `INSERT INTO marketing_review_event_receipts
        (approval_id, run_id, account_id, phase, body_json, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
    )
      .bind(input.approval_id, input.run_id, input.account_id, input.phase, bodyJson, now, now)
      .run();
  }
  const instance = await env.MARKETING_WORKFLOW.get(input.run_id);
  await instance.sendEvent({
    type: input.phase === "candidates" ? "candidate_approval" : "human_approval",
    payload,
  });
  const deliveredAt = new Date().toISOString();
  await env.DB.prepare(
    `UPDATE marketing_review_event_receipts SET delivered_at = ?, updated_at = ?
     WHERE approval_id = ?`,
  )
    .bind(deliveredAt, deliveredAt, input.approval_id)
    .run();
  return { accepted: true, duplicate: false };
}

function reviewEventWasApplied(phase, state) {
  if (phase === "candidates") {
    return !["scheduled", "context_snapshot", "research", "planning", "candidate_generation", "awaiting_candidate_approval"].includes(state);
  }
  return [
    "approved",
    "rejected",
    "scheduled_for_publish",
    "publishing",
    "published",
    "observing",
    "evaluated",
    "memory_committed",
    "completed",
  ].includes(state);
}

async function sendTaskCompletion(env, task, callback) {
  const instance = await env.MARKETING_WORKFLOW.get(task.run_id);
  await instance.sendEvent({
    type: taskCompletionEventType(task.kind, callback.task_id),
    payload: callback,
  });
}

async function transition(db, runId, from, to, detail) {
  assertTransition(from, to);
  const now = new Date().toISOString();
  const result = await db.prepare(
    `UPDATE marketing_runs SET state = ?, context_digest = COALESCE(?, context_digest),
       publication_id = COALESCE(?, publication_id), error_code = COALESCE(?, error_code), updated_at = ?
     WHERE run_id = ? AND state = ?`,
  )
    .bind(
      to,
      detail.context_digest ?? null,
      detail.publication_id ?? null,
      detail.failure_code ?? null,
      now,
      runId,
      from,
    )
    .run();
  if (result.meta.changes !== 1) throw new Error(`concurrent run transition for ${runId}`);
  await recordEvent(db, runId, to, detail);
}

async function recordEvent(db, runId, state, detail) {
  await db.prepare(
    "INSERT INTO marketing_run_events (event_id, run_id, state, detail_json, created_at) VALUES (?, ?, ?, ?, ?)",
  )
    .bind(crypto.randomUUID(), runId, state, JSON.stringify(detail), new Date().toISOString())
    .run();
}

async function loadAccount(db, accountId) {
  const row = await db.prepare("SELECT config_json FROM marketing_accounts WHERE account_id = ?")
    .bind(accountId)
    .first();
  if (!row) throw new HttpError(404, "account not found");
  return JSON.parse(row.config_json);
}

async function loadInstruction(db, revision) {
  const row = await db.prepare("SELECT revision, body, body_sha256 FROM shared_instructions WHERE revision = ?")
    .bind(revision)
    .first();
  if (!row) throw new Error(`shared instruction ${revision} not found`);
  return row;
}

async function readRun(db, runId) {
  const run = await db.prepare("SELECT * FROM marketing_runs WHERE run_id = ?").bind(runId).first();
  if (!run) throw new HttpError(404, "run not found");
  const events = await db.prepare(
    "SELECT state, detail_json, created_at FROM marketing_run_events WHERE run_id = ? ORDER BY created_at",
  )
    .bind(runId)
    .all();
  return { ...run, events: events.results.map((row) => ({ ...row, detail: JSON.parse(row.detail_json) })) };
}

async function currentRunState(db, runId) {
  const row = await db.prepare("SELECT state FROM marketing_runs WHERE run_id = ?").bind(runId).first();
  if (!row) throw new Error(`run ${runId} not found`);
  return row.state;
}

async function activeRun(db, accountId) {
  return db
    .prepare(
      `SELECT run_id, state FROM marketing_runs
       WHERE account_id = ?
         AND state NOT IN ('completed', 'failed', 'rejected', 'unknown_side_effect')
       ORDER BY created_at DESC LIMIT 1`,
    )
    .bind(accountId)
    .first();
}

function evaluate(samples) {
  const final = samples.at(-1) ?? {};
  const views = Number(final.views ?? 0);
  const likes = Number(final.likes ?? 0);
  return { samples, engagement_rate: views > 0 ? likes / views : 0 };
}

async function sha256(value) {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, "0")).join("");
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) =>
      `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}
