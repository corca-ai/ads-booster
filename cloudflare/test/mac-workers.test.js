import assert from "node:assert/strict";
import test from "node:test";

import {
  assertHostedCallbackTransport,
  claimWorkerTasks,
  handleMacWorkerRequest,
  hasRegisteredBrokerWorker,
  heartbeatWorker,
  markWorkerTaskExecuting,
  publicWorkerStatus,
  reserveWorkerTaskCallback,
  revokeWorker,
  workerOperationalStatus,
  workerTaskKinds,
} from "../src/mac-workers.js";

function task(overrides = {}) {
  return {
    task_id: "task-1",
    task_json: JSON.stringify({
      schema_version: "1",
      task_id: "task-1",
      run_id: "run-1",
      account_id: "trace_demo_kr",
      kind: "capture",
      idempotency_key: "hosted:task-1",
      payload: {},
      created_at: "2026-08-26T00:00:00.000Z",
      credential_ref: null,
    }),
    state: "queued",
    dispatch_mode: "worker_broker",
    kind: "capture",
    callback_id: null,
    worker_id: null,
    lease_id: null,
    lease_expires_at: null,
    lease_started_at: null,
    lease_accepted_at: null,
    execution_started_at: null,
    callback_reservation_id: null,
    callback_reserved_at: null,
    callback_result_sha256: null,
    attempt_count: 0,
    created_at: "2026-08-26T00:00:00.000Z",
    ...overrides,
  };
}

function worker(workerId, overrides = {}) {
  return {
    worker_id: workerId,
    display_name: workerId,
    pool: "appium",
    state: "active",
    capabilities_json: '{"task_kinds":"capture,generate_candidates"}',
    doctor_json: '{"ready":true}',
    last_seen_at: "2026-08-26T00:00:00.000Z",
    current_task_id: null,
    ...overrides,
  };
}

class ClaimDb {
  constructor(workers, tasks) {
    this.workers = new Map(workers.map((row) => [row.worker_id, { ...row }]));
    this.tasks = new Map(tasks.map((row) => [row.task_id, { ...row }]));
  }

  prepare(sql) {
    return new ClaimStatement(this, sql);
  }
}

class ClaimStatement {
  constructor(db, sql, values = []) {
    this.db = db;
    this.sql = sql;
    this.values = values;
  }

  bind(...values) {
    return new ClaimStatement(this.db, this.sql, values);
  }

  async first() {
    if (this.sql.includes("FROM mac_workers WHERE token_sha256")) {
      return [...this.db.workers.values()][0] ?? null;
    }
    if (this.sql.includes("SELECT capabilities_json FROM mac_workers WHERE worker_id = ?")) {
      return this.db.workers.get(this.values[0]) ?? null;
    }
    if (this.sql.includes("SELECT worker_id, execution_started_at")) {
      return this.db.tasks.get(this.values[0]) ?? null;
    }
    if (this.sql.includes("SELECT current_task_id FROM mac_workers")) {
      return this.db.workers.get(this.values[0]) ?? null;
    }
    if (this.sql.includes("WHERE worker_id = ?") && this.sql.includes("lease_expires_at > ?")) {
      const [workerId, now] = this.values;
      const kinds = this.values.slice(2);
      return [...this.db.tasks.values()].find((row) =>
        row.worker_id === workerId && row.dispatch_mode === "worker_broker" &&
        row.state === "queued" && !row.callback_id && !row.execution_started_at &&
        (!this.sql.includes("callback_reservation_id IS NULL") || !row.callback_reservation_id) &&
        kinds.includes(row.kind) &&
        row.lease_expires_at > now
      ) ?? null;
    }
    if (this.sql.includes("SELECT task_id FROM hosted_workspace_capture_tasks") &&
        this.sql.includes("worker_id IS NULL")) {
      const [now] = this.values;
      const kinds = this.values.slice(1);
      return [...this.db.tasks.values()].find((row) =>
        row.dispatch_mode === "worker_broker" && row.state === "queued" && !row.callback_id &&
        (!this.sql.includes("callback_reservation_id IS NULL") || !row.callback_reservation_id) &&
        kinds.includes(row.kind) &&
        !row.execution_started_at && (!row.worker_id || !row.lease_expires_at || row.lease_expires_at <= now)
      ) ?? null;
    }
    if (this.sql.includes("WHERE lease_id = ? AND worker_id = ?")) {
      const [leaseId, workerId] = this.values;
      return [...this.db.tasks.values()].find((row) =>
        row.lease_id === leaseId && row.worker_id === workerId && row.state === "queued" &&
        !row.callback_id && !row.execution_started_at &&
        (!this.sql.includes("callback_reservation_id IS NULL") || !row.callback_reservation_id)
      ) ?? null;
    }
    if (this.sql.includes("FROM hosted_workspace_capture_tasks") &&
        this.sql.includes("WHERE task_id = ? AND worker_id = ? AND lease_id = ?")) {
      const [taskId, workerId, leaseId] = this.values;
      const row = this.db.tasks.get(taskId);
      return row?.worker_id === workerId && row.lease_id === leaseId ? row : null;
    }
    if (this.sql.includes("SELECT task_id FROM hosted_workspace_capture_tasks") &&
        this.sql.includes("task_id = ? AND worker_id = ?")) {
      const [taskId, workerId, now] = this.values;
      const row = this.db.tasks.get(taskId);
      return row?.worker_id === workerId && row.state === "queued" &&
        !row.callback_id && (
          (this.sql.includes("callback_reservation_id IS NOT NULL") && row.callback_reservation_id) ||
          row.execution_started_at || row.lease_expires_at > now
        ) ? row : null;
    }
    throw new Error(`unexpected first SQL: ${this.sql}`);
  }

  async run() {
    if (this.sql.includes("SET execution_started_at = ?")) {
      const [startedAt, updatedAt, taskId, workerId, now] = this.values;
      const row = this.db.tasks.get(taskId);
      if (!row || row.worker_id !== workerId || row.dispatch_mode !== "worker_broker" ||
          row.state !== "queued" || row.callback_id || row.execution_started_at ||
          (this.sql.includes("callback_reservation_id IS NULL") && row.callback_reservation_id) ||
          !row.lease_accepted_at || !row.lease_expires_at || row.lease_expires_at <= now) {
        return { meta: { changes: 0 } };
      }
      Object.assign(row, {
        execution_started_at: startedAt,
        lease_expires_at: null,
        updated_at: updatedAt,
      });
      return { meta: { changes: 1 } };
    }
    if (this.sql.includes("SET worker_id = ?, lease_id = ?")) {
      const [workerId, leaseId, expiresAt, startedAt, updatedAt, taskId, now] = this.values;
      // The kind list sits between the task predicate and the owner check, so the last two
      // values are read from the end rather than by a fixed index.
      const [ownerWorkerId, reservation] = this.values.slice(-2);
      const kinds = this.values.slice(7, -2);
      const row = this.db.tasks.get(taskId);
      const owner = this.db.workers.get(ownerWorkerId);
      if (!row || row.dispatch_mode !== "worker_broker" || row.state !== "queued" ||
          row.callback_id || row.execution_started_at || (row.worker_id && row.lease_expires_at && row.lease_expires_at > now) ||
          (this.sql.includes("callback_reservation_id IS NULL") && row.callback_reservation_id) ||
          !kinds.includes(row.kind) ||
          owner?.state !== "active" || owner.current_task_id !== reservation) {
        return { meta: { changes: 0 } };
      }
      Object.assign(row, {
        worker_id: workerId,
        lease_id: leaseId,
        lease_expires_at: expiresAt,
        lease_started_at: startedAt,
        lease_accepted_at: null,
        attempt_count: row.attempt_count + 1,
        updated_at: updatedAt,
      });
      return { meta: { changes: 1 } };
    }
    if (this.sql.includes("SET worker_id = NULL, lease_id = NULL")) {
      const [_updatedAt, taskId, leaseId, workerId] = this.values;
      const row = this.db.tasks.get(taskId);
      if (!row || row.lease_id !== leaseId || row.worker_id !== workerId ||
          (this.sql.includes("callback_reservation_id IS NULL") && row.callback_reservation_id)) {
        return { meta: { changes: 0 } };
      }
      Object.assign(row, {
        worker_id: null,
        lease_id: null,
        lease_expires_at: null,
        lease_started_at: null,
        lease_accepted_at: null,
      });
      return { meta: { changes: 1 } };
    }
    if (this.sql.includes("UPDATE mac_workers SET current_task_id = ?")) {
      const [taskId, updatedAt, workerId] = this.values;
      const row = this.db.workers.get(workerId);
      if (!row || row.state !== "active") return { meta: { changes: 0 } };
      if (this.sql.includes("current_task_id IS NULL") && row.current_task_id !== null) {
        return { meta: { changes: 0 } };
      }
      if (this.sql.includes("AND current_task_id = ?") && row.current_task_id !== this.values[3]) {
        return { meta: { changes: 0 } };
      }
      Object.assign(row, { current_task_id: taskId, updated_at: updatedAt });
      return { meta: { changes: 1 } };
    }
    if (this.sql.includes("UPDATE mac_workers SET current_task_id = NULL")) {
      const [updatedAt, workerId, taskId] = this.values;
      const row = this.db.workers.get(workerId);
      if (!row || row.current_task_id !== taskId) return { meta: { changes: 0 } };
      Object.assign(row, { current_task_id: null, updated_at: updatedAt });
      return { meta: { changes: 1 } };
    }
    throw new Error(`unexpected run SQL: ${this.sql}`);
  }
}

class EnrollmentDb {
  constructor() {
    this.enrollments = new Map();
    this.workers = new Map();
  }

  prepare(sql) {
    return new EnrollmentStatement(this, sql);
  }
}

class EnrollmentStatement {
  constructor(db, sql, values = []) {
    this.db = db;
    this.sql = sql;
    this.values = values;
  }

  bind(...values) {
    return new EnrollmentStatement(this.db, this.sql, values);
  }

  async first() {
    if (this.sql.includes("FROM mac_worker_enrollments WHERE code_sha256")) {
      return [...this.db.enrollments.values()].find((row) => row.code_sha256 === this.values[0]) ?? null;
    }
    throw new Error(`unexpected enrollment first SQL: ${this.sql}`);
  }

  async run() {
    if (this.sql.includes("INSERT INTO mac_worker_enrollments")) {
      const [enrollmentId, codeDigest, displayName, pool, expiresAt, createdAt] = this.values;
      this.db.enrollments.set(enrollmentId, {
        enrollment_id: enrollmentId,
        code_sha256: codeDigest,
        display_name: displayName,
        pool,
        expires_at: expiresAt,
        created_at: createdAt,
        used_at: null,
        worker_id: null,
      });
      return { meta: { changes: 1 } };
    }
    if (this.sql.includes("UPDATE mac_worker_enrollments SET used_at = ?")) {
      const [usedAt, workerId, enrollmentId, now] = this.values;
      const row = this.db.enrollments.get(enrollmentId);
      if (!row || row.used_at || row.expires_at <= now) return { meta: { changes: 0 } };
      Object.assign(row, { used_at: usedAt, worker_id: workerId });
      return { meta: { changes: 1 } };
    }
    if (this.sql.includes("INSERT INTO mac_workers")) {
      const [workerId, displayName, pool, tokenDigest, capabilitiesJson, doctorJson,
        version, lastSeenAt, createdAt, updatedAt] = this.values;
      this.db.workers.set(workerId, {
        worker_id: workerId,
        display_name: displayName,
        pool,
        token_sha256: tokenDigest,
        capabilities_json: capabilitiesJson,
        doctor_json: doctorJson,
        version,
        last_seen_at: lastSeenAt,
        created_at: createdAt,
        updated_at: updatedAt,
        state: "active",
      });
      return { meta: { changes: 1 } };
    }
    if (this.sql.includes("UPDATE mac_worker_enrollments SET used_at = NULL")) {
      return { meta: { changes: 0 } };
    }
    throw new Error(`unexpected enrollment run SQL: ${this.sql}`);
  }

  async all() {
    if (this.sql.includes("FROM mac_workers ORDER BY created_at DESC")) {
      return { results: [...this.db.workers.values()] };
    }
    throw new Error(`unexpected enrollment all SQL: ${this.sql}`);
  }
}

class HeartbeatDb {
  constructor(workerRow, taskRow) {
    this.worker = { ...workerRow };
    this.task = { ...taskRow };
  }

  prepare(sql) {
    return new HeartbeatStatement(this, sql);
  }

  async batch(statements) {
    return Promise.all(statements.map((statement) => statement.run()));
  }
}

class HeartbeatStatement {
  constructor(db, sql, values = []) {
    this.db = db;
    this.sql = sql;
    this.values = values;
  }

  bind(...values) {
    return new HeartbeatStatement(this.db, this.sql, values);
  }

  async run() {
    if (this.sql.includes("UPDATE mac_workers SET capabilities_json")) {
      const [capabilitiesJson, doctorJson, version, lastSeenAt, updatedAt, workerId] = this.values;
      if (this.db.worker.worker_id !== workerId || this.db.worker.state === "revoked") {
        return { meta: { changes: 0 } };
      }
      Object.assign(this.db.worker, {
        capabilities_json: capabilitiesJson,
        doctor_json: doctorJson,
        version,
        last_seen_at: lastSeenAt,
        updated_at: updatedAt,
      });
      return { meta: { changes: 1 } };
    }
    if (this.sql.includes("UPDATE hosted_workspace_capture_tasks SET lease_expires_at")) {
      const [leaseExpiresAt, updatedAt, workerId, maximumStartedAt] = this.values;
      if (this.db.task.worker_id !== workerId || !this.db.task.lease_started_at ||
          this.db.task.lease_started_at <= maximumStartedAt) {
        return { meta: { changes: 0 } };
      }
      Object.assign(this.db.task, { lease_expires_at: leaseExpiresAt, updated_at: updatedAt });
      return { meta: { changes: 1 } };
    }
    throw new Error(`unexpected heartbeat SQL: ${this.sql}`);
  }
}

class CallbackReservationDb {
  constructor(taskRow) {
    this.task = { ...taskRow };
    this.worker = worker(taskRow.worker_id ?? "worker-1", { current_task_id: taskRow.task_id });
  }

  prepare(sql) {
    const db = this;
    return {
      values: [],
      bind(...values) { this.values = values; return this; },
      async run() {
        if (sql.includes("SET callback_reservation_id = ?")) {
          const [reservationId, reservedAt, resultDigest, updatedAt, taskId, workerId, leaseId,
            allowPreExecutionFailure] = this.values;
          if (db.task.task_id !== taskId || db.task.worker_id !== workerId ||
              db.task.lease_id !== leaseId || db.task.dispatch_mode !== "worker_broker" ||
              db.task.state !== "queued" || db.task.callback_id ||
              (!db.task.execution_started_at && allowPreExecutionFailure !== 1) ||
              db.task.callback_reservation_id) {
            return { meta: { changes: 0 } };
          }
          Object.assign(db.task, {
            callback_reservation_id: reservationId, callback_reserved_at: reservedAt,
            callback_result_sha256: resultDigest, updated_at: updatedAt,
          });
          return { meta: { changes: 1 } };
        }
        if (sql.includes("UPDATE mac_workers SET state = 'revoked'")) {
          if (db.task.callback_reservation_id && !db.task.callback_id) {
            return { meta: { changes: 0 } };
          }
          Object.assign(db.worker, { state: "revoked", token_sha256: null, current_task_id: null });
          return { meta: { changes: 1 } };
        }
        if (sql.includes("SET worker_id = NULL")) {
          if (db.task.callback_reservation_id) return { meta: { changes: 0 } };
          Object.assign(db.task, { worker_id: null, lease_id: null });
          return { meta: { changes: 1 } };
        }
        throw new Error("unexpected callback reservation run SQL");
      },
      async first() {
        if (sql.includes("SELECT current_task_id FROM mac_workers")) return db.worker;
        if (sql.includes("SELECT worker_id, lease_id, state, callback_id")) return db.task;
        throw new Error("unexpected callback reservation first SQL");
      },
    };
  }

  async batch(statements) {
    return Promise.all(statements.map((statement) => statement.run()));
  }
}

class CallbackCompletionDb {
  constructor() {
    this.worker = worker("worker-1", { current_task_id: "task-1" });
    this.task = task({
      worker_id: "worker-1",
      lease_id: "lease-1",
      lease_expires_at: "2026-08-26T00:15:00.000Z",
      lease_started_at: "2026-08-26T00:00:00.000Z",
    });
  }

  prepare(sql) {
    const db = this;
    return {
      values: [],
      bind(...values) { this.values = values; return this; },
      async first() {
        if (sql.includes("FROM mac_workers WHERE token_sha256")) return db.worker;
        throw new Error(`unexpected callback completion first SQL: ${sql}`);
      },
      async run() {
        if (sql.includes("UPDATE mac_workers SET current_task_id = NULL")) {
          const [_updatedAt, workerId, taskId] = this.values;
          if (db.worker.worker_id !== workerId || db.worker.current_task_id !== taskId) {
            return { meta: { changes: 0 } };
          }
          db.worker.current_task_id = null;
          return { meta: { changes: 1 } };
        }
        if (sql.includes("SET lease_expires_at = NULL")) {
          const [_updatedAt, taskId, workerId] = this.values;
          if (db.task.task_id !== taskId || db.task.worker_id !== workerId) {
            return { meta: { changes: 0 } };
          }
          db.task.lease_expires_at = null;
          db.task.lease_started_at = null;
          return { meta: { changes: 1 } };
        }
        throw new Error(`unexpected callback completion run SQL: ${sql}`);
      },
    };
  }

  async batch(statements) {
    return Promise.all(statements.map((statement) => statement.run()));
  }
}

test("reassignment wins before a stale pre-execution failure can reserve", async () => {
  const original = task({
    worker_id: "worker-1",
    lease_id: "lease-1",
  });
  const db = new CallbackReservationDb(original);
  db.task.worker_id = "worker-2";
  db.task.lease_id = "lease-2";

  await assert.rejects(
    reserveWorkerTaskCallback(
      db,
      worker("worker-1"),
      original,
      "task-1:completed",
      JSON.stringify({ status: "failed", failure_code: "native_appium_capture_failed" }),
    ),
    /worker no longer owns the callback lease/u,
  );

  assert.equal(db.task.callback_reservation_id, null);
});

test("a failed callback can terminate before Appium for the current worker lease", async () => {
  const current = task({ worker_id: "worker-1", lease_id: "lease-1" });
  const db = new CallbackReservationDb(current);

  const reserved = await reserveWorkerTaskCallback(
    db,
    worker("worker-1"),
    current,
    "task-1:completed",
    JSON.stringify({ status: "failed", failure_code: "native_appium_capture_failed" }),
  );

  assert.deepEqual(reserved, { duplicate: false, retry: false });
  assert.equal(db.task.callback_reservation_id, "task-1:completed");
});

for (const status of ["succeeded", "unknown_side_effect"]) {
  test(`a pre-execution ${status} callback cannot bypass the Appium barrier`, async () => {
    const current = task({ worker_id: "worker-1", lease_id: "lease-1" });
    const db = new CallbackReservationDb(current);

    await assert.rejects(
      reserveWorkerTaskCallback(
        db,
        worker("worker-1"),
        current,
        "task-1:completed",
        JSON.stringify({ status }),
      ),
      /worker no longer owns the callback lease or result/u,
    );

    assert.equal(db.task.callback_reservation_id, null);
  });
}

test("an accepted callback releases the worker assignment and renewable lease", async () => {
  const db = new CallbackCompletionDb();
  let received = null;
  const callback = {
    callback_id: "task-1:completed",
    task_id: "task-1",
    run_id: "run-1",
    account_id: "trace_demo_kr",
    kind: "capture",
    result: { status: "failed", failure_code: "codex_plan_failed" },
    completed_at: "2026-08-26T00:05:00.000Z",
  };

  const response = await handleMacWorkerRequest(
    new Request("https://workspace.example/v1/workers/task-callbacks", {
      method: "POST",
      headers: {
        authorization: "Bearer worker-secret",
        "content-type": "application/json",
      },
      body: JSON.stringify(callback),
    }),
    { DB: db },
    async (value) => {
      received = value;
      return { accepted: true, duplicate: false };
    },
  );

  assert.equal(response.status, 202);
  assert.deepEqual(received, callback);
  assert.equal(db.worker.current_task_id, null);
  assert.equal(db.task.lease_expires_at, null);
  assert.equal(db.task.lease_started_at, null);
});

test("a callback reservation wins before revocation can release the task", async () => {
  const current = task({
    worker_id: "worker-1",
    lease_id: "lease-1",
    execution_started_at: "2026-08-26T00:05:00.000Z",
  });
  const db = new CallbackReservationDb(current);

  const reserved = await reserveWorkerTaskCallback(
    db, worker("worker-1"), current, "task-1:completed", "{\"status\":\"succeeded\"}",
  );
  const releaseAllowed = db.task.callback_reservation_id === null;

  assert.deepEqual(reserved, { duplicate: false, retry: false });
  assert.equal(releaseAllowed, false);
  assert.equal(db.task.worker_id, "worker-1");
});

test("a reserved callback rejects changed results and accepts an identical partial retry", async () => {
  const current = task({
    worker_id: "worker-1", lease_id: "lease-1",
    execution_started_at: "2026-08-26T00:05:00.000Z",
  });
  const db = new CallbackReservationDb(current);
  const owner = worker("worker-1");

  await reserveWorkerTaskCallback(db, owner, current, "task-1:completed", "result-a");
  await assert.rejects(
    reserveWorkerTaskCallback(db, owner, current, "task-1:completed", "result-b"),
    /callback lease or result/u,
  );
  const retry = await reserveWorkerTaskCallback(
    db, owner, current, "task-1:completed", "result-a",
  );

  assert.deepEqual(retry, { duplicate: false, retry: true });
});

test("revocation defers credential invalidation until a reserved callback can retry", async () => {
  const current = task({
    worker_id: "worker-1", lease_id: "lease-1",
    execution_started_at: "2026-08-26T00:05:00.000Z",
  });
  const db = new CallbackReservationDb(current);
  const owner = worker("worker-1");
  await reserveWorkerTaskCallback(db, owner, current, "task-1:completed", "result-a");

  await assert.rejects(revokeWorker(db, "worker-1"), /callback application is in progress/u);
  const retry = await reserveWorkerTaskCallback(
    db, owner, current, "task-1:completed", "result-a",
  );

  assert.equal(db.worker.state, "active");
  assert.deepEqual(retry, { duplicate: false, retry: true });
});

test("hosted callback transport cannot cross broker and legacy ownership", () => {
  assert.throws(
    () => assertHostedCallbackTransport(task({ dispatch_mode: "worker_broker" }), null),
    /assigned worker callback/u,
  );
  assert.throws(
    () => assertHostedCallbackTransport(task({ dispatch_mode: "legacy_queue" }), worker("worker-1")),
    /does not accept a worker-scoped callback/u,
  );
  assert.doesNotThrow(
    () => assertHostedCallbackTransport(task({ dispatch_mode: "worker_broker" }), worker("worker-1")),
  );
});

test("a draining identity keeps new hosted captures on the broker transport", async () => {
  const rows = [worker("worker-draining", { state: "draining" })];
  const db = {
    prepare(sql) {
      return {
        bind() { return this; },
        async first() {
          if (sql.includes("state = 'active'")) {
            return rows.find((row) => row.state === "active") ?? null;
          }
          if (sql.includes("state != 'revoked'")) {
            return rows.find((row) => row.state !== "revoked") ?? null;
          }
          throw new Error("unexpected broker availability SQL: " + sql);
        },
      };
    },
  };

  assert.equal(await hasRegisteredBrokerWorker(db), true);
});

test("public worker status exposes aliases and availability without machine details", async () => {
  const now = new Date("2026-08-26T00:00:30.000Z");
  const rows = [
    worker("worker-secret-id", { display_name: "Studio Mac", current_task_id: "task-1" }),
    worker("worker-offline", {
      display_name: "Backup Mac",
      last_seen_at: "2026-08-25T23:58:00.000Z",
    }),
  ];
  const db = {
    prepare() {
      return { async all() { return { results: rows }; } };
    },
  };

  const status = await publicWorkerStatus(db, now);

  assert.equal(status.status, "ready");
  assert.deepEqual(status.counts, {
    registered: 2, online: 1, ready: 0, busy: 1, draining: 0, generation_ready: 1,
  });
  // The Macs are named; what each one can run is not, because nobody acts on that per Mac.
  assert.deepEqual(status.workers, [
    { display_name: "Studio Mac", pool: "appium", status: "busy" },
    { display_name: "Backup Mac", pool: "appium", status: "offline" },
  ]);
  assert.equal(JSON.stringify(status).includes("worker-secret-id"), false);
});

test("an online Mac that cannot write captions is counted as one that cannot", async () => {
  // Otherwise the card says the connected Mac takes caption work, and the person only finds
  // out otherwise by pressing the button.
  const now = new Date("2026-08-26T00:00:30.000Z");
  const rows = [
    worker("worker-1", {
      display_name: "Studio Mac",
      capabilities_json: '{"native_appium":true}',
    }),
  ];
  const db = { prepare() { return { async all() { return { results: rows }; } }; } };

  const status = await publicWorkerStatus(db, now);

  assert.equal(status.status, "ready");
  assert.equal(status.counts.ready, 1);
  assert.equal(status.counts.generation_ready, 0);
});

test("a degraded doctor result is visible without leaking its detailed checks", () => {
  const status = workerOperationalStatus(
    worker("worker-1", { doctor_json: '{"ready":false,"private":"not public"}' }),
    new Date("2026-08-26T00:00:30.000Z"),
  );

  assert.equal(status, "degraded");
});

test("heartbeat renews pre-execution work only within the one-hour claim cap", async () => {
  const now = new Date("2026-08-26T00:10:00.000Z");
  const workerRow = worker("worker-1", { current_task_id: "task-1" });
  const db = new HeartbeatDb(workerRow, task({
    worker_id: "worker-1",
    lease_id: "lease-1",
    lease_started_at: "2026-08-26T00:00:00.000Z",
    lease_accepted_at: "2026-08-26T00:00:00.000Z",
    lease_expires_at: "2026-08-26T00:11:00.000Z",
  }));

  await heartbeatWorker(
    db,
    workerRow,
    { version: "0.2.3", capabilities: {}, doctor: { ready: true, summary: "ready" } },
    now,
  );

  assert.equal(db.task.lease_expires_at, "2026-08-26T00:25:00.000Z");

  db.task.lease_started_at = "2026-08-25T23:00:00.000Z";
  const previousExpiry = db.task.lease_expires_at;
  await heartbeatWorker(
    db,
    workerRow,
    { version: "0.2.3", capabilities: {}, doctor: { ready: true, summary: "ready" } },
    now,
  );
  assert.equal(db.task.lease_expires_at, previousExpiry);
});

// A Mac enrolled before caption generation existed advertises nothing about task kinds.
const legacyWorker = (workerId) => worker(workerId, { capabilities_json: '{"native_appium":true}' });

test("a Mac that predates caption generation cannot lease one", async () => {
  // The failure this prevents is silent: the old Python does not recognise the job, the batch
  // dies inside it, and the task sits leased until expiry while the button says "만드는 중".
  const now = new Date("2026-08-26T00:00:30.000Z");
  const outdated = legacyWorker("worker-1");
  const db = new ClaimDb([outdated], [task({ kind: "generate_candidates" })]);

  const leases = await claimWorkerTasks(db, outdated, now);

  assert.deepEqual(leases, []);
  assert.equal(db.tasks.get("task-1").worker_id, null);
  assert.equal(db.tasks.get("task-1").attempt_count, 0);
  // And the worker is left free rather than holding a reservation for a task it never took.
  assert.equal(db.workers.get("worker-1").current_task_id, null);
});

test("a Mac that predates caption generation still leases image captures", async () => {
  const now = new Date("2026-08-26T00:00:30.000Z");
  const outdated = legacyWorker("worker-1");
  const db = new ClaimDb([outdated], [task()]);

  const leases = await claimWorkerTasks(db, outdated, now);

  assert.equal(leases.length, 1);
  assert.equal(db.tasks.get("task-1").worker_id, "worker-1");
});

test("an updated Mac leases either kind, oldest first", async () => {
  const now = new Date("2026-08-26T00:00:30.000Z");
  const updated = worker("worker-1");
  const db = new ClaimDb([updated], [task({ kind: "generate_candidates" })]);

  const leases = await claimWorkerTasks(db, updated, now);

  assert.equal(leases.length, 1);
  assert.equal(leases[0].message_id, "task-1");
});

test("a caption batch waits for the updated Mac rather than stalling on the old one", async () => {
  // Both Macs poll. The old one must skip the generation task and take the capture instead,
  // which is what keeps one un-updated Mac from blocking the queue for everyone.
  const now = new Date("2026-08-26T00:00:30.000Z");
  const outdated = legacyWorker("worker-1");
  const updated = worker("worker-2");
  const db = new ClaimDb([outdated, updated], [
    task({ task_id: "task-generate", kind: "generate_candidates", created_at: "2026-08-26T00:00:00.000Z" }),
    task({ task_id: "task-capture", created_at: "2026-08-26T00:00:10.000Z" }),
  ]);

  const outdatedLeases = await claimWorkerTasks(db, outdated, now);
  const updatedLeases = await claimWorkerTasks(db, updated, now);

  assert.deepEqual(outdatedLeases.map((lease) => lease.message_id), ["task-capture"]);
  assert.deepEqual(updatedLeases.map((lease) => lease.message_id), ["task-generate"]);
});

test("an advertisement is read as the closed set it is, and silence as capture only", () => {
  // The value is a comma-joined string because the control plane flattens every non-scalar
  // capability to null; a worker that sent a list would read as having said nothing.
  assert.deepEqual(workerTaskKinds({ task_kinds: "capture,generate_candidates" }),
    ["capture", "generate_candidates"]);
  assert.deepEqual(workerTaskKinds({ task_kinds: " generate_candidates , capture " }),
    ["generate_candidates", "capture"]);
  // Tokens this control plane does not define are dropped rather than trusted.
  assert.deepEqual(workerTaskKinds({ task_kinds: "capture,publish" }), ["capture"]);
  assert.deepEqual(workerTaskKinds({ task_kinds: "publish" }), ["capture"]);
  // Every shape a worker from before the field can produce reads as capture-only.
  assert.deepEqual(workerTaskKinds({ native_appium: true }), ["capture"]);
  assert.deepEqual(workerTaskKinds({ task_kinds: null }), ["capture"]);
  assert.deepEqual(workerTaskKinds({}), ["capture"]);
  assert.deepEqual(workerTaskKinds(null), ["capture"]);
});

test("two workers racing for one task produce exactly one lease owner", async () => {
  const now = new Date("2026-08-26T00:00:30.000Z");
  const first = worker("worker-1");
  const second = worker("worker-2");
  const db = new ClaimDb([first, second], [task()]);

  const [firstLeases, secondLeases] = await Promise.all([
    claimWorkerTasks(db, first, now),
    claimWorkerTasks(db, second, now),
  ]);

  assert.equal(firstLeases.length + secondLeases.length, 1);
  assert.equal(db.tasks.get("task-1").attempt_count, 1);
  assert.ok(["worker-1", "worker-2"].includes(db.tasks.get("task-1").worker_id));
});

test("an expired task lease can move to a replacement worker", async () => {
  const now = new Date("2026-08-26T00:10:00.000Z");
  const replacement = worker("worker-2");
  const db = new ClaimDb(
    [replacement],
    [task({
      worker_id: "worker-1",
      lease_id: "expired-lease",
      lease_expires_at: "2026-08-26T00:09:00.000Z",
      attempt_count: 1,
    })],
  );

  const leases = await claimWorkerTasks(db, replacement, now);

  assert.equal(leases.length, 1);
  assert.equal(db.tasks.get("task-1").worker_id, "worker-2");
  assert.equal(db.tasks.get("task-1").attempt_count, 2);
});

test("an execution barrier prevents automatic reassignment after Appium starts", async () => {
  const startedAt = new Date("2026-08-26T00:05:00.000Z");
  const owner = worker("worker-1", { current_task_id: "task-1" });
  const replacement = worker("worker-2");
  const db = new ClaimDb(
    [owner, replacement],
    [task({
      worker_id: "worker-1",
      lease_id: "lease-1",
      lease_expires_at: "2026-08-26T00:10:00.000Z",
      lease_started_at: "2026-08-26T00:00:00.000Z",
      lease_accepted_at: "2026-08-26T00:00:30.000Z",
      attempt_count: 1,
    })],
  );

  const barrier = await markWorkerTaskExecuting(db, owner, "task-1", startedAt);
  const duplicate = await markWorkerTaskExecuting(db, owner, "task-1", startedAt);
  const leases = await claimWorkerTasks(
    db,
    replacement,
    new Date("2026-08-26T02:00:00.000Z"),
  );

  assert.deepEqual(barrier, { accepted: true, duplicate: false });
  assert.deepEqual(duplicate, { accepted: true, duplicate: true });
  assert.equal(db.tasks.get("task-1").execution_started_at, startedAt.toISOString());
  assert.equal(db.tasks.get("task-1").lease_expires_at, null);
  assert.deepEqual(leases, []);
  assert.equal(db.tasks.get("task-1").worker_id, "worker-1");
  assert.equal(db.tasks.get("task-1").attempt_count, 1);
});

test("a callback reservation prevents expired pre-execution reassignment", async () => {
  const replacement = worker("worker-2");
  const db = new ClaimDb(
    [replacement],
    [task({
      worker_id: "worker-1",
      lease_id: "lease-1",
      lease_expires_at: "2026-08-26T00:09:00.000Z",
      callback_reservation_id: "task-1:completed",
      callback_result_sha256: "a".repeat(64),
    })],
  );

  const leases = await claimWorkerTasks(db, replacement, new Date("2026-08-26T00:10:00.000Z"));

  assert.deepEqual(leases, []);
  assert.equal(db.tasks.get("task-1").worker_id, "worker-1");
  assert.equal(db.tasks.get("task-1").lease_id, "lease-1");
});

test("a callback reservation keeps its worker busy after lease expiry", async () => {
  const owner = worker("worker-1", { current_task_id: "task-1" });
  const db = new ClaimDb(
    [owner],
    [task({
      worker_id: "worker-1",
      lease_id: "lease-1",
      lease_expires_at: "2026-08-26T00:09:00.000Z",
      callback_reservation_id: "task-1:completed",
      callback_result_sha256: "a".repeat(64),
    })],
  );

  const leases = await claimWorkerTasks(db, owner, new Date("2026-08-26T00:10:00.000Z"));

  assert.deepEqual(leases, []);
  assert.equal(db.workers.get("worker-1").current_task_id, "task-1");
});

test("a callback reservation rejects retry release and late execution start", async () => {
  const owner = worker("worker-1", { current_task_id: "task-1" });
  const db = new ClaimDb(
    [owner],
    [task({
      worker_id: "worker-1",
      lease_id: "lease-1",
      lease_expires_at: "2099-08-26T00:15:00.000Z",
      lease_accepted_at: "2026-08-26T00:00:30.000Z",
      callback_reservation_id: "task-1:completed",
      callback_result_sha256: "a".repeat(64),
    })],
  );

  const retryResponse = await handleMacWorkerRequest(
    new Request("https://workspace.example/v1/workers/tasks/ack", {
      method: "POST",
      headers: {
        authorization: "Bearer worker-secret",
        "content-type": "application/json",
      },
      body: JSON.stringify({ retries: ["lease-1"] }),
    }),
    { DB: db },
    () => { throw new Error("unexpected callback"); },
  );

  assert.equal(retryResponse.status, 409);
  await assert.rejects(
    markWorkerTaskExecuting(db, owner, "task-1", new Date("2026-08-26T00:05:00.000Z")),
    /task is not ready for native execution/u,
  );
  assert.equal(db.tasks.get("task-1").worker_id, "worker-1");
  assert.equal(db.tasks.get("task-1").lease_id, "lease-1");
  assert.equal(db.tasks.get("task-1").execution_started_at, null);
});

test("an unreserved acknowledgement retry still releases pre-execution work", async () => {
  const owner = worker("worker-1", { current_task_id: "task-1" });
  const db = new ClaimDb(
    [owner],
    [task({
      worker_id: "worker-1",
      lease_id: "lease-1",
      lease_expires_at: "2099-08-26T00:15:00.000Z",
      lease_accepted_at: "2026-08-26T00:00:30.000Z",
    })],
  );

  const response = await handleMacWorkerRequest(
    new Request("https://workspace.example/v1/workers/tasks/ack", {
      method: "POST",
      headers: {
        authorization: "Bearer worker-secret",
        "content-type": "application/json",
      },
      body: JSON.stringify({ retries: ["lease-1"] }),
    }),
    { DB: db },
    () => { throw new Error("unexpected callback"); },
  );

  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { accepted: 0, retried: 1 });
  assert.equal(db.tasks.get("task-1").worker_id, null);
  assert.equal(db.tasks.get("task-1").lease_id, null);
  assert.equal(db.workers.get("worker-1").current_task_id, null);
});

test("one worker identity cannot concurrently claim two tasks", async () => {
  const now = new Date("2026-08-26T00:00:30.000Z");
  const onlyWorker = worker("worker-1");
  const db = new ClaimDb(
    [onlyWorker],
    [task(), task({ task_id: "task-2", task_json: task().task_json.replaceAll("task-1", "task-2") })],
  );

  const [first, second] = await Promise.all([
    claimWorkerTasks(db, onlyWorker, now),
    claimWorkerTasks(db, onlyWorker, now),
  ]);

  assert.equal(first.length + second.length, 1);
  assert.equal([...db.tasks.values()].filter((row) => row.worker_id === "worker-1").length, 1);
});

test("one-time enrollment stores only hashes and cannot be replayed", async () => {
  const db = new EnrollmentDb();
  const env = { DB: db, CONTROL_PLANE_TOKEN: "admin-secret" };
  const createdResponse = await handleMacWorkerRequest(
    new Request("https://workspace.example/v1/worker-enrollments", {
      method: "POST",
      headers: {
        authorization: "Bearer admin-secret",
        "content-type": "application/json",
      },
      body: JSON.stringify({ display_name: "Studio Mac", pool: "appium", ttl_seconds: 600 }),
    }),
    env,
    () => { throw new Error("unexpected callback"); },
  );
  const created = await createdResponse.json();
  const enrollmentRequest = () => new Request("https://workspace.example/v1/workers/enroll", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      enrollment_code: created.enrollment_code,
      version: "0.2.3",
      capabilities: { native_appium: true },
      doctor: { ready: true, summary: "ready" },
    }),
  });

  const enrolledResponse = await handleMacWorkerRequest(
    enrollmentRequest(),
    env,
    () => { throw new Error("unexpected callback"); },
  );
  const enrolled = await enrolledResponse.json();
  const replayResponse = await handleMacWorkerRequest(
    enrollmentRequest(),
    env,
    () => { throw new Error("unexpected callback"); },
  );
  const inventoryResponse = await handleMacWorkerRequest(
    new Request("https://workspace.example/v1/workers", {
      headers: { authorization: "Bearer admin-secret" },
    }),
    env,
    () => { throw new Error("unexpected callback"); },
  );
  const unauthorizedInventory = await handleMacWorkerRequest(
    new Request("https://workspace.example/v1/workers"),
    env,
    () => { throw new Error("unexpected callback"); },
  );
  const inventory = await inventoryResponse.json();

  assert.equal(createdResponse.status, 201);
  assert.equal(enrolledResponse.status, 201);
  assert.equal(replayResponse.status, 401);
  assert.equal(inventoryResponse.status, 200);
  assert.equal(unauthorizedInventory.status, 401);
  assert.equal(inventory.workers[0].status, "ready");
  assert.deepEqual(inventory.workers[0].doctor, { ready: true, summary: "ready" });
  assert.equal(enrolled.display_name, "Studio Mac");
  assert.ok(enrolled.worker_token.startsWith("trace-worker_"));
  const persisted = JSON.stringify({
    enrollments: [...db.enrollments.values()],
    workers: [...db.workers.values()],
  });
  assert.equal(persisted.includes(created.enrollment_code), false);
  assert.equal(persisted.includes(enrolled.worker_token), false);
  assert.match([...db.workers.values()][0].token_sha256, /^[0-9a-f]{64}$/);
});
