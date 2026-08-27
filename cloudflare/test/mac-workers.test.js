import assert from "node:assert/strict";
import test from "node:test";

import {
  claimWorkerTasks,
  handleMacWorkerRequest,
  hasRegisteredBrokerWorker,
  heartbeatWorker,
  publicWorkerStatus,
  workerOperationalStatus,
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
    callback_id: null,
    worker_id: null,
    lease_id: null,
    lease_expires_at: null,
    lease_started_at: null,
    lease_accepted_at: null,
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
    if (this.sql.includes("SELECT current_task_id FROM mac_workers")) {
      return this.db.workers.get(this.values[0]) ?? null;
    }
    if (this.sql.includes("WHERE worker_id = ?") && this.sql.includes("lease_expires_at > ?")) {
      const [workerId, now] = this.values;
      return [...this.db.tasks.values()].find((row) =>
        row.worker_id === workerId && row.dispatch_mode === "worker_broker" &&
        row.state === "queued" && !row.callback_id && row.lease_expires_at > now
      ) ?? null;
    }
    if (this.sql.includes("SELECT task_id FROM hosted_workspace_capture_tasks") &&
        this.sql.includes("worker_id IS NULL")) {
      const [now] = this.values;
      return [...this.db.tasks.values()].find((row) =>
        row.dispatch_mode === "worker_broker" && row.state === "queued" && !row.callback_id &&
        (!row.worker_id || !row.lease_expires_at || row.lease_expires_at <= now)
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
        !row.callback_id && row.lease_expires_at > now ? row : null;
    }
    throw new Error(`unexpected first SQL: ${this.sql}`);
  }

  async run() {
    if (this.sql.includes("SET worker_id = ?, lease_id = ?")) {
      const [workerId, leaseId, expiresAt, startedAt, updatedAt, taskId, now,
        ownerWorkerId, reservation] = this.values;
      const row = this.db.tasks.get(taskId);
      const owner = this.db.workers.get(ownerWorkerId);
      if (!row || row.dispatch_mode !== "worker_broker" || row.state !== "queued" ||
          row.callback_id || (row.worker_id && row.lease_expires_at && row.lease_expires_at > now) ||
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
  assert.deepEqual(status.counts, { registered: 2, online: 1, ready: 0, busy: 1, draining: 0 });
  assert.deepEqual(status.workers, [
    { display_name: "Studio Mac", pool: "appium", status: "busy" },
    { display_name: "Backup Mac", pool: "appium", status: "offline" },
  ]);
  assert.equal(JSON.stringify(status).includes("worker-secret-id"), false);
});

test("a degraded doctor result is visible without leaking its detailed checks", () => {
  const status = workerOperationalStatus(
    worker("worker-1", { doctor_json: '{"ready":false,"private":"not public"}' }),
    new Date("2026-08-26T00:00:30.000Z"),
  );

  assert.equal(status, "degraded");
});

test("heartbeat renews a live claim while leaving a one-hour execution cap", async () => {
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
