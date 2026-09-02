const ONLINE_WINDOW_MS = 45_000;
const DEFAULT_ENROLLMENT_TTL_SECONDS = 600;
const MAX_ENROLLMENT_TTL_SECONDS = 3_600;
const INITIAL_LEASE_SECONDS = 120;
const ACCEPTED_LEASE_SECONDS = 900;
const MAX_EXECUTION_LEASE_SECONDS = 3_600;
const MAX_WORKER_PAYLOAD_BYTES = 16 * 1024;
const MAX_WORKER_CALLBACK_BYTES = 24 * 1024 * 1024;
const WORKER_STATES = new Set(["active", "draining"]);
// The job kinds a task row can carry. A worker only leases a task whose kind it advertises.
export const WORKER_TASK_KINDS = Object.freeze(["capture", "generate_candidates"]);
// What a worker that advertises nothing can do. Every Mac enrolled before caption generation
// existed is one of those, and capture is the only job its Python knows how to run.
const LEGACY_WORKER_TASK_KINDS = Object.freeze(["capture"]);
const MAX_ADVERTISED_TASK_KINDS = 8;

export async function handleMacWorkerRequest(request, env, receiveTaskCallback) {
  const url = new URL(request.url);
  const isWorkerRoute = url.pathname.startsWith("/v1/workers/");
  if (url.pathname !== "/api/workers/status" &&
      url.pathname !== "/v1/workers" &&
      url.pathname !== "/v1/worker-enrollments" &&
      !isWorkerRoute) {
    return null;
  }

  try {
    if (request.method === "GET" && url.pathname === "/api/workers/status") {
      return Response.json(await publicWorkerStatus(env.DB));
    }
    if (request.method === "POST" && url.pathname === "/v1/workers/enroll") {
      return Response.json(await enrollWorker(request, env.DB), { status: 201 });
    }

    if (request.method === "POST" && url.pathname === "/v1/workers/heartbeat") {
      const worker = await requireWorker(request, env.DB);
      return Response.json(await heartbeatWorker(
        env.DB, worker, await readJson(request), new Date(), env.TRACE_MARKETING_RELEASE_VERSION,
      ));
    }
    if (request.method === "POST" && url.pathname === "/v1/workers/tasks/claim") {
      const worker = await requireWorker(request, env.DB);
      const body = await readJson(request);
      await heartbeatWorker(env.DB, worker, body);
      if (body.doctor?.ready !== true) return Response.json({ leases: [] });
      return Response.json({ leases: await claimWorkerTasks(env.DB, worker, new Date()) });
    }
    if (request.method === "POST" && url.pathname === "/v1/workers/tasks/ack") {
      const worker = await requireWorker(request, env.DB);
      return Response.json(await acknowledgeWorkerLeases(env.DB, worker, await readJson(request)));
    }
    if (request.method === "POST" && url.pathname === "/v1/workers/tasks/executing") {
      const worker = await requireWorker(request, env.DB);
      const body = await readJson(request);
      return Response.json(await markWorkerTaskExecuting(env.DB, worker, body.task_id));
    }
    if (request.method === "POST" && url.pathname === "/v1/workers/task-callbacks") {
      const worker = await requireWorker(request, env.DB);
      const callback = await readJson(request, MAX_WORKER_CALLBACK_BYTES);
      const result = await receiveTaskCallback(callback, { worker_id: worker.worker_id });
      await env.DB.batch([
        env.DB.prepare(
          `UPDATE mac_workers SET current_task_id = NULL, updated_at = ?
           WHERE worker_id = ? AND current_task_id = ?`,
        ).bind(new Date().toISOString(), worker.worker_id, callback.task_id),
        env.DB.prepare(
          `UPDATE hosted_workspace_capture_tasks
           SET lease_expires_at = NULL, lease_started_at = NULL, updated_at = ?
           WHERE task_id = ? AND worker_id = ?`,
        ).bind(new Date().toISOString(), callback.task_id, worker.worker_id),
      ]);
      return Response.json(result, { status: 202 });
    }

    authorizeAdmin(request, env.CONTROL_PLANE_TOKEN);
    if (request.method === "POST" && url.pathname === "/v1/worker-enrollments") {
      return Response.json(await createEnrollment(request, env.DB), { status: 201 });
    }
    if (request.method === "GET" && url.pathname === "/v1/workers") {
      return Response.json({ workers: await listWorkers(env.DB) });
    }
    const stateRoute = url.pathname.match(/^\/v1\/workers\/([^/]+)\/state$/);
    if (request.method === "POST" && stateRoute) {
      return Response.json(
        await setWorkerState(env.DB, decodeURIComponent(stateRoute[1]), await readJson(request)),
      );
    }
    const revokeRoute = url.pathname.match(/^\/v1\/workers\/([^/]+)\/revoke$/);
    if (request.method === "POST" && revokeRoute) {
      return Response.json(await revokeWorker(env.DB, decodeURIComponent(revokeRoute[1])));
    }
    return Response.json({ error: "not_found" }, { status: 404 });
  } catch (error) {
    const status = Number.isInteger(error?.status) ? error.status : 500;
    return Response.json(
      { error: status === 500 ? "mac worker request failed" : error.message },
      { status },
    );
  }
}

export async function hasRegisteredBrokerWorker(db) {
  const row = await db.prepare(
    "SELECT worker_id FROM mac_workers WHERE state != 'revoked' LIMIT 1",
  ).bind().first();
  return Boolean(row);
}

/**
 * Which job kinds one worker says it can run.
 *
 * The advertisement is a comma-joined string rather than a list because `normalizeObject`
 * flattens every non-scalar capability value to null — a worker that sent an array would
 * read as having advertised nothing. Silence means the worker predates this field, and the
 * only job that existed then is capture: that is the whole point of the default, because
 * during the minutes between deploying the Worker and a Mac updating itself, that Mac must
 * not lease a caption batch its Python cannot parse.
 */
export function workerTaskKinds(capabilities) {
  const declared = capabilities?.task_kinds;
  if (typeof declared !== "string" || !declared.trim()) return [...LEGACY_WORKER_TASK_KINDS];
  const advertised = declared
    .split(",")
    .map((kind) => kind.trim())
    .filter((kind) => WORKER_TASK_KINDS.includes(kind))
    .slice(0, MAX_ADVERTISED_TASK_KINDS);
  const unique = [...new Set(advertised)];
  return unique.length > 0 ? unique : [...LEGACY_WORKER_TASK_KINDS];
}

/** True when some non-revoked worker advertises this job kind. */
export async function hasWorkerForTaskKind(db, kind, requiredCapability = null) {
  const result = await db.prepare(
    "SELECT capabilities_json FROM mac_workers WHERE state != 'revoked'",
  ).bind().all();
  return result.results.some((row) => {
    const capabilities = parseObject(row.capabilities_json);
    return workerTaskKinds(capabilities).includes(kind)
      && (!requiredCapability || capabilities[requiredCapability] === true);
  });
}

export async function publicWorkerStatus(db, now = new Date()) {
  const result = await db.prepare(
    `SELECT display_name, pool, state, capabilities_json, doctor_json, last_seen_at,
            current_task_id
     FROM mac_workers WHERE state != 'revoked' ORDER BY display_name`,
  ).all();
  const rows = result.results.map((row) => ({
    display_name: row.display_name,
    pool: row.pool,
    status: workerOperationalStatus(row, now),
    kinds: workerTaskKinds(parseObject(row.capabilities_json)),
    feedback_context: parseObject(row.capabilities_json).feedback_context_v1 === true,
  }));
  // The screen names the Macs; it does not say what each one can run. What it needs is the
  // one number a person acts on: whether any Mac online right now can write captions.
  const workers = rows.map(({ kinds, feedback_context, ...worker }) => worker);
  const counts = {
    registered: workers.length,
    online: workers.filter((worker) => ["ready", "busy", "degraded"].includes(worker.status)).length,
    ready: workers.filter((worker) => worker.status === "ready").length,
    busy: workers.filter((worker) => worker.status === "busy").length,
    draining: workers.filter((worker) => worker.status === "draining").length,
    generation_ready: 0,
  };
  counts.generation_ready = rows.filter((row) =>
    ["ready", "busy"].includes(row.status)
      && row.kinds.includes("generate_candidates")
      && row.feedback_context).length;
  let status = "not_configured";
  if (counts.ready > 0 || counts.busy > 0) status = "ready";
  else if (counts.online > 0 || counts.draining > 0) status = "degraded";
  else if (counts.registered > 0) status = "offline";
  return { status, counts, workers, online_window_seconds: ONLINE_WINDOW_MS / 1000 };
}

export function workerOperationalStatus(row, now = new Date()) {
  if (row.state === "draining") return "draining";
  const seen = Date.parse(row.last_seen_at ?? "");
  if (!Number.isFinite(seen) || now.getTime() - seen > ONLINE_WINDOW_MS) return "offline";
  let doctor = {};
  try {
    doctor = JSON.parse(row.doctor_json || "{}");
  } catch {
    return "degraded";
  }
  if (doctor.ready === false) return "degraded";
  return row.current_task_id ? "busy" : "ready";
}

export async function claimWorkerTasks(db, worker, now = new Date()) {
  if (worker.state !== "active") return [];
  await clearStaleWorkerAssignment(db, worker.worker_id, now);
  // What this Mac says it can run, read back from the row the heartbeat just wrote rather
  // than from the token lookup, so a worker that updated itself is trusted on this poll.
  const advertised = await claimableWorkerCapabilities(db, worker.worker_id);
  const kinds = advertised.kinds;
  if (kinds.length === 0) return [];
  const supportedCapability = advertised.feedbackContext ? "feedback_context_v1" : "__none__";
  const kindPlaceholders = kinds.map(() => "?").join(", ");
  const current = await db.prepare(
    `SELECT task_id, task_json, lease_id, attempt_count
     FROM hosted_workspace_capture_tasks
     WHERE worker_id = ? AND dispatch_mode = 'worker_broker' AND state = 'queued'
       AND callback_id IS NULL AND callback_reservation_id IS NULL
       AND execution_started_at IS NULL AND lease_expires_at > ?
       AND kind IN (${kindPlaceholders})
       AND (required_capability IS NULL OR required_capability = ?)
     ORDER BY created_at LIMIT 1`,
  ).bind(worker.worker_id, now.toISOString(), ...kinds, supportedCapability).first();
  if (current) return [leaseResponse(current)];

  const reservation = `claim:${crypto.randomUUID()}`;
  const reserved = await db.prepare(
    `UPDATE mac_workers SET current_task_id = ?, updated_at = ?
     WHERE worker_id = ? AND state = 'active' AND current_task_id IS NULL`,
  ).bind(reservation, now.toISOString(), worker.worker_id).run();
  if (reserved.meta.changes !== 1) return [];

  for (let attempt = 0; attempt < 3; attempt += 1) {
    const task = await db.prepare(
      `SELECT task_id FROM hosted_workspace_capture_tasks
       WHERE dispatch_mode = 'worker_broker' AND state = 'queued' AND callback_id IS NULL
         AND callback_reservation_id IS NULL AND execution_started_at IS NULL
         AND (worker_id IS NULL OR lease_expires_at IS NULL OR lease_expires_at <= ?)
         AND kind IN (${kindPlaceholders})
         AND (required_capability IS NULL OR required_capability = ?)
       ORDER BY created_at LIMIT 1`,
    ).bind(now.toISOString(), ...kinds, supportedCapability).first();
    if (!task) {
      await clearWorkerReservation(db, worker.worker_id, reservation, now);
      return [];
    }
    const leaseId = crypto.randomUUID();
    const expiresAt = new Date(now.getTime() + INITIAL_LEASE_SECONDS * 1000).toISOString();
    const claimed = await db.prepare(
      `UPDATE hosted_workspace_capture_tasks
       SET worker_id = ?, lease_id = ?, lease_expires_at = ?, lease_started_at = ?,
           lease_accepted_at = NULL,
           attempt_count = attempt_count + 1, updated_at = ?
       WHERE task_id = ? AND dispatch_mode = 'worker_broker' AND state = 'queued'
         AND callback_id IS NULL AND callback_reservation_id IS NULL
         AND execution_started_at IS NULL
         AND (worker_id IS NULL OR lease_expires_at IS NULL OR lease_expires_at <= ?)
         AND kind IN (${kindPlaceholders})
         AND (required_capability IS NULL OR required_capability = ?)
         AND EXISTS (SELECT 1 FROM mac_workers
                     WHERE worker_id = ? AND state = 'active' AND current_task_id = ?)`,
    ).bind(
      worker.worker_id,
      leaseId,
      expiresAt,
      now.toISOString(),
      now.toISOString(),
      task.task_id,
      now.toISOString(),
      ...kinds,
      supportedCapability,
      worker.worker_id,
      reservation,
    ).run();
    if (claimed.meta.changes !== 1) continue;
    const assigned = await db.prepare(
      `UPDATE mac_workers SET current_task_id = ?, updated_at = ?
       WHERE worker_id = ? AND state != 'revoked' AND current_task_id = ?`,
    ).bind(task.task_id, now.toISOString(), worker.worker_id, reservation).run();
    if (assigned.meta.changes !== 1) {
      await releaseClaimedTask(db, worker.worker_id, task.task_id, leaseId, now);
      return [];
    }
    const winner = await db.prepare(
      `SELECT task_id, task_json, lease_id, attempt_count
       FROM hosted_workspace_capture_tasks
       WHERE task_id = ? AND worker_id = ? AND lease_id = ?`,
    ).bind(task.task_id, worker.worker_id, leaseId).first();
    if (!winner) {
      await clearWorkerReservation(db, worker.worker_id, task.task_id, now);
      return [];
    }
    return [leaseResponse(winner)];
  }
  await clearWorkerReservation(db, worker.worker_id, reservation, now);
  return [];
}

/**
 * The kinds this worker may lease, read fresh from its stored capabilities.
 *
 * The claim route heartbeats before it claims, so this row is the worker's current
 * advertisement rather than whatever it said when its token was last looked up.
 */
async function claimableWorkerCapabilities(db, workerId) {
  const row = await db.prepare(
    "SELECT capabilities_json FROM mac_workers WHERE worker_id = ?",
  ).bind(workerId).first();
  if (!row) return { kinds: [], feedbackContext: false };
  const capabilities = parseObject(row.capabilities_json);
  return {
    kinds: workerTaskKinds(capabilities),
    feedbackContext: capabilities.feedback_context_v1 === true,
  };
}

async function clearWorkerReservation(db, workerId, reservation, now) {
  await db.prepare(
    `UPDATE mac_workers SET current_task_id = NULL, updated_at = ?
     WHERE worker_id = ? AND current_task_id = ?`,
  ).bind(now.toISOString(), workerId, reservation).run();
}

async function releaseClaimedTask(db, workerId, taskId, leaseId, now) {
  await db.prepare(
    `UPDATE hosted_workspace_capture_tasks
     SET worker_id = NULL, lease_id = NULL, lease_expires_at = NULL,
         lease_started_at = NULL, lease_accepted_at = NULL, updated_at = ?
     WHERE task_id = ? AND worker_id = ? AND lease_id = ? AND state = 'queued'`,
  ).bind(now.toISOString(), taskId, workerId, leaseId).run();
}

async function createEnrollment(request, db) {
  const body = await readJson(request);
  const displayName = requiredName(body.display_name, "display_name", 80);
  const pool = optionalName(body.pool, "pool", 40) || "appium";
  const ttl = boundedInteger(
    body.ttl_seconds,
    DEFAULT_ENROLLMENT_TTL_SECONDS,
    60,
    MAX_ENROLLMENT_TTL_SECONDS,
  );
  const code = randomSecret("trace-enroll");
  const now = new Date();
  const enrollmentId = crypto.randomUUID();
  const expiresAt = new Date(now.getTime() + ttl * 1000).toISOString();
  await db.prepare(
    `INSERT INTO mac_worker_enrollments
      (enrollment_id, code_sha256, display_name, pool, expires_at, created_at)
     VALUES (?, ?, ?, ?, ?, ?)`,
  ).bind(enrollmentId, await sha256(code), displayName, pool, expiresAt, now.toISOString()).run();
  return {
    enrollment_id: enrollmentId,
    enrollment_code: code,
    display_name: displayName,
    pool,
    expires_at: expiresAt,
  };
}

async function enrollWorker(request, db) {
  const body = await readJson(request);
  const code = requiredName(body.enrollment_code, "enrollment_code", 256);
  const digest = await sha256(code);
  const now = new Date().toISOString();
  const enrollment = await db.prepare(
    `SELECT enrollment_id, display_name, pool, expires_at, used_at
     FROM mac_worker_enrollments WHERE code_sha256 = ?`,
  ).bind(digest).first();
  if (!enrollment || enrollment.used_at || enrollment.expires_at <= now) {
    throw new WorkerHttpError(401, "enrollment code is invalid or expired");
  }
  const workerId = crypto.randomUUID();
  const token = randomSecret("trace-worker");
  const tokenDigest = await sha256(token);
  const consumed = await db.prepare(
    `UPDATE mac_worker_enrollments SET used_at = ?, worker_id = ?
     WHERE enrollment_id = ? AND used_at IS NULL AND expires_at > ?`,
  ).bind(now, workerId, enrollment.enrollment_id, now).run();
  if (consumed.meta.changes !== 1) {
    throw new WorkerHttpError(409, "enrollment code was already used");
  }
  try {
    await db.prepare(
      `INSERT INTO mac_workers
        (worker_id, display_name, pool, token_sha256, state, capabilities_json,
         doctor_json, version, last_seen_at, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)`,
    ).bind(
      workerId,
      enrollment.display_name,
      enrollment.pool,
      tokenDigest,
      JSON.stringify(normalizeObject(body.capabilities)),
      JSON.stringify(normalizeDoctor(body.doctor)),
      optionalName(body.version, "version", 80),
      now,
      now,
      now,
    ).run();
  } catch (error) {
    await db.prepare(
      "UPDATE mac_worker_enrollments SET used_at = NULL, worker_id = NULL WHERE enrollment_id = ? AND worker_id = ?",
    ).bind(enrollment.enrollment_id, workerId).run();
    throw error;
  }
  return {
    worker_id: workerId,
    worker_token: token,
    display_name: enrollment.display_name,
    pool: enrollment.pool,
    state: "active",
  };
}

async function requireWorker(request, db) {
  const authorization = request.headers.get("authorization") ?? "";
  const token = authorization.startsWith("Bearer ") ? authorization.slice(7) : "";
  if (!token) throw new WorkerHttpError(401, "unauthorized");
  const worker = await db.prepare(
    `SELECT worker_id, display_name, pool, state, current_task_id
     FROM mac_workers WHERE token_sha256 = ?`,
  ).bind(await sha256(token)).first();
  if (!worker || worker.state === "revoked") throw new WorkerHttpError(401, "unauthorized");
  return worker;
}

export async function heartbeatWorker(
  db,
  worker,
  body,
  clock = new Date(),
  releaseTarget = null,
) {
  const now = clock.toISOString();
  const version = optionalName(body.version, "version", 80);
  const renewedUntil = new Date(clock.getTime() + ACCEPTED_LEASE_SECONDS * 1000).toISOString();
  const maximumStartedAt = new Date(
    clock.getTime() - MAX_EXECUTION_LEASE_SECONDS * 1000,
  ).toISOString();
  const [updated] = await db.batch([
    db.prepare(
      `UPDATE mac_workers SET capabilities_json = ?, doctor_json = ?, version = ?,
       last_seen_at = ?, updated_at = ? WHERE worker_id = ? AND state != 'revoked'`,
    ).bind(
      JSON.stringify(normalizeObject(body.capabilities)),
      JSON.stringify(normalizeDoctor(body.doctor)),
      version,
      now,
      now,
      worker.worker_id,
    ),
    db.prepare(
      `UPDATE hosted_workspace_capture_tasks SET lease_expires_at = ?, updated_at = ?
       WHERE worker_id = ? AND dispatch_mode = 'worker_broker' AND state = 'queued'
         AND callback_id IS NULL AND execution_started_at IS NULL
         AND lease_started_at IS NOT NULL AND lease_started_at > ?`,
    ).bind(renewedUntil, now, worker.worker_id, maximumStartedAt),
  ]);
  if (updated.meta.changes !== 1) throw new WorkerHttpError(401, "worker was revoked");
  return {
    worker_id: worker.worker_id,
    state: worker.state,
    seen_at: now,
    update_target_version: newerReleaseTarget(releaseTarget, version),
  };
}

async function acknowledgeWorkerLeases(db, worker, body) {
  const acknowledgements = stringList(body.acks, "acks", 5);
  const retries = stringList(body.retries, "retries", 5);
  const overlap = acknowledgements.find((leaseId) => retries.includes(leaseId));
  if (overlap) throw new WorkerHttpError(400, "a lease cannot be acknowledged and retried");
  const now = new Date();
  let accepted = 0;
  let retried = 0;
  for (const leaseId of acknowledgements) {
    const result = await db.prepare(
      `UPDATE hosted_workspace_capture_tasks
       SET lease_accepted_at = ?, lease_expires_at = ?, updated_at = ?
       WHERE lease_id = ? AND worker_id = ? AND dispatch_mode = 'worker_broker'
         AND state = 'queued' AND callback_id IS NULL AND callback_reservation_id IS NULL
         AND execution_started_at IS NULL AND lease_expires_at > ?`,
    ).bind(
      now.toISOString(),
      new Date(now.getTime() + ACCEPTED_LEASE_SECONDS * 1000).toISOString(),
      now.toISOString(),
      leaseId,
      worker.worker_id,
      now.toISOString(),
    ).run();
    accepted += result.meta.changes;
  }
  for (const leaseId of retries) {
    const task = await db.prepare(
      `SELECT task_id FROM hosted_workspace_capture_tasks
       WHERE lease_id = ? AND worker_id = ? AND dispatch_mode = 'worker_broker'
         AND state = 'queued' AND callback_id IS NULL
         AND callback_reservation_id IS NULL AND execution_started_at IS NULL`,
    ).bind(leaseId, worker.worker_id).first();
    if (!task) continue;
    const result = await db.prepare(
      `UPDATE hosted_workspace_capture_tasks
       SET worker_id = NULL, lease_id = NULL, lease_expires_at = NULL,
           lease_started_at = NULL, lease_accepted_at = NULL, execution_started_at = NULL,
           updated_at = ?
       WHERE task_id = ? AND lease_id = ? AND worker_id = ? AND state = 'queued'
         AND callback_reservation_id IS NULL`,
    ).bind(now.toISOString(), task.task_id, leaseId, worker.worker_id).run();
    if (result.meta.changes === 1) {
      retried += 1;
      await db.prepare(
        `UPDATE mac_workers SET current_task_id = NULL, updated_at = ?
         WHERE worker_id = ? AND current_task_id = ?`,
      ).bind(now.toISOString(), worker.worker_id, task.task_id).run();
    }
  }
  if (accepted !== acknowledgements.length || retried !== retries.length) {
    throw new WorkerHttpError(409, "one or more leases are no longer owned by this worker");
  }
  return { accepted, retried };
}

export async function markWorkerTaskExecuting(db, worker, taskId, clock = new Date()) {
  if (typeof taskId !== "string" || !taskId || taskId.length > 128) {
    throw new WorkerHttpError(400, "task_id is required");
  }
  const now = clock.toISOString();
  const updated = await db.prepare(
    `UPDATE hosted_workspace_capture_tasks
     SET execution_started_at = ?, lease_expires_at = NULL, updated_at = ?
     WHERE task_id = ? AND worker_id = ? AND dispatch_mode = 'worker_broker'
       AND state = 'queued' AND callback_id IS NULL AND callback_reservation_id IS NULL
       AND execution_started_at IS NULL
       AND lease_accepted_at IS NOT NULL AND lease_expires_at > ?`,
  ).bind(now, now, taskId, worker.worker_id, now).run();
  if (updated.meta.changes === 1) return { accepted: true, duplicate: false };
  const existing = await db.prepare(
    `SELECT worker_id, execution_started_at FROM hosted_workspace_capture_tasks
     WHERE task_id = ? AND dispatch_mode = 'worker_broker' AND state = 'queued'
       AND callback_id IS NULL`,
  ).bind(taskId).first();
  if (existing?.worker_id === worker.worker_id && existing.execution_started_at) {
    return { accepted: true, duplicate: true };
  }
  throw new WorkerHttpError(409, "task is not ready for native execution");
}

export async function reserveWorkerTaskCallback(
  db,
  worker,
  task,
  callbackId,
  resultJson,
  clock = new Date(),
) {
  if (typeof callbackId !== "string" || !callbackId || !task?.lease_id) {
    throw new WorkerHttpError(409, "worker callback has no current lease");
  }
  const now = clock.toISOString();
  const resultDigest = await sha256(resultJson);
  const allowPreExecutionFailure = callbackResultStatus(resultJson) === "failed" ? 1 : 0;
  const reserved = await db.prepare(
    `UPDATE hosted_workspace_capture_tasks
     SET callback_reservation_id = ?, callback_reserved_at = ?,
         callback_result_sha256 = ?, updated_at = ?
     WHERE task_id = ? AND worker_id = ? AND lease_id = ?
       AND dispatch_mode = 'worker_broker' AND state = 'queued'
       AND callback_id IS NULL
       AND (execution_started_at IS NOT NULL OR ? = 1)
       AND callback_reservation_id IS NULL`,
  ).bind(
    callbackId, now, resultDigest, now, task.task_id, worker.worker_id, task.lease_id,
    allowPreExecutionFailure,
  ).run();
  if (reserved.meta.changes === 1) return { duplicate: false, retry: false };
  const current = await db.prepare(
    `SELECT worker_id, lease_id, state, callback_id, result_json,
            callback_reservation_id, callback_result_sha256
     FROM hosted_workspace_capture_tasks WHERE task_id = ?`,
  ).bind(task.task_id).first();
  if (current?.callback_id === callbackId && current.result_json === resultJson) {
    return { duplicate: true, retry: false };
  }
  if (
    current?.worker_id === worker.worker_id && current.lease_id === task.lease_id &&
    current.state === "queued" && !current.callback_id &&
    current.callback_reservation_id === callbackId &&
    current.callback_result_sha256 === resultDigest
  ) {
    return { duplicate: false, retry: true };
  }
  throw new WorkerHttpError(409, "worker no longer owns the callback lease or result");
}

export function assertHostedCallbackTransport(task, worker) {
  if (task.dispatch_mode === "worker_broker" && !worker) {
    throw new WorkerHttpError(409, "broker task requires its assigned worker callback");
  }
  if (task.dispatch_mode !== "worker_broker" && worker) {
    throw new WorkerHttpError(409, "legacy task does not accept a worker-scoped callback");
  }
}

async function clearStaleWorkerAssignment(db, workerId, now) {
  const worker = await db.prepare(
    "SELECT current_task_id FROM mac_workers WHERE worker_id = ?",
  ).bind(workerId).first();
  if (!worker?.current_task_id) return;
  const task = await db.prepare(
    `SELECT task_id FROM hosted_workspace_capture_tasks
     WHERE task_id = ? AND worker_id = ? AND dispatch_mode = 'worker_broker'
       AND state = 'queued' AND callback_id IS NULL
       AND (callback_reservation_id IS NOT NULL OR execution_started_at IS NOT NULL
            OR lease_expires_at > ?)`,
  ).bind(worker.current_task_id, workerId, now.toISOString()).first();
  if (task) return;
  await db.prepare(
    `UPDATE mac_workers SET current_task_id = NULL, updated_at = ?
     WHERE worker_id = ? AND current_task_id = ?`,
  ).bind(now.toISOString(), workerId, worker.current_task_id).run();
}

async function listWorkers(db) {
  const result = await db.prepare(
    `SELECT worker_id, display_name, pool, state, capabilities_json, doctor_json,
            version, last_seen_at, current_task_id, created_at, updated_at
     FROM mac_workers ORDER BY created_at DESC`,
  ).all();
  return result.results.map((row) => ({
    ...row,
    status: workerOperationalStatus(row),
    capabilities: parseObject(row.capabilities_json),
    doctor: parseObject(row.doctor_json),
    capabilities_json: undefined,
    doctor_json: undefined,
  }));
}

async function setWorkerState(db, workerId, body) {
  const state = requiredName(body.state, "state", 20);
  if (!WORKER_STATES.has(state)) throw new WorkerHttpError(400, "state must be active or draining");
  const now = new Date().toISOString();
  const updated = await db.prepare(
    `UPDATE mac_workers SET state = ?, updated_at = ?
     WHERE worker_id = ? AND state != 'revoked'`,
  ).bind(state, now, workerId).run();
  if (updated.meta.changes !== 1) throw new WorkerHttpError(404, "worker not found");
  return { worker_id: workerId, state };
}

export async function revokeWorker(db, workerId) {
  const now = new Date().toISOString();
  const worker = await db.prepare(
    "SELECT current_task_id FROM mac_workers WHERE worker_id = ? AND state != 'revoked'",
  ).bind(workerId).first();
  if (!worker) throw new WorkerHttpError(404, "worker not found");
  const [revoked, released] = await db.batch([
    db.prepare(
      `UPDATE mac_workers SET state = 'revoked', token_sha256 = NULL,
       current_task_id = NULL, updated_at = ? WHERE worker_id = ? AND state != 'revoked'
       AND NOT EXISTS (
         SELECT 1 FROM hosted_workspace_capture_tasks
         WHERE worker_id = ? AND callback_reservation_id IS NOT NULL AND callback_id IS NULL
       )`,
    ).bind(now, workerId, workerId),
    db.prepare(
      `UPDATE hosted_workspace_capture_tasks
       SET worker_id = NULL, lease_id = NULL, lease_expires_at = NULL,
           lease_started_at = NULL, lease_accepted_at = NULL, execution_started_at = NULL,
           updated_at = ?
       WHERE worker_id = ? AND dispatch_mode = 'worker_broker' AND state = 'queued'
         AND callback_id IS NULL AND callback_reservation_id IS NULL`,
    ).bind(now, workerId),
  ]);
  if (revoked.meta.changes !== 1) {
    throw new WorkerHttpError(409, "callback application is in progress; retry revocation later");
  }
  const releasedTaskId = released.meta.changes === 1 ? worker.current_task_id ?? null : null;
  return {
    worker_id: workerId,
    state: "revoked",
    released_task_id: releasedTaskId,
    retained_task_id: releasedTaskId ? null : worker.current_task_id ?? null,
  };
}

function leaseResponse(row) {
  return {
    message_id: row.task_id,
    lease_id: row.lease_id,
    attempts: Number(row.attempt_count),
    task: JSON.parse(row.task_json),
  };
}

function authorizeAdmin(request, token) {
  if (!token || request.headers.get("authorization") !== `Bearer ${token}`) {
    throw new WorkerHttpError(401, "unauthorized");
  }
}

async function readJson(request, maximumBytes = MAX_WORKER_PAYLOAD_BYTES) {
  const length = Number(request.headers.get("content-length") ?? 0);
  if (length > maximumBytes) throw new WorkerHttpError(413, "request body is too large");
  try {
    const body = await request.json();
    if (!body || typeof body !== "object" || Array.isArray(body)) throw new Error("not object");
    if (new TextEncoder().encode(JSON.stringify(body)).byteLength > maximumBytes) {
      throw new WorkerHttpError(413, "request body is too large");
    }
    return body;
  } catch (error) {
    if (error instanceof WorkerHttpError) throw error;
    throw new WorkerHttpError(400, "request body must be a JSON object");
  }
}

function normalizeObject(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value).slice(0, 20).map(([key, item]) => [
      String(key).slice(0, 80),
      typeof item === "string" || typeof item === "number" || typeof item === "boolean"
        ? item
        : null,
    ]),
  );
}

function normalizeDoctor(value) {
  const normalized = normalizeObject(value);
  return {
    ready: normalized.ready === true,
    summary: typeof normalized.summary === "string" ? normalized.summary.slice(0, 200) : "",
  };
}

function requiredName(value, field, maxLength) {
  if (typeof value !== "string" || !value.trim() || value.length > maxLength) {
    throw new WorkerHttpError(400, `${field} is required and must be at most ${maxLength} characters`);
  }
  return value.trim();
}

function optionalName(value, field, maxLength) {
  if (value === undefined || value === null || value === "") return null;
  return requiredName(value, field, maxLength);
}

function newerReleaseTarget(target, current) {
  const targetParts = strictReleaseVersion(target);
  const currentParts = strictReleaseVersion(current);
  if (!targetParts || !currentParts) return null;
  for (let index = 0; index < targetParts.length; index += 1) {
    const order = numericPartOrder(targetParts[index], currentParts[index]);
    if (order !== 0) return order > 0 ? target : null;
  }
  return null;
}

function strictReleaseVersion(value) {
  if (typeof value !== "string") return null;
  const matched = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/u.exec(value);
  return matched ? matched.slice(1) : null;
}

function numericPartOrder(left, right) {
  return left.length === right.length ? left.localeCompare(right) : left.length - right.length;
}

function boundedInteger(value, fallback, minimum, maximum) {
  const parsed = value === undefined ? fallback : Number(value);
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new WorkerHttpError(400, `value must be an integer between ${minimum} and ${maximum}`);
  }
  return parsed;
}

function stringList(value, field, maximum) {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > maximum ||
      value.some((item) => typeof item !== "string" || !item || item.length > 128)) {
    throw new WorkerHttpError(400, `${field} must be an array of lease identifiers`);
  }
  return [...new Set(value)];
}

function parseObject(value) {
  try {
    const parsed = JSON.parse(value || "{}");
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function callbackResultStatus(resultJson) {
  try {
    return JSON.parse(resultJson)?.status ?? null;
  } catch {
    return null;
  }
}

function randomSecret(prefix) {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  const encoded = btoa(String.fromCharCode(...bytes))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
  return `${prefix}_${encoded}`;
}

async function sha256(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

class WorkerHttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}
