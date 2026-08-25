import { DurableObject, WorkflowEntrypoint } from "cloudflare:workers";

import {
  accountName,
  approvalPhase,
  assertRunnableAdapterMode,
  assertTransition,
  normalizeCandidateIds,
  observationSchedule,
  selectedCandidateIds,
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
      await this.env.TASK_QUEUE.send(JSON.stringify(body), { contentType: "text" });
      return body;
    });
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
        return Response.json({ ok: true });
      }
      authorize(
        request,
        url.pathname === "/v1/task-callbacks"
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
      return Response.json({ error: "not_found" }, { status: 404 });
    } catch (error) {
      const status = error instanceof HttpError ? error.status : 500;
      return Response.json({ error: error.message }, { status });
    }
  },

  async scheduled(_event, env, ctx) {
    ctx.waitUntil(startDueRuns(env));
  },
};

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

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

async function receiveCallback(env, callback) {
  if (
    typeof callback.callback_id !== "string" ||
    callback.callback_id.length < 1 ||
    callback.callback_id.length > 256
  ) {
    throw new HttpError(400, "callback_id must be a non-empty identifier");
  }
  const resultJson = JSON.stringify(callback.result);
  if (new TextEncoder().encode(resultJson).byteLength > MAX_CALLBACK_RESULT_BYTES) {
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
