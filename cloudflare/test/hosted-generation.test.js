// Caption generation delegated to a Mac worker: the route that publishes the job, and the
// callback that brings the candidates back.
//
// The hosted surface used to write captions inline with Workers AI, from a prompt that read
// none of the reference corpus. The generator the team tunes is the Python one, so the
// hosted surface now publishes the same kind of job it already publishes for image capture
// and a Mac runs that generator. These tests hold both ends of that round trip.
import assert from "node:assert/strict";
import test from "node:test";

import {
  applyHostedGenerationResult,
  handleHostedWorkspace,
  HOSTED_GENERATION_PIPELINE,
  WORKER_GENERATION_PROMPT_VERSION,
} from "../src/hosted-workspace.js";
import { receiveHostedGenerationCallback } from "../src/hosted-generation-callback.js";

const ACCOUNT_ID = "trace_demo_kr";
// What an updated worker advertises, and what one that predates the field says instead.
const CAPABLE = { task_kinds: "capture,generate_candidates", feedback_context_v1: true };
const LEGACY = { native_appium: true };
const REGISTRY = { global: "전역", countries: { KR: "한국", JP: "일본" } };

const account = () => ({
  account_id: ACCOUNT_ID,
  display_name: "Trace Korea",
  country: "KR",
  language: "ko",
  timezone: "Asia/Seoul",
  morning_time: "07:30",
  evening_time: "19:30",
  generation_enabled: 1,
  next_generation_at: "2026-08-27T22:30:00.000Z",
  enabled: 1,
  revision: 1,
});

const contextProfileRow = () => ({
  account_id: ACCOUNT_ID,
  profile_id: "profile-1",
  country: "KR",
  name: "학생의 실제 하루",
  persona_id: "kr_student",
  audience: "한국 대학생",
  situation: "시험 주간",
  tone: "담백한 한국어",
  guidance: "실제 하루 장면을 보여준다.",
  reference_ids_json: JSON.stringify(["kr-study-day"]),
  source: "custom",
  is_default: 1,
  enabled: 1,
  revision: 1,
});

const personaIdentity = () => ({
  display_name: "이서진",
  age: 27,
  region: "서울 마포구",
  occupation: "병동 간호사",
  concept: "3교대를 잠금화면 일정으로 버티는 간호사",
  domain: "office_worker",
  interests: ["쿠로미", "필라테스"],
  life_rhythm: "데이 출근일 5시 40분 기상",
  taste: {
    background_subject: "character_other",
    background_mood: "파스텔 톤의 캐릭터 배경",
    font: "sf_pro_rounded",
  },
});

const personaRow = (accountId = "persona-1", country = "KR") => ({
  workspace_id: `cloudflare:${ACCOUNT_ID}`,
  account_id: accountId,
  country,
  identity_json: JSON.stringify(personaIdentity()),
  schedule_json: JSON.stringify({
    language: country === "JP" ? "ja" : "ko",
    timezone: "Asia/Seoul",
    morning_time: "08:00",
    evening_time: "20:00",
    generation_enabled: false,
  }),
  status: "observing",
  note: "",
  revision: 1,
  created_at: 1,
  updated_at: 1,
});

/** One draft in the shape the Mac worker's executor emits. */
const workerDraft = (overrides = {}) => ({
  topic: "야간 근무 전날 밤",
  country: "KR",
  posting_slot: "evening",
  persona_domain: "office_worker",
  caption: "내일 나이트라 오늘은 일찍 눕는다.",
  hypothesis: "교대 근무의 하루 리듬이 공감을 만든다.",
  refs_used: ["kr-001"],
  principles_applied: [3],
  appium_prompt: "",
  image_inputs: {
    trace_items: ["05:40 기상", "07:00 출근", "20:00 인계", "23:00 취침"],
    device_time: "22:40",
    background_subject: "character_other",
    background_mood: "파스텔 톤의 캐릭터 배경",
    background_search_query: "쿠로미 배경화면",
    language: "ko",
  },
  provenance: {
    documents: [
      { relative_path: "core/PRINCIPLES-GLOBAL.md", size_bytes: 1200 },
      { relative_path: "references/KR/INDEX.md", size_bytes: 3400 },
    ],
    model: "gpt-5.6-codex",
    instruction_chars: 18_240,
    generated_at: 1_756_000_000,
    assigned_domains: ["office_worker"],
    reference_ids: ["kr-001", "kr-014"],
    caption_form: "daily",
  },
  ...overrides,
});

/**
 * A D1 stand-in that knows the statements this round trip issues and keeps the rows.
 *
 * Small on purpose: it recognizes the tasks table, the candidates table, the persona table
 * and the two single-row lookups, and it fails loudly on anything else so a statement that
 * quietly changes shape is a test failure rather than a silent `null`.
 */
function environment(options = {}) {
  const tasks = options.tasks ? options.tasks.map((row) => ({ ...row })) : [];
  const candidates = [];
  const personas = options.personas ? options.personas.map((row) => ({ ...row })) : [];
  const locks = new Map();
  // Each entry is one non-revoked worker's advertised capabilities. `[{}]` is a Mac from
  // before caption generation existed: registered, online, and capture-only.
  const workers = options.workers ?? [];
  const matches = (sql, ...fragments) => fragments.every((fragment) => sql.includes(fragment));
  const DB = {
    prepare(sql) {
      return {
        bind(...values) {
          return {
            sql,
            values,
            async first() {
              if (matches(sql, "SELECT worker_id FROM mac_workers")) {
                return workers.length ? { worker_id: "worker-1" } : null;
              }
              if (matches(sql, "FROM hosted_workspace_capture_tasks", "kind = 'generate_candidates'")) {
                const [accountId, personaId] = values;
                return tasks.find((row) => row.account_id === accountId
                  && row.kind === "generate_candidates" && row.state === "queued"
                  && !row.callback_id && (row.persona_id ?? null) === (personaId ?? null)) ?? null;
              }
              if (matches(sql, "FROM hosted_workspace_capture_tasks", "WHERE task_id = ?")) {
                return tasks.find((row) => row.task_id === values[0]) ?? null;
              }
              if (matches(sql, "SELECT * FROM hosted_workspace_accounts")) return account();
              if (matches(sql, "SELECT * FROM hosted_workspace_context_profiles")) {
                return contextProfileRow();
              }
              if (matches(sql, "SELECT * FROM hosted_marketing_personas")) {
                return personas.find((row) => row.account_id === values[1]) ?? null;
              }
              if (matches(sql, "generation_batch_id = ?")) {
                const [accountId, batchId] = values;
                return candidates.find((row) => row.account_id === accountId
                  && row.generation_batch_id === batchId) ?? null;
              }
              if (matches(sql, "SELECT * FROM hosted_workspace_candidates")) {
                return candidates.find((row) => row.account_id === values[0]
                  && row.candidate_id === values[1]) ?? null;
              }
              if (matches(sql, "SELECT callback_id, result_json")) {
                return tasks.find((row) => row.task_id === values[0]) ?? null;
              }
              throw new Error(`unexpected first SQL: ${sql}`);
            },
            async all() {
              if (matches(sql, "SELECT capabilities_json FROM mac_workers")) {
                return {
                  results: workers.map((capabilities) => ({
                    capabilities_json: JSON.stringify(capabilities),
                  })),
                };
              }
              if (matches(sql, "FROM hosted_workspace_feedback_events")) {
                return { results: options.feedbackRows ?? [] };
              }
              if (matches(sql, "FROM hosted_workspace_feedback_rule_overrides")) {
                return { results: options.feedbackOverrides ?? [] };
              }
              if (matches(sql, "FROM hosted_workspace_capture_tasks", "kind = 'generate_candidates'")) {
                const scoped = sql.includes("AND persona_id = ?")
                  ? tasks.filter((row) => row.account_id === values[0]
                    && row.persona_id === values[1])
                  : tasks.filter((row) => row.account_id === values[0]);
                return { results: scoped.filter((row) => row.kind === "generate_candidates") };
              }
              if (matches(sql, "SELECT topic FROM hosted_workspace_candidates")) {
                const scoped = sql.includes("AND persona_id = ?")
                  ? candidates.filter((row) => row.account_id === values[0]
                    && row.persona_id === values[1])
                  : candidates.filter((row) => row.account_id === values[0] && !row.persona_id);
                const limit = values[values.length - 1];
                return {
                  results: [...scoped]
                    .sort((left, right) => right.created_at - left.created_at)
                    .slice(0, limit)
                    .map((row) => ({ topic: row.topic })),
                };
              }
              throw new Error(`unexpected all SQL: ${sql}`);
            },
            async run() {
              if (matches(sql, "INSERT OR IGNORE INTO hosted_workspace_accounts")) {
                return { meta: { changes: 0 } };
              }
              if (matches(sql, "hosted_workspace_generation_locks")) {
                const [lockId, startedAt] = values;
                if (locks.has(lockId)) return { meta: { changes: 0 } };
                locks.set(lockId, startedAt);
                return { meta: { changes: 1 } };
              }
              if (matches(sql, "INSERT INTO hosted_workspace_capture_tasks")) {
                if (workers.length === 0) return { meta: { changes: 0 } };
                const [taskId, runId, accountId, idempotencyKey, taskJson, personaId,
                  requiredCapability, createdAt] =
                  values;
                tasks.push({
                  task_id: taskId,
                  run_id: runId,
                  account_id: accountId,
                  candidate_id: "",
                  candidate_revision: 1,
                  idempotency_key: idempotencyKey,
                  task_json: taskJson,
                  state: "queued",
                  dispatch_mode: "worker_broker",
                  kind: "generate_candidates",
                  persona_id: personaId,
                  required_capability: requiredCapability,
                  worker_id: "worker-1",
                  lease_id: "lease-1",
                  execution_started_at: createdAt,
                  callback_reservation_id: null,
                  callback_id: null,
                  result_json: null,
                  created_at: createdAt,
                  updated_at: createdAt,
                });
                return { meta: { changes: 1 } };
              }
              if (matches(sql, "UPDATE hosted_workspace_capture_tasks", "SET callback_reservation_id = ?")) {
                const [reservationId, , , , taskId] = values;
                const row = tasks.find((entry) => entry.task_id === taskId);
                if (!row || row.callback_reservation_id) return { meta: { changes: 0 } };
                row.callback_reservation_id = reservationId;
                return { meta: { changes: 1 } };
              }
              if (matches(sql, "UPDATE hosted_workspace_capture_tasks", "SET state = ?")) {
                const [state, resultJson, callbackId, updatedAt, taskId] = values;
                const row = tasks.find((entry) => entry.task_id === taskId);
                if (!row || row.callback_id) return { meta: { changes: 0 } };
                Object.assign(row, {
                  state,
                  result_json: resultJson,
                  callback_id: callbackId,
                  updated_at: updatedAt,
                });
                return { meta: { changes: 1 } };
              }
              throw new Error(`unexpected run SQL: ${sql}`);
            },
          };
        },
      };
    },
    async batch(statements) {
      const results = [];
      for (const statement of statements) {
        if (!statement.sql.includes("INSERT INTO hosted_workspace_candidates")) {
          throw new Error(`unexpected batch SQL: ${statement.sql}`);
        }
        const values = statement.values;
        candidates.push({
          candidate_id: values[0],
          account_id: values[1],
          source: values[2],
          country: values[3],
          topic: values[4],
          caption: values[5],
          hypothesis: values[6],
          refs_json: values[7],
          principles_json: values[8],
          appium_prompt: values[9],
          image_inputs_json: values[10],
          ai_verdict: values[11],
          context_profile_id: values[12],
          context_snapshot_json: values[13],
          posting_slot: values[14],
          generation_batch_id: values[15],
          generation_prompt_version: values[16],
          generation_prompt_sha256: values[17],
          generation_model: values[18],
          feedback_rules_json: values[19],
          persona_id: values[20],
          generation_provenance_json: values[21],
          status: "awaiting_review",
          review_note: null,
          revision: 1,
          created_at: values[22],
          updated_at: values[23],
        });
        results.push({ meta: { changes: 1 } });
      }
      return results;
    },
  };
  return { env: { DB, PUBLIC_WORKSPACE_ACCOUNT_ID: ACCOUNT_ID }, tasks, candidates, locks };
}

const generateRequest = (personaId = null) =>
  new Request("https://workspace.example/api/candidates/generate", {
    method: "POST",
    headers: personaId
      ? { "Content-Type": "application/json", "X-Trace-Persona-ID": personaId }
      : { "Content-Type": "application/json" },
    body: JSON.stringify({ context_profile_id: "profile-1" }),
  });

const callbackFor = (task, result) => {
  const expectedFeedback = JSON.parse(task.task_json).payload.feedback_context_sha256;
  const received = result.status === "succeeded"
    ? { ...result, output: { ...result.output, feedback_application_sha256: expectedFeedback } }
    : result;
  return ({
  schema_version: "1",
  callback_id: `${task.task_id}:completed`,
  task_id: task.task_id,
  run_id: task.run_id,
  account_id: task.account_id,
  kind: "generate_candidates",
  result: received,
  completed_at: "2026-08-28T00:00:00.000Z",
  });
};

test("a published batch carries what this persona has already been given", async () => {
  // The worker reads no database, so anything the generator has to avoid repeating has to
  // travel with the job. Without this the same four posts come back every week.
  const state = environment({ workers: [CAPABLE], personas: [personaRow()] });
  state.candidates.push(
    { account_id: ACCOUNT_ID, persona_id: "persona-1", topic: "야간 근무 전날 밤", created_at: 3 },
    { account_id: ACCOUNT_ID, persona_id: "persona-1", topic: "퇴근 뒤 필라테스", created_at: 2 },
    { account_id: ACCOUNT_ID, persona_id: "persona-2", topic: "다른 사람의 주제", created_at: 5 },
  );

  await handleHostedWorkspace(generateRequest("persona-1"), state.env, REGISTRY);

  // Newest first, this persona's only.
  const published = JSON.parse(state.tasks[0].task_json);
  assert.deepEqual(published.payload.recent_topics, ["야간 근무 전날 밤", "퇴근 뒤 필라테스"]);
});

test("a persona with no back catalogue publishes an empty recent list", async () => {
  const state = environment({ workers: [CAPABLE], personas: [personaRow()] });

  await handleHostedWorkspace(generateRequest("persona-1"), state.env, REGISTRY);

  const published = JSON.parse(state.tasks[0].task_json);
  assert.deepEqual(published.payload.recent_topics, []);
});

test("the generate route publishes a worker job and answers before it is written", async () => {
  const state = environment({ workers: [CAPABLE], personas: [personaRow()] });

  const response = await handleHostedWorkspace(generateRequest("persona-1"), state.env, REGISTRY);

  // 202, not 201: nothing has been created yet, and the browser has to wait for the Mac.
  assert.equal(response.status, 202);
  const receipt = await response.json();
  assert.equal(receipt.state, "queued");
  assert.equal(receipt.persona_id, "persona-1");
  assert.equal(receipt.count, 4);
  assert.equal(state.tasks.length, 1);

  const published = JSON.parse(state.tasks[0].task_json);
  assert.equal(published.kind, "generate_candidates");
  assert.equal(published.payload.pipeline, HOSTED_GENERATION_PIPELINE);
  assert.equal(published.payload.country, "KR");
  assert.equal(published.payload.count, 4);
  assert.equal(published.payload.context_profile_id, "profile-1");
  // The whole identity travels, because the worker rebuilds the generation brief from it.
  assert.equal(published.payload.persona.display_name, "이서진");
  assert.equal(published.payload.persona.domain, "office_worker");
  assert.equal(published.payload.persona.taste.background_subject, "character_other");
  // And the row carries the persona, so the callback knows whose list the candidates join.
  assert.equal(state.tasks[0].persona_id, "persona-1");
  assert.equal(state.tasks[0].kind, "generate_candidates");
  assert.equal(state.tasks[0].dispatch_mode, "worker_broker");
  assert.equal(state.tasks[0].required_capability, "feedback_context_v1");
});

test("promoted caption feedback is bound to the published batch by digest", async () => {
  const feedbackRows = [1, 2, 3].map((index) => ({
    candidate_id: `reviewed-${index}`,
    candidate_revision: index,
    tags_json: JSON.stringify(["컨셉이 약함"]),
    rating: 2,
    stage: "caption",
    created_at: 100 - index,
  }));
  const state = environment({
    workers: [CAPABLE],
    personas: [personaRow()],
    feedbackRows,
  });

  const response = await handleHostedWorkspace(generateRequest("persona-1"), state.env, REGISTRY);

  assert.equal(response.status, 202);
  const payload = JSON.parse(state.tasks[0].task_json).payload;
  assert.equal(payload.feedback_context.schema_version, "trace.feedback-context.v1");
  assert.equal(payload.feedback_context.stage, "caption");
  assert.equal(payload.feedback_context.scope.context_profile_id, "profile-1");
  assert.equal(payload.feedback_context.rules[0].rule_id, "caption-concept-specificity");
  assert.match(payload.feedback_context_sha256, /^[a-f0-9]{64}$/u);
});

test("a persona's country decides the corpus the worker is asked to read", async () => {
  const state = environment({
    workers: [CAPABLE],
    personas: [personaRow("persona-jp", "JP")],
  });

  const response = await handleHostedWorkspace(generateRequest("persona-jp"), state.env, REGISTRY);

  assert.equal(response.status, 202);
  assert.equal(JSON.parse(state.tasks[0].task_json).payload.country, "JP");
  assert.equal(JSON.parse(state.tasks[0].task_json).payload.language, "ja");
});

test("with no Mac worker the generate route refuses instead of writing captions itself", async () => {
  // The Workers AI fallback is exactly what this change removes: a second generator that
  // reads none of the corpus is worse than telling the operator to connect a Mac.
  const state = environment({ workers: [], personas: [personaRow()] });

  const response = await handleHostedWorkspace(generateRequest("persona-1"), state.env, REGISTRY);

  assert.equal(response.status, 503);
  assert.match((await response.json()).detail, /Mac 워커가 없어 후보를 만들 수 없습니다/u);
  assert.equal(state.tasks.length, 0);
});

test("a Mac that predates caption generation is told to update rather than left silent", async () => {
  // The window between deploying the Worker and a Mac updating itself is minutes, and a
  // button pressed inside it used to publish a batch that Mac would take and not understand.
  const state = environment({ workers: [LEGACY], personas: [personaRow()] });

  const response = await handleHostedWorkspace(generateRequest("persona-1"), state.env, REGISTRY);

  assert.equal(response.status, 503);
  assert.match((await response.json()).detail, /Mac 워커를 업데이트해 주세요/u);
  // Different sentence from "no Mac at all", because it is a different thing to fix.
  assert.doesNotMatch((await (await handleHostedWorkspace(
    generateRequest("persona-1"), environment({ workers: [], personas: [personaRow()] }).env, REGISTRY,
  )).json()).detail, /업데이트/u);
  assert.equal(state.tasks.length, 0);
});

test("one updated Mac among older ones is enough to publish", async () => {
  const state = environment({ workers: [LEGACY, CAPABLE], personas: [personaRow()] });

  const response = await handleHostedWorkspace(generateRequest("persona-1"), state.env, REGISTRY);

  assert.equal(response.status, 202);
  assert.equal(state.tasks.length, 1);
});

test("a persona with a batch already on a Mac is refused a second one", async () => {
  const state = environment({ workers: [CAPABLE], personas: [personaRow()] });

  const first = await handleHostedWorkspace(generateRequest("persona-1"), state.env, REGISTRY);
  const second = await handleHostedWorkspace(generateRequest("persona-1"), state.env, REGISTRY);

  assert.equal(first.status, 202);
  assert.equal(second.status, 409);
  assert.match((await second.json()).detail, /이미 Mac 워커가/u);
  assert.equal(state.tasks.length, 1);
});

test("one persona's cooldown does not hold another persona under the same country", async () => {
  // The window used to be the country's. Generation ran inline for minutes so nobody met it;
  // a published job answers at once, and the second persona would have met it every time.
  const state = environment({
    workers: [CAPABLE],
    personas: [personaRow("persona-1"), personaRow("persona-2")],
  });

  const first = await handleHostedWorkspace(generateRequest("persona-1"), state.env, REGISTRY);
  const second = await handleHostedWorkspace(generateRequest("persona-2"), state.env, REGISTRY);

  assert.equal(first.status, 202);
  assert.equal(second.status, 202);
  assert.equal(state.tasks.length, 2);
});

test("published batches are listed so a reloaded tab finds the one already running", async () => {
  const state = environment({ workers: [CAPABLE], personas: [personaRow()] });
  await handleHostedWorkspace(generateRequest("persona-1"), state.env, REGISTRY);

  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/candidates/generation-tasks", {
      headers: { "X-Trace-Persona-ID": "persona-1" },
    }),
    state.env,
    REGISTRY,
  );

  assert.equal(response.status, 200);
  const { tasks } = await response.json();
  assert.equal(tasks.length, 1);
  assert.equal(tasks[0].state, "queued");
  assert.equal(tasks[0].persona_id, "persona-1");
  assert.equal(tasks[0].created, 0);
});

test("the worker callback stores the batch under its persona and keeps the generator's record", async () => {
  const state = environment({ workers: [CAPABLE], personas: [personaRow()] });
  await handleHostedWorkspace(generateRequest("persona-1"), state.env, REGISTRY);
  const task = state.tasks[0];

  const accepted = await receiveHostedGenerationCallback(
    state.env,
    task,
    callbackFor(task, {
      status: "succeeded",
      output: {
        pipeline: HOSTED_GENERATION_PIPELINE,
        persona_id: "persona-1",
        requested: 4,
        failures: 1,
        candidates: [workerDraft(), workerDraft({ topic: "인계 끝나고" })],
      },
    }),
    { worker_id: "worker-1" },
  );

  assert.deepEqual(accepted, { accepted: true, duplicate: false, created: 2 });
  assert.equal(state.candidates.length, 2);
  for (const row of state.candidates) {
    assert.equal(row.persona_id, "persona-1");
    assert.equal(row.source, "auto");
    assert.equal(row.status, "awaiting_review");
    // The generator's own record, kept whole: the panel on screen reads these keys.
    const provenance = JSON.parse(row.generation_provenance_json);
    assert.equal(provenance.model, "gpt-5.6-codex");
    assert.equal(provenance.instruction_chars, 18_240);
    assert.equal(provenance.caption_form, "daily");
    assert.deepEqual(provenance.reference_ids, ["kr-001", "kr-014"]);
    assert.deepEqual(provenance.assigned_domains, ["office_worker"]);
    assert.equal(provenance.documents.length, 2);
    assert.equal(provenance.documents[1].relative_path, "references/KR/INDEX.md");
    assert.equal(row.generation_model, "gpt-5.6-codex");
    assert.equal(row.generation_prompt_version, WORKER_GENERATION_PROMPT_VERSION);
    // And the wallpaper query the model wrote survives, because the image stage runs it.
    assert.equal(JSON.parse(row.image_inputs_json).background_search_query, "쿠로미 배경화면");
  }
  // A partial batch is stored and the shortfall is reported rather than thrown away.
  assert.equal(JSON.parse(task.result_json).output.failures, 1);
  assert.equal(task.state, "succeeded");
});

test("a successful callback without the selected feedback receipt is refused", async () => {
  const state = environment({ workers: [CAPABLE], personas: [personaRow()] });
  await handleHostedWorkspace(generateRequest("persona-1"), state.env, REGISTRY);
  const callback = callbackFor(state.tasks[0], {
    status: "succeeded",
    output: { candidates: [workerDraft()] },
  });
  callback.result.output.feedback_application_sha256 = "f".repeat(64);

  await assert.rejects(
    receiveHostedGenerationCallback(state.env, state.tasks[0], callback, {
      worker_id: "worker-1",
    }),
    /feedback receipt does not match/u,
  );
  assert.equal(state.candidates.length, 0);
});

test("a callback retried after the rows were written does not write them twice", async () => {
  const state = environment({ workers: [CAPABLE], personas: [personaRow()] });
  await handleHostedWorkspace(generateRequest("persona-1"), state.env, REGISTRY);
  const task = state.tasks[0];
  const result = {
    status: "succeeded",
    output: { pipeline: HOSTED_GENERATION_PIPELINE, failures: 0, candidates: [workerDraft()] },
  };

  await receiveHostedGenerationCallback(state.env, task, callbackFor(task, result), { worker_id: "worker-1" });
  const again = await receiveHostedGenerationCallback(
    state.env,
    task,
    callbackFor(task, result),
    { worker_id: "worker-1" },
  );

  assert.equal(again.duplicate, true);
  assert.equal(state.candidates.length, 1);
});

test("a failed batch closes its task and names the failure without writing candidates", async () => {
  const state = environment({ workers: [CAPABLE], personas: [personaRow()] });
  await handleHostedWorkspace(generateRequest("persona-1"), state.env, REGISTRY);
  const task = state.tasks[0];

  await receiveHostedGenerationCallback(
    state.env,
    task,
    callbackFor(task, { status: "failed", failure_code: "hosted_generation_ai_login_required" }),
    { worker_id: "worker-1" },
  );

  assert.equal(state.candidates.length, 0);
  assert.equal(task.state, "failed");

  const listed = await handleHostedWorkspace(
    new Request("https://workspace.example/api/candidates/generation-tasks", {
      headers: { "X-Trace-Persona-ID": "persona-1" },
    }),
    state.env,
    REGISTRY,
  );
  const { tasks } = await listed.json();
  // The operator's fix is on the Mac, so the code that says so has to reach the browser.
  assert.equal(tasks[0].failure_code, "hosted_generation_ai_login_required");
  assert.equal(tasks[0].state, "failed");
});

test("a callback for another account's generation task is refused", async () => {
  const state = environment({ workers: [CAPABLE], personas: [personaRow()] });
  await handleHostedWorkspace(generateRequest("persona-1"), state.env, REGISTRY);
  const task = state.tasks[0];
  const callback = callbackFor(task, {
    status: "succeeded",
    output: { candidates: [workerDraft()] },
  });
  callback.account_id = "someone_else";

  await assert.rejects(
    () => receiveHostedGenerationCallback(state.env, task, callback, { worker_id: "worker-1" }),
    /callback scope does not match hosted generation task/u,
  );
  assert.equal(state.candidates.length, 0);
});

test("a batch with no usable candidates stores nothing and still closes the task", async () => {
  const state = environment({ workers: [CAPABLE], personas: [personaRow()] });
  await handleHostedWorkspace(generateRequest("persona-1"), state.env, REGISTRY);
  const task = state.tasks[0];

  const accepted = await receiveHostedGenerationCallback(
    state.env,
    task,
    callbackFor(task, { status: "succeeded", output: { candidates: [] } }),
    { worker_id: "worker-1" },
  );

  assert.equal(accepted.created, 0);
  assert.equal(state.candidates.length, 0);
  assert.equal(task.state, "succeeded");
});

test("a draft the control plane cannot store costs only itself", async () => {
  // A batch is one provider call per candidate, so one bad answer is one bad candidate. The
  // three beside it are worth keeping, and the worker has nothing to retry either way.
  const state = environment({ workers: [CAPABLE] });

  const applied = await applyHostedGenerationResult(
    state.env,
    { task_id: "task-8", account_id: ACCOUNT_ID, persona_id: null, context_profile_id: null },
    {
      candidates: [
        workerDraft(),
        workerDraft({ image_inputs: { ...workerDraft().image_inputs, device_time: "7시" } }),
      ],
    },
  );

  assert.deepEqual(applied, { created: 1, rejected: 1 });
  assert.equal(state.candidates.length, 1);
});

test("the storage step is callable on its own and reports what it wrote", async () => {
  const state = environment({ workers: [CAPABLE] });

  const applied = await applyHostedGenerationResult(
    state.env,
    {
      task_id: "task-9",
      account_id: ACCOUNT_ID,
      persona_id: null,
      context_profile_id: "profile-1",
    },
    { candidates: [workerDraft(), workerDraft({ topic: "다른 하루" })] },
  );

  assert.deepEqual(applied, { created: 2, rejected: 0 });
  // A country-wide batch has no persona, and inventing one would be a worse record.
  assert.equal(state.candidates[0].persona_id, null);
  assert.equal(state.candidates[0].generation_batch_id, "task-9");
  assert.equal(state.candidates[0].context_profile_id, "profile-1");
});
