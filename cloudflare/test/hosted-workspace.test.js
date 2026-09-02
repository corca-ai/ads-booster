import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  aiCandidates,
  candidateResponseSchema,
  contextForCountry,
  DEFAULT_WORKSPACE_AI_MODEL,
  feedbackSummary,
  generateCandidates,
  generationPrompt,
  handleHostedWorkspace,
  nextDailyGenerationAt,
  normalizeCandidateDraft,
  normalizeContextProfile,
  normalizeHostedAccount,
  normalizeReviewFeedback,
  validateGeneratedCandidateBatch,
  WORKSPACE_GENERATION_PROMPT_VERSION,
} from "../src/hosted-workspace.js";
import { canonicalJson } from "../src/marketing-adapter-capabilities.js";
import {
  WORKSPACE_CONTEXT,
  WORKSPACE_CONTEXT_PROFILES,
} from "../src/generated-workspace-context.js";

function candidate(overrides = {}) {
  return {
    topic: "시험 주간 잠금화면",
    country: "KR",
    caption: "이번 주 일정, 잠금화면에서 먼저 확인해요.",
    hypothesis: "구체적인 시험 주간 장면이 공감을 만든다.",
    refs_used: ["kr-study-day"],
    principles_applied: [1, 7],
    appium_prompt: "",
    image_inputs: {
      trace_items: ["09:00 통계학", "13:00 스터디", "19:00 러닝"],
      device_time: "07:20",
      background_subject: "scenery",
      background_mood: "이른 아침 캠퍼스 창가",
      language: "ko",
    },
    ...overrides,
  };
}

function generatedBatch() {
  return [
    ["아침 시험 준비", "도서관 가기 전 오늘 순서를 확인해요.", "morning", "07:10"],
    ["출근 전 정리", "첫 미팅 전에 오늘의 흐름을 잠금화면에 두세요.", "morning", "07:40"],
    ["저녁 운동 루틴", "퇴근 뒤 운동과 회복 순서를 놓치지 마세요.", "evening", "18:20"],
    ["밤 공부 마감", "하루를 닫기 전 남은 공부를 차분히 끝내요.", "evening", "20:10"],
  ].map(([topic, caption, postingSlot, start], index) => normalizeCandidateDraft(candidate({
    topic,
    caption,
    hypothesis: `${topic}의 구체적인 전환 장면이 공감을 만든다.`,
    posting_slot: postingSlot,
    refs_used: ["kr-study-day"],
    principles_applied: [index + 1],
    image_inputs: {
      ...candidate().image_inputs,
      trace_items: Array.from({ length: 5 }, (_, itemIndex) => {
        const [hour, minute] = start.split(":").map(Number);
        const totalMinutes = hour * 60 + minute + itemIndex * 50;
        const time = `${String(Math.floor(totalMinutes / 60)).padStart(2, "0")}:${String(totalMinutes % 60).padStart(2, "0")}`;
        return `${time} ${topic} ${itemIndex + 1}`;
      }),
    },
  })));
}

function candidateRow(overrides = {}) {
  return {
    candidate_id: "candidate-1",
    account_id: "trace_demo_kr",
    source: "auto",
    country: "KR",
    topic: "승인된 기존 후보",
    caption: "기존 캡션",
    hypothesis: "기존 가설",
    refs_json: JSON.stringify(["kr-study-day"]),
    principles_json: JSON.stringify([1]),
    appium_prompt: "기존 Appium 프롬프트",
    image_inputs_json: JSON.stringify(candidate().image_inputs),
    ai_verdict: "검증됨",
    context_profile_id: null,
    context_snapshot_json: null,
    posting_slot: "evening",
    generation_batch_id: "batch-1",
    generation_prompt_version: null,
    generation_prompt_sha256: null,
    generation_model: null,
    feedback_rules_json: "[]",
    last_review_rating: 5,
    last_review_tags_json: "[]",
    image_key: "workspace/trace_demo_kr/candidates/candidate-1.svg",
    image_sha256: "sha256",
    status: "submitted",
    review_note: null,
    revision: 4,
    created_at: 1,
    updated_at: 1,
    ...overrides,
  };
}

function marketingCaptureCapability({ requestState = "approved", bindingSha256 = null } = {}) {
  const descriptor = {
    schema_version: "trace.adapter-capability.v1",
    capability_id: "capture.native_png",
    effect_class: "local_artifact",
    owner_id: "trace.native_capture",
    request_schema_sha256: "a".repeat(64),
    receipt_schema_sha256: "b".repeat(64),
    activation_state: "active",
  };
  const descriptorSha256 = sha256(descriptor);
  const binding = {
    capability_id: descriptor.capability_id,
    descriptor_sha256: descriptorSha256,
    effect_class: descriptor.effect_class,
    request_schema_sha256: descriptor.request_schema_sha256,
    receipt_schema_sha256: descriptor.receipt_schema_sha256,
    owner_id: descriptor.owner_id,
  };
  const resolvedBindingSha256 = sha256(binding);
  return {
    request: {
      capability_id: descriptor.capability_id,
      capability_binding_sha256: bindingSha256 ?? resolvedBindingSha256,
      state: requestState,
    },
    catalog: {
      capability_id: descriptor.capability_id,
      descriptor_json: canonicalJson(descriptor),
      descriptor_sha256: descriptorSha256,
      effect_class: descriptor.effect_class,
      request_schema_sha256: descriptor.request_schema_sha256,
      receipt_schema_sha256: descriptor.receipt_schema_sha256,
      owner_id: descriptor.owner_id,
      enabled: 1,
      activation_state: "active",
    },
  };
}

function sha256(value) {
  return createHash("sha256").update(canonicalJson(value)).digest("hex");
}

function candidateEnvironment(initial = candidateRow(), activeBrokerWorker = false, options = {}) {
  let row = { ...initial };
  const deletedArtifacts = [];
  const queuedTasks = [];
  const captureTasks = [];
  const feedbackEvents = [];
  const DB = {
    prepare(sql) {
      return {
        bind(...values) {
          return {
            sql,
            values,
            async first() {
              if (sql.includes("SELECT worker_id FROM mac_workers")) {
                return activeBrokerWorker ? { worker_id: "worker-1" } : null;
              }
              if (sql.includes("SELECT * FROM hosted_workspace_accounts")) {
                return {
                  account_id: "trace_demo_kr",
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
                };
              }
              if (sql.includes("FROM hosted_workspace_feedback_events") && sql.includes("event_id = ?")) {
                return options.feedbackEvent ?? null;
              }
              if (sql.includes("SELECT marketing_assignment_id FROM hosted_workspace_candidates")) {
                return row ? { marketing_assignment_id: row.marketing_assignment_id ?? null } : null;
              }
              if (!sql.includes("SELECT * FROM hosted_workspace_candidates")) return null;
              const [accountId, candidateId] = values;
              return row?.account_id === accountId && row?.candidate_id === candidateId ? { ...row } : null;
            },
            async run() {
              if (sql.includes("INSERT OR IGNORE INTO hosted_workspace_accounts")) {
                return { meta: { changes: 0 } };
              }
              if (sql.includes("SET country = ?")) {
                const [
                  country,
                  topic,
                  caption,
                  hypothesis,
                  refsJson,
                  principlesJson,
                  appiumPrompt,
                  imageInputsJson,
                  contextProfileId,
                  contextSnapshotJson,
                  postingSlot,
                  updatedAt,
                  accountId,
                  candidateId,
                  revision,
                ] = values;
                if (!row || row.account_id !== accountId || row.candidate_id !== candidateId || row.revision !== revision) {
                  return { meta: { changes: 0 } };
                }
                row = {
                  ...row,
                  country,
                  topic,
                  caption,
                  hypothesis,
                  refs_json: refsJson,
                  principles_json: principlesJson,
                  appium_prompt: appiumPrompt,
                  image_inputs_json: imageInputsJson,
                  context_profile_id: contextProfileId,
                  context_snapshot_json: contextSnapshotJson,
                  posting_slot: postingSlot,
                  ai_verdict: null,
                  generation_prompt_version: null,
                  generation_prompt_sha256: null,
                  generation_model: null,
                  feedback_rules_json: "[]",
                  status: "awaiting_review",
                  review_note: null,
                  image_key: null,
                  image_sha256: null,
                  last_image_feedback_event_id: null,
                  capture_feedback_context_sha256: null,
                  capture_feedback_application_sha256: null,
                  revision: row.revision + 1,
                  updated_at: updatedAt,
                };
                return { meta: { changes: 1 } };
              }
              if (sql.includes("INSERT INTO hosted_workspace_capture_tasks")) {
                const [gateAccountId, gateCandidateId, gateRevision] = values.slice(-3);
                if (
                  !activeBrokerWorker || !row || row.account_id !== gateAccountId ||
                  row.candidate_id !== gateCandidateId || row.status !== "caption_approved" ||
                  row.revision !== gateRevision
                ) {
                  return { meta: { changes: 0 } };
                }
                captureTasks.push(values);
                return { meta: { changes: 1 } };
              }
              if (sql.includes("INSERT INTO hosted_workspace_feedback_events")) {
                if (options.failFeedbackInsert) throw new Error("injected feedback insert failure");
                const [gateAccountId, gateCandidateId, gateStatus, gateRevision] = values.slice(-4);
                if (
                  !row || row.account_id !== gateAccountId || row.candidate_id !== gateCandidateId ||
                  row.status !== gateStatus || row.revision !== gateRevision
                ) {
                  return { meta: { changes: 0 } };
                }
                feedbackEvents.push(values);
                return { meta: { changes: 1 } };
              }
              if (sql.includes("SET status = ?, review_note = ?, last_review_rating = ?")) {
                if (options.failReviewTransition) throw new Error("injected review transition failure");
                const [
                  status,
                  reviewNote,
                  reviewRating,
                  reviewTagsJson,
                  updatedAt,
                  accountId,
                  candidateId,
                  previousStatus,
                  revision,
                ] = values;
                if (
                  !row || row.account_id !== accountId || row.candidate_id !== candidateId ||
                  row.status !== previousStatus || row.revision !== revision
                ) {
                  return { meta: { changes: 0 } };
                }
                row = {
                  ...row,
                  status,
                  review_note: reviewNote,
                  last_review_rating: reviewRating,
                  last_review_tags_json: reviewTagsJson,
                  revision: row.revision + 1,
                  updated_at: updatedAt,
                };
                return { meta: { changes: 1 } };
              }
              if (sql.includes("SET status = ?, review_note = ?, image_key = ?, image_sha256 = ?")) {
                if (options.failReviewTransition) throw new Error("injected review transition failure");
                const [
                  status,
                  reviewNote,
                  imageKey,
                  imageSha256,
                  reviewRating,
                  reviewTagsJson,
                  feedbackEventId,
                  updatedAt,
                  accountId,
                  candidateId,
                  revision,
                ] = values;
                if (
                  !row || row.account_id !== accountId || row.candidate_id !== candidateId ||
                  row.status !== "image_awaiting_review" || row.revision !== revision
                ) {
                  return { meta: { changes: 0 } };
                }
                row = {
                  ...row,
                  status,
                  review_note: reviewNote,
                  image_key: imageKey,
                  image_sha256: imageSha256,
                  last_review_rating: reviewRating,
                  last_review_tags_json: reviewTagsJson,
                  last_image_feedback_event_id: feedbackEventId,
                  revision: row.revision + 1,
                  updated_at: updatedAt,
                };
                return { meta: { changes: 1 } };
              }
              if (sql.includes("SET capture_state = 'queued'")) {
                if (options.failCandidateQueueUpdate) throw new Error("injected candidate queue failure");
                const [taskId, requestedAt, feedbackDigest, updatedAt,
                  accountId, candidateId, revision] = values;
                if (
                  !activeBrokerWorker || !row || row.account_id !== accountId || row.candidate_id !== candidateId ||
                  row.status !== "caption_approved" || row.revision !== revision
                ) {
                  return { meta: { changes: 0 } };
                }
                row = {
                  ...row,
                  capture_state: "queued",
                  capture_task_id: taskId,
                  capture_error: null,
                  capture_requested_at: requestedAt,
                  capture_feedback_context_sha256: feedbackDigest,
                  capture_feedback_application_sha256: null,
                  revision: row.revision + 1,
                  updated_at: updatedAt,
                };
                return { meta: { changes: 1 } };
              }
              if (sql.includes("SET last_dispatched_at = ?")) {
                return { meta: { changes: 1 } };
              }
              if (sql.includes("DELETE FROM hosted_workspace_candidates")) {
                const [accountId, candidateId, revision] = values;
                if (!row || row.account_id !== accountId || row.candidate_id !== candidateId || row.revision !== revision) {
                  return { meta: { changes: 0 } };
                }
                row = null;
                return { meta: { changes: 1 } };
              }
              throw new Error(`unexpected SQL: ${sql}`);
            },
            async all() {
              if (sql.includes("SELECT capabilities_json FROM mac_workers")) {
                return {
                  results: activeBrokerWorker
                    ? [{ capabilities_json: JSON.stringify({
                        task_kinds: "capture,generate_candidates",
                        feedback_context_v1: true,
                      }) }]
                    : [],
                };
              }
              if (sql.includes("FROM hosted_workspace_feedback_events")) {
                return { results: options.feedbackRows ?? [] };
              }
              if (sql.includes("FROM hosted_workspace_feedback_rule_overrides")) {
                return { results: options.feedbackOverrides ?? [] };
              }
              if (sql.includes("hosted_marketing_adapter_capabilities")) {
                return { results: options.marketingCatalogRows ?? [] };
              }
              if (sql.includes("FROM hosted_marketing_post_assignments")) {
                assert.match(sql, /request\.state = 'approved'/);
                return {
                  results: (options.marketingCaptureRequests ?? [])
                    .filter((request) => request.state === "approved"),
                };
              }
              throw new Error(`unexpected all SQL: ${sql}`);
            },
          };
        },
      };
    },
    async batch(statements) {
      const previousRow = row ? { ...row } : null;
      const previousCaptureCount = captureTasks.length;
      const previousFeedbackCount = feedbackEvents.length;
      try {
        const results = [];
        for (const statement of statements) results.push(await statement.run());
        return results;
      } catch (error) {
        row = previousRow;
        captureTasks.length = previousCaptureCount;
        feedbackEvents.length = previousFeedbackCount;
        throw error;
      }
    },
  };
  return {
    env: {
      DB,
      ARTIFACTS: {
        async delete(key) {
          deletedArtifacts.push(key);
        },
      },
      TASK_QUEUE: {
        async send(body) {
          queuedTasks.push(JSON.parse(body));
        },
      },
      PUBLIC_WORKSPACE_ACCOUNT_ID: "trace_demo_kr",
    },
    deletedArtifacts,
    queuedTasks,
    captureTasks,
    feedbackEvents,
    row: () => row,
  };
}

function generationEnvironment(options = {}) {
  const inserted = new Map();
  const aiCalls = [];
  const account = {
    account_id: "trace_demo_kr",
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
  };
  const profile = {
    account_id: "trace_demo_kr",
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
  };
  const feedbackRows = [1, 2, 3].map((revision) => ({
    candidate_id: `reviewed-${revision}`,
    candidate_revision: revision,
    tags_json: JSON.stringify(["컨셉이 약함"]),
    note: `review ${revision}`,
    rating: 2,
    stage: "caption",
    created_at: 100 - revision,
  }));
  const DB = {
    prepare(sql) {
      return {
        bind(...values) {
          return {
            sql,
            values,
            async run() {
              if (sql.includes("INSERT OR IGNORE INTO hosted_workspace_accounts")) {
                return { meta: { changes: 0 } };
              }
              if (sql.includes("hosted_workspace_generation_locks")) {
                return { meta: { changes: 1 } };
              }
              throw new Error(`unexpected run SQL: ${sql}`);
            },
            async first() {
              if (sql.includes("SELECT * FROM hosted_workspace_accounts")) return account;
              if (sql.includes("SELECT * FROM hosted_workspace_context_profiles")) return profile;
              if (sql.includes("JOIN shared_instructions")) return { body: "공통 지침" };
              if (sql.includes("SELECT * FROM hosted_workspace_candidates")) {
                return inserted.get(values[1]) ?? null;
              }
              throw new Error(`unexpected first SQL: ${sql}`);
            },
            async all() {
              if (sql.includes("FROM hosted_workspace_feedback_events")) {
                return { results: feedbackRows };
              }
              if (sql.includes("FROM hosted_workspace_feedback_rule_overrides")) {
                return { results: [] };
              }
              throw new Error(`unexpected all SQL: ${sql}`);
            },
          };
        },
      };
    },
    async batch(statements) {
      if (options.failCandidateInsert) throw new Error("injected candidate insert failure");
      for (const statement of statements) {
        if (!statement.sql.includes("INSERT INTO hosted_workspace_candidates")) {
          throw new Error(`unexpected batch SQL: ${statement.sql}`);
        }
        const values = statement.values;
        inserted.set(values[0], {
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
          status: "awaiting_review",
          revision: 1,
          created_at: values[20],
          updated_at: values[21],
        });
      }
      return statements.map(() => ({ meta: { changes: 1 } }));
    },
  };
  return {
    env: {
      DB,
      AI: {
        async run(model, request) {
          aiCalls.push({ model, request });
          return { response: { candidates: generatedBatch() } };
        },
      },
      PUBLIC_WORKSPACE_ACCOUNT_ID: "trace_demo_kr",
    },
    aiCalls,
    inserted,
  };
}

test("public session enters the Cloudflare workspace without an access ID", async () => {
  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/auth/session"),
    { PUBLIC_WORKSPACE_ACCOUNT_ID: "trace_demo_kr" },
    "context",
  );

  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    workspace_id: "cloudflare:trace_demo_kr",
    account_id: "trace_demo_kr",
    member_id: "public",
    display_name: "Trace Team",
    is_admin: false,
  });
});

test("candidate normalization always fills the Appium prompt", () => {
  const normalized = normalizeCandidateDraft(candidate());

  assert.match(normalized.appium_prompt, /입력_일정: D\+0 09:00 통계학/);
  assert.match(normalized.appium_prompt, /기기_시각: 07:20/);
  assert.match(normalized.appium_prompt, /정지\/영상: 정지 이미지/);
});

test("candidate normalization replaces an incomplete AI Appium prompt", () => {
  const normalized = normalizeCandidateDraft(candidate({ appium_prompt: "시험 기간 잠금화면" }));

  assert.match(normalized.appium_prompt, /입력_일정:/);
  assert.match(normalized.appium_prompt, /배경화면: scenery · 이른 아침 캠퍼스 창가/);
  assert.doesNotMatch(normalized.appium_prompt, /^시험 기간 잠금화면$/);
});

test("candidate normalization rejects unusable image context", () => {
  assert.throws(
    () => normalizeCandidateDraft(candidate({ image_inputs: { ...candidate().image_inputs, device_time: "7시" } })),
    /device_time은 HH:MM/,
  );
});

test("Workers AI schema uses provider-compatible constraints and keeps local uniqueness", () => {
  const schema = candidateResponseSchema("KR", ["kr-study-day", "kr-020"]);
  const candidates = schema.properties.candidates;

  assert.doesNotMatch(JSON.stringify(schema), /"uniqueItems":/u);
  assert.equal(candidates.minItems, 4);
  assert.equal(candidates.maxItems, 4);
  assert.deepEqual(candidates.items.properties.posting_slot.enum, ["morning", "evening"]);
  assert.equal(candidates.items.properties.refs_used.minItems, 1);
  assert.equal("uniqueItems" in candidates.items.properties.refs_used, false);
  assert.deepEqual(candidates.items.properties.refs_used.items.enum, ["kr-study-day", "kr-020"]);
  assert.equal(candidates.items.properties.principles_applied.minItems, 1);
  assert.equal("uniqueItems" in candidates.items.properties.principles_applied, false);
  assert.equal(candidates.items.properties.image_inputs.properties.trace_items.minItems, 5);
  assert.equal(candidates.items.properties.image_inputs.properties.trace_items.maxItems, 24);
  assert.ok(candidates.items.required.includes("posting_slot"));
  assert.ok(candidates.items.required.includes("appium_prompt"));
  assert.ok(candidates.items.required.includes("image_inputs"));
});

test("generated batches reject repeated content and non-schedule trace items", () => {
  const profile = { reference_ids: ["kr-study-day"] };
  const valid = generatedBatch();
  assert.doesNotThrow(() => validateGeneratedCandidateBatch(valid, profile));

  const repeatedCaption = generatedBatch();
  repeatedCaption[1].caption = repeatedCaption[0].caption;
  assert.throws(
    () => validateGeneratedCandidateBatch(repeatedCaption, profile),
    /캡션이 서로 달라야/u,
  );

  const filenameSchedule = generatedBatch();
  filenameSchedule[0].image_inputs.trace_items[0] = "trace-home.png";
  assert.throws(
    () => validateGeneratedCandidateBatch(filenameSchedule, profile),
    /제목을 가진 일정 항목/u,
  );
});

test("generated batches reject profile escape, duplicate principles, and duplicate schedules", () => {
  const profile = { reference_ids: ["kr-study-day"] };
  const outsideReference = generatedBatch();
  outsideReference[0].refs_used = ["kr-020"];
  assert.throws(
    () => validateGeneratedCandidateBatch(outsideReference, profile),
    /페르소나 밖의 레퍼런스/u,
  );

  const duplicatePrinciples = generatedBatch();
  duplicatePrinciples[0].principles_applied = [1, 1];
  assert.throws(
    () => validateGeneratedCandidateBatch(duplicatePrinciples, profile),
    /적용 원리는 중복/u,
  );

  const duplicateSchedules = generatedBatch();
  duplicateSchedules[1].image_inputs.trace_items = [...duplicateSchedules[0].image_inputs.trace_items];
  assert.throws(
    () => validateGeneratedCandidateBatch(duplicateSchedules, profile),
    /서로 다른 일정 장면/u,
  );
});

test("hosted candidate generation defaults to GPT-OSS 20B in code and deployment config", async () => {
  const config = JSON.parse(
    await readFile(new URL("../wrangler.template.jsonc", import.meta.url), "utf8"),
  );

  assert.equal(DEFAULT_WORKSPACE_AI_MODEL, "@cf/openai/gpt-oss-20b");
  assert.equal(config.vars.WORKSPACE_AI_MODEL, DEFAULT_WORKSPACE_AI_MODEL);
});

test("Workers AI chat completion output exposes the generated candidates", () => {
  const expected = [candidate()];
  const result = {
    choices: [{ message: { content: JSON.stringify({ candidates: expected }) } }],
  };

  assert.deepEqual(aiCandidates(result), expected);
});

test("workspace context assets expose data-driven country profiles", () => {
  assert.ok(WORKSPACE_CONTEXT.global.includes("PRINCIPLES-GLOBAL"));
  assert.ok(WORKSPACE_CONTEXT.countries.KR.includes("PRINCIPLES-KR"));
  assert.equal(WORKSPACE_CONTEXT_PROFILES.length, 16);
  assert.equal(WORKSPACE_CONTEXT_PROFILES.filter((profile) => profile.is_default).length, 7);
  assert.deepEqual(new Set(WORKSPACE_CONTEXT_PROFILES.map(
    (profile) => normalizeContextProfile(profile).country,
  )), new Set(["BR", "DE", "FR", "JP", "KR", "TW", "US"]));
});

test("researched countries carry the archive's own principle documents", () => {
  for (const country of ["KR", "JP", "TW"]) {
    const documents = contextForCountry(WORKSPACE_CONTEXT, country);
    assert.ok(documents.includes(`core/PRINCIPLES-${country}.md`));
    assert.ok(documents.includes(`core/ELEMENTS-${country}.md`));
    // The archive documents carry frontmatter the earlier paraphrased stubs never had.
    assert.match(documents, new RegExp(`country: ${country}`));
    assert.match(documents, /status: verified/);
  }
  const korean = contextForCountry(WORKSPACE_CONTEXT, "KR");
  assert.ok(korean.includes("core/VOICE-KR.md"));
  assert.ok(korean.includes("core/SHOOTING-KR.md"));
});

test("hypothesis markets carry no researched country documents", () => {
  for (const country of ["US", "DE", "FR", "BR"]) {
    const documents = contextForCountry(WORKSPACE_CONTEXT, country);
    assert.ok(!documents.includes(`core/PRINCIPLES-${country}.md`));
    assert.ok(documents.includes(`markets/${country}.md`));
  }
});

test("every country is told how to read deprecated and unverified findings", () => {
  for (const country of Object.keys(WORKSPACE_CONTEXT.countries)) {
    const documents = contextForCountry(WORKSPACE_CONTEXT, country);
    assert.ok(documents.includes("core/PIPELINE-SCOPE.md"));
    assert.match(documents, /취소선/);
    assert.match(documents, /default-OFF Cloudflare scheduler/);
    assert.match(documents, /다른\s+채널에는 자동 게시하지 않는다/);
  }
});

test("the reference corpus ships whole but only named records reach the prompt", () => {
  assert.equal(Object.keys(WORKSPACE_CONTEXT.referenceBodies.KR).length, 41);

  const sceneProfile = WORKSPACE_CONTEXT_PROFILES.find((profile) => profile.country === "KR");
  const withoutBodies = contextForCountry(WORKSPACE_CONTEXT, "KR", sceneProfile);
  assert.ok(!withoutBodies.includes("[레퍼런스 본문:"));

  const withBodies = contextForCountry(WORKSPACE_CONTEXT, "KR", {
    ...sceneProfile,
    reference_ids: ["kr-020", "kr-027"],
  });
  assert.ok(withBodies.includes("[레퍼런스 본문: kr-020]"));
  assert.ok(withBodies.includes("[레퍼런스 본문: kr-027]"));
});

test("an oversized reference selection is trimmed instead of blowing the prompt", () => {
  const ids = Object.keys(WORKSPACE_CONTEXT.referenceBodies.KR);
  const documents = contextForCountry(WORKSPACE_CONTEXT, "KR", {
    country: "KR",
    reference_ids: ids,
  });
  const inlined = documents.match(/\[레퍼런스 본문: /g) ?? [];
  assert.ok(inlined.length > 0);
  assert.ok(inlined.length <= 5);
});

test("context profiles reject reference IDs the Mac contract cannot consume", () => {
  const profile = { ...WORKSPACE_CONTEXT_PROFILES[0], reference_ids: ["invalid/reference"] };
  assert.throws(
    () => normalizeContextProfile(profile),
    /레퍼런스 ID는/u,
  );
});

test("candidate drafts reject reference IDs the Mac contract cannot consume", () => {
  assert.throws(
    () => normalizeCandidateDraft({ ...candidate(), refs_used: ["invalid/reference"] }),
    /레퍼런스 ID는/u,
  );
});

test("candidate drafts reject duplicate references and principles before storage", () => {
  assert.throws(
    () => normalizeCandidateDraft({ ...candidate(), refs_used: ["kr-study-day", "kr-study-day"] }),
    /레퍼런스 ID는 중복/u,
  );
  assert.throws(
    () => normalizeCandidateDraft({ ...candidate(), principles_applied: [1, 1] }),
    /적용 원리는 중복/u,
  );
});

test("hosted context countries come from the packaged manifest", async () => {
  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/context-countries"),
    {},
    WORKSPACE_CONTEXT,
  );

  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), [
    { country: "BR", language: "pt" },
    { country: "DE", language: "de" },
    { country: "FR", language: "fr" },
    { country: "JP", language: "ja" },
    { country: "KR", language: "ko" },
    { country: "TW", language: "zh" },
    { country: "US", language: "en" },
  ]);
});

test("account creation rejects a country without packaged context", () => {
  assert.throws(
    () => normalizeHostedAccount({
      account_id: "trace_es",
      display_name: "Trace Spain",
      country: "ES",
      timezone: "Europe/Madrid",
    }, WORKSPACE_CONTEXT),
    /ES 국가 context 문서/,
  );
});

test("review feedback requires structured rejection reasons", () => {
  assert.deepEqual(normalizeReviewFeedback({ rating: 5 }, true), {
    rating: 5,
    tags: [],
    note: "",
  });
  assert.throws(() => normalizeReviewFeedback({ rating: 2, tags: [] }, false), /이유 태그/);
  assert.throws(
    () => normalizeReviewFeedback({ rating: 2, tags: ["기타"] }, false),
    /상세 이유/,
  );
  assert.deepEqual(
    normalizeReviewFeedback({ rating: 2, tags: ["컨셉이 약함"] }, false).tags,
    ["컨셉이 약함"],
  );
});

test("feedback rules require three distinct strong rejections in the same stage", async () => {
  const rows = [
    ["candidate-1", 1, "caption", 2, "컨셉이 약함", "첫 번째 구체적인 이유"],
    ["candidate-1", 1, "caption", 1, "컨셉이 약함", "같은 revision 중복 이벤트"],
    ["candidate-2", 2, "caption", 2, "컨셉이 약함", "두 번째 이유"],
    ["candidate-3", 4, "caption", 1, "컨셉이 약함", "세 번째 이유"],
    ["candidate-4", 1, "image", 2, "컨셉이 약함", "단계가 다른 이유"],
    ["candidate-5", 1, "caption", 3, "캡션 부적합", "약한 반려"],
    ["candidate-6", 1, "caption", 1, "기타", "자유 입력은 자동 규칙이 아님"],
  ].map(([candidateId, revision, stage, rating, tag, note], index) => ({
    candidate_id: candidateId,
    candidate_revision: revision,
    stage,
    rating,
    tags_json: JSON.stringify([tag]),
    note,
    created_at: 100 - index,
  }));
  const env = {
    PUBLIC_WORKSPACE_ACCOUNT_ID: "trace_demo_kr",
    DB: {
      prepare(sql) {
        return {
          bind() {
            return { async all() {
              return { results: sql.includes("feedback_rule_overrides") ? [] : rows };
            } };
          },
        };
      },
    },
  };

  const summary = await feedbackSummary(env, null);

  assert.equal(summary.rejected_reviews, rows.length);
  assert.equal(summary.active_rules.length, 1);
  assert.deepEqual(summary.active_rules[0], {
    rule_id: "caption-concept-specificity",
    dimension: "concept",
    instruction: "일반적인 생산성 문구 대신 한 장면과 한 갈등이 보이는 구체적인 컨셉을 만든다.",
    definition_version: "1",
    stage: "caption",
    tag: "컨셉이 약함",
    evidence_count: 3,
    targets: ["candidate_generation"],
  });
  assert.doesNotMatch(summary.rule_candidates[0], /첫 번째 구체적인 이유/u);
  assert.equal(Object.hasOwn(summary, "recent_notes"), false);

  const disabledEnv = {
    PUBLIC_WORKSPACE_ACCOUNT_ID: "trace_demo_kr",
    DB: {
      prepare(sql) {
        return {
          bind() {
            return { async all() {
              return { results: sql.includes("feedback_rule_overrides")
                ? [{
                    stage: "caption",
                    rule_id: "caption-concept-specificity",
                    enabled: 0,
                    revision: 2,
                    updated_at: 101,
                  }]
                : rows };
            } };
          },
        };
      },
    },
  };
  const disabled = await feedbackSummary(disabledEnv, null);
  assert.deepEqual(disabled.active_rules, []);
  assert.equal(disabled.disabled_rules[0].rule_id, "caption-concept-specificity");
});

test("legacy feedback without a reviewed revision remains aggregate-only", async () => {
  const rows = ["legacy-1", "legacy-2", "legacy-3"].map((candidateId, index) => ({
    candidate_id: candidateId,
    candidate_revision: null,
    stage: "caption",
    rating: 1,
    tags_json: JSON.stringify(["컨셉이 약함"]),
    created_at: 100 - index,
  }));
  const env = {
    PUBLIC_WORKSPACE_ACCOUNT_ID: "trace_demo_kr",
    DB: {
      prepare(sql) {
        return {
          bind() {
            return { async all() {
              return { results: sql.includes("feedback_rule_overrides") ? [] : rows };
            } };
          },
        };
      },
    },
  };

  const summary = await feedbackSummary(env, null);

  assert.equal(summary.rejected_reviews, 3);
  assert.deepEqual(summary.top_tags, [{ tag: "컨셉이 약함", count: 3 }]);
  assert.deepEqual(summary.rule_candidates, []);
  assert.deepEqual(summary.active_rules, []);
});

test("caption review stores the exact reviewed revision snapshot and generation provenance", async () => {
  const state = candidateEnvironment(candidateRow({
    status: "awaiting_review",
    revision: 4,
    generation_prompt_version: WORKSPACE_GENERATION_PROMPT_VERSION,
    generation_prompt_sha256: "a".repeat(64),
    generation_model: DEFAULT_WORKSPACE_AI_MODEL,
    feedback_rules_json: JSON.stringify([{
      rule_id: "caption-concept-specificity",
      dimension: "concept",
      instruction: "한 장면과 한 갈등을 만든다.",
      stage: "caption",
      tag: "컨셉이 약함",
      evidence_count: 3,
    }]),
  }));
  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/candidates/candidate-1/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        accepted: false,
        expected_revision: 4,
        rating: 2,
        tags: ["컨셉이 약함"],
        note: "구체적인 사용 장면이 보이지 않음",
      }),
    }),
    state.env,
    "context",
  );

  assert.equal(response.status, 200);
  assert.equal(state.row().status, "rejected");
  assert.equal(state.feedbackEvents.length, 1);
  const event = state.feedbackEvents[0];
  assert.equal(event[2], "candidate-1");
  assert.equal(event[4], "caption");
  assert.equal(event[5], "rejected");
  assert.equal(event[9], 4);
  assert.equal(JSON.parse(event[10]).candidate_revision, 4);
  assert.match(event[11], /^[a-f0-9]{64}$/u);
  assert.equal(event[12], WORKSPACE_GENERATION_PROMPT_VERSION);
  assert.equal(event[13], "a".repeat(64));
  assert.equal(event[14], DEFAULT_WORKSPACE_AI_MODEL);
  assert.equal(JSON.parse(event[15])[0].rule_id, "caption-concept-specificity");
});

test("caption review rejects image-only tags before storing a signal", async () => {
  const state = candidateEnvironment(candidateRow({ status: "awaiting_review", revision: 4 }));
  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/candidates/candidate-1/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        accepted: false,
        expected_revision: 4,
        rating: 2,
        tags: ["이미지 품질·AI 티"],
      }),
    }),
    state.env,
    "context",
  );

  assert.equal(response.status, 400);
  assert.match((await response.json()).detail, /caption 검수 단계/u);
  assert.equal(state.feedbackEvents.length, 0);
});

test("caption review rolls back the decision when feedback evidence cannot be stored", async () => {
  const state = candidateEnvironment(
    candidateRow({ status: "awaiting_review", revision: 4 }),
    false,
    { failFeedbackInsert: true },
  );
  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/candidates/candidate-1/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        accepted: false,
        expected_revision: 4,
        rating: 2,
        tags: ["컨셉이 약함"],
      }),
    }),
    state.env,
    "context",
  );

  assert.equal(response.status, 500);
  assert.equal(state.row().status, "awaiting_review");
  assert.equal(state.row().revision, 4);
  assert.equal(state.feedbackEvents.length, 0);
});

test("image review rolls back feedback evidence when the candidate transition fails", async () => {
  const state = candidateEnvironment(
    candidateRow({ status: "image_awaiting_review", revision: 5 }),
    false,
    { failReviewTransition: true },
  );
  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/candidates/candidate-1/review-image", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        accepted: false,
        expected_revision: 5,
        rating: 2,
        tags: ["이미지 품질·AI 티"],
      }),
    }),
    state.env,
    "context",
  );

  assert.equal(response.status, 500);
  assert.equal(state.row().status, "image_awaiting_review");
  assert.equal(state.row().revision, 5);
  assert.equal(state.feedbackEvents.length, 0);
  assert.deepEqual(state.deletedArtifacts, []);
});

test("image rejection commits its evidence and candidate transition together", async () => {
  const state = candidateEnvironment(candidateRow({ status: "image_awaiting_review", revision: 5 }));
  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/candidates/candidate-1/review-image", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        accepted: false,
        expected_revision: 5,
        rating: 2,
        tags: ["이미지 품질·AI 티"],
      }),
    }),
    state.env,
    "context",
  );

  assert.equal(response.status, 200);
  assert.equal(state.row().status, "caption_approved");
  assert.equal(state.row().revision, 6);
  assert.equal(state.feedbackEvents.length, 1);
  assert.deepEqual(state.deletedArtifacts, ["workspace/trace_demo_kr/candidates/candidate-1.svg"]);
});

test("daily generation follows the account timezone", () => {
  const after = new Date("2026-08-26T00:00:00.000Z");
  assert.equal(
    nextDailyGenerationAt("Asia/Seoul", "07:30", after).toISOString(),
    "2026-08-26T22:30:00.000Z",
  );
});

test("generation prompt binds the selected persona and country", () => {
  const profile = WORKSPACE_CONTEXT_PROFILES[1];
  const prompt = generationPrompt("기본 원리", "계정 지침", profile);
  const schema = candidateResponseSchema(profile.country, profile.reference_ids);

  assert.equal(WORKSPACE_GENERATION_PROMPT_VERSION, "trace.workspace-generation.v3");
  assert.match(prompt, new RegExp(profile.persona_id));
  assert.match(prompt, new RegExp(profile.audience));
  assert.match(prompt, /topic, caption, trace_items/u);
  assert.match(prompt, /네 후보에서 각각 서로 달라야/u);
  assert.deepEqual(schema.properties.candidates.items.properties.country.enum, ["KR"]);
  assert.deepEqual(
    schema.properties.candidates.items.properties.image_inputs.properties.language.enum,
    ["ko"],
  );
});

test("generation prompt routes controlled feedback rules by quality dimension", () => {
  const profile = WORKSPACE_CONTEXT_PROFILES[1];
  const prompt = generationPrompt("기본 원리", "계정 지침", profile, null, {
    active_rules: [
      { dimension: "caption", instruction: "캡션 규칙" },
      { dimension: "design", instruction: "디자인 규칙" },
      { dimension: "persona", instruction: "페르소나 규칙" },
    ],
  });

  assert.match(prompt, /\[캡션 규칙\]\n- 캡션 규칙/u);
  assert.match(prompt, /\[디자인 규칙\]\n- 디자인 규칙/u);
  assert.match(prompt, /\[페르소나 규칙\]\n- 페르소나 규칙/u);
});

// The Workers AI generator is off every route now — the Mac worker writes hosted captions —
// but it is still here and still covered, because the history it wrote is still on screen and
// the two records have to keep rendering side by side.
const retainedProfile = {
  profile_id: "profile-1",
  country: "KR",
  name: "학생의 실제 하루",
  persona_id: "kr_student",
  audience: "한국 대학생",
  situation: "시험 주간",
  tone: "담백한 한국어",
  guidance: "실제 하루 장면을 보여준다.",
  reference_ids: ["kr-study-day"],
  source: "custom",
  is_default: true,
  revision: 1,
};

test("the retained Workers AI generator still persists its prompt digest, model, and rule evidence", async () => {
  const state = generationEnvironment();
  const generated = await generateCandidates(state.env, "기본 원리", retainedProfile);

  assert.equal(generated.length, 4);
  assert.equal(state.aiCalls.length, 1);
  const exactPrompt = state.aiCalls[0].request.messages[1].content;
  const bytes = new TextEncoder().encode(exactPrompt);
  const digest = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
  for (const item of generated) {
    assert.deepEqual(item.generation_provenance, {
      prompt_version: WORKSPACE_GENERATION_PROMPT_VERSION,
      prompt_sha256: digest,
      model: DEFAULT_WORKSPACE_AI_MODEL,
      feedback_rules: [{
        rule_id: "caption-concept-specificity",
        definition_version: "1",
        dimension: "concept",
        instruction: "일반적인 생산성 문구 대신 한 장면과 한 갈등이 보이는 구체적인 컨셉을 만든다.",
        stage: "caption",
        tag: "컨셉이 약함",
        evidence_count: 3,
        targets: ["candidate_generation"],
      }],
    });
  }
  assert.match(exactPrompt, /\[컨셉 규칙\]/u);
  assert.equal(state.inserted.size, 4);
});

test("candidate storage failure does not trigger another Workers AI call", async () => {
  const state = generationEnvironment({ failCandidateInsert: true });

  await assert.rejects(() => generateCandidates(state.env, "기본 원리", retainedProfile));
  assert.equal(state.aiCalls.length, 1);
  assert.equal(state.inserted.size, 0);
});

test("editing a submitted candidate invalidates approval and removes its preview", async () => {
  const state = candidateEnvironment();
  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/candidates/candidate-1", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...candidate({ topic: "수정된 후보" }), expected_revision: 4 }),
    }),
    state.env,
    "context",
  );

  assert.equal(response.status, 200);
  const updated = await response.json();
  assert.equal(updated.topic, "수정된 후보");
  assert.equal(updated.status, "awaiting_review");
  assert.equal(updated.revision, 5);
  assert.equal(updated.image_path, null);
  assert.equal(updated.ai_verdict, null);
  assert.deepEqual(state.deletedArtifacts, ["workspace/trace_demo_kr/candidates/candidate-1.svg"]);
});

test("deleting a submitted candidate removes its D1 record and R2 preview", async () => {
  const state = candidateEnvironment();
  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/candidates/candidate-1", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_revision: 4 }),
    }),
    state.env,
    "context",
  );

  assert.equal(response.status, 204);
  assert.equal(state.row(), null);
  assert.deepEqual(state.deletedArtifacts, ["workspace/trace_demo_kr/candidates/candidate-1.svg"]);
});

test("stale candidate edits fail without deleting the approved preview", async () => {
  const state = candidateEnvironment();
  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/candidates/candidate-1", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...candidate(), expected_revision: 3 }),
    }),
    state.env,
    "context",
  );

  assert.equal(response.status, 409);
  assert.equal(state.row().status, "submitted");
  assert.deepEqual(state.deletedArtifacts, []);
});

test("image generation fails before queueing when no Mac worker is registered", async () => {
  const state = candidateEnvironment(candidateRow({
    status: "caption_approved",
    image_key: null,
    image_sha256: null,
    revision: 3,
    capture_state: null,
    capture_task_id: null,
    capture_error: null,
    capture_requested_at: null,
    last_image_feedback_event_id: null,
    context_snapshot_json: JSON.stringify({
      persona_id: "kr_student",
      guidance: "과장 없이 실제 사용 장면을 보여준다.",
      reference_ids: ["kr-020"],
    }),
  }));
  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/candidates/candidate-1/generate-image", {
      method: "POST",
    }),
    state.env,
    "context",
  );

  assert.equal(response.status, 503);
  assert.match((await response.json()).detail, /등록된 Mac worker/u);
  assert.equal(state.row().status, "caption_approved");
  assert.equal(state.row().capture_state, null);
  assert.equal(state.queuedTasks.length, 0);
  assert.equal(state.captureTasks.length, 0);
});

test("a marketing candidate needs an approved bound native-capture request before queueing", async () => {
  const state = candidateEnvironment(candidateRow({
    status: "caption_approved",
    image_key: null,
    image_sha256: null,
    revision: 3,
    capture_state: null,
    capture_task_id: null,
    capture_error: null,
    capture_requested_at: null,
    marketing_assignment_id: "assignment-1",
  }), true);

  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/candidates/candidate-1/generate-image", {
      method: "POST",
    }),
    state.env,
    "context",
  );

  assert.equal(response.status, 409);
  assert.match((await response.json()).detail, /native capture request/u);
  assert.equal(state.row().capture_state, null);
  assert.equal(state.captureTasks.length, 0);
  assert.equal(state.queuedTasks.length, 0);
});

test("a planned native-capture request cannot queue a marketing candidate", async () => {
  const capability = marketingCaptureCapability({ requestState: "planned" });
  const state = candidateEnvironment(candidateRow({
    status: "caption_approved",
    image_key: null,
    image_sha256: null,
    revision: 3,
    capture_state: null,
    capture_task_id: null,
    capture_error: null,
    capture_requested_at: null,
    marketing_assignment_id: "assignment-1",
  }), true, {
    marketingCaptureRequests: [capability.request],
    marketingCatalogRows: [capability.catalog],
  });

  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/candidates/candidate-1/generate-image", {
      method: "POST",
    }),
    state.env,
    "context",
  );

  assert.equal(response.status, 409);
  assert.match((await response.json()).detail, /native capture request/u);
  assert.equal(state.row().capture_state, null);
  assert.equal(state.captureTasks.length, 0);
});

test("a current approved native-capture binding queues exactly one marketing capture", async () => {
  const capability = marketingCaptureCapability();
  const state = candidateEnvironment(candidateRow({
    status: "caption_approved",
    image_key: null,
    image_sha256: null,
    revision: 3,
    capture_state: null,
    capture_task_id: null,
    capture_error: null,
    capture_requested_at: null,
    marketing_assignment_id: "assignment-1",
  }), true, {
    marketingCaptureRequests: [capability.request],
    marketingCatalogRows: [capability.catalog],
  });

  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/candidates/candidate-1/generate-image", {
      method: "POST",
    }),
    state.env,
    "context",
  );

  assert.equal(response.status, 201);
  assert.equal(state.row().capture_state, "queued");
  assert.equal(state.captureTasks.length, 1);
  assert.equal(state.queuedTasks.length, 0);
});

test("a stale native-capture binding cannot queue a marketing candidate", async () => {
  const capability = marketingCaptureCapability({ bindingSha256: "f".repeat(64) });
  const state = candidateEnvironment(candidateRow({
    status: "caption_approved",
    image_key: null,
    image_sha256: null,
    revision: 3,
    capture_state: null,
    capture_task_id: null,
    capture_error: null,
    capture_requested_at: null,
    marketing_assignment_id: "assignment-1",
  }), true, {
    marketingCaptureRequests: [capability.request],
    marketingCatalogRows: [capability.catalog],
  });

  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/candidates/candidate-1/generate-image", {
      method: "POST",
    }),
    state.env,
    "context",
  );

  assert.equal(response.status, 409);
  assert.match((await response.json()).detail, /binding is no longer active/u);
  assert.equal(state.row().capture_state, null);
  assert.equal(state.captureTasks.length, 0);
});

test("image generation rolls back the broker task when candidate queueing fails", async () => {
  const state = candidateEnvironment(candidateRow({
    status: "caption_approved",
    image_key: null,
    image_sha256: null,
    revision: 3,
    capture_state: null,
    capture_task_id: null,
    capture_error: null,
    capture_requested_at: null,
  }), true, { failCandidateQueueUpdate: true });

  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/candidates/candidate-1/generate-image", {
      method: "POST",
    }),
    state.env,
    "context",
  );

  assert.equal(response.status, 500);
  assert.equal(state.row().status, "caption_approved");
  assert.equal(state.row().revision, 3);
  assert.equal(state.row().capture_state, null);
  assert.equal(state.captureTasks.length, 0);
});

test("an enrolled Mac receives revision context without control-plane schedule metadata", async () => {
  const state = candidateEnvironment(candidateRow({
    status: "caption_approved",
    image_key: null,
    image_sha256: null,
    revision: 3,
    capture_state: null,
    capture_task_id: null,
    capture_error: null,
    capture_requested_at: null,
    last_image_feedback_event_id: "feedback-event-1",
    context_snapshot_json: JSON.stringify({
      persona_id: "kr_student",
      guidance: "과장 없이 실제 사용 장면을 보여준다.",
      reference_ids: ["kr-020"],
    }),
    generation_prompt_version: WORKSPACE_GENERATION_PROMPT_VERSION,
    generation_prompt_sha256: "a".repeat(64),
    generation_model: DEFAULT_WORKSPACE_AI_MODEL,
    last_review_rating: 2,
    last_review_tags_json: JSON.stringify(["앱 화면·데이터 오류"]),
    feedback_rules_json: "[]",
    image_inputs_json: JSON.stringify({
      ...candidate().image_inputs,
      trace_items: Array.from({ length: 5 }, (_, index) => ({
        title: `주간 일정 ${index + 1}`,
        day: index,
        days: 1,
        time: null,
        color: null,
        structured: true,
      })),
    }),
  }), true, {
    feedbackRows: [1, 2, 3].map((index) => ({
      candidate_id: `reviewed-${index}`,
      candidate_revision: index,
      stage: "image",
      rating: 2,
      tags_json: JSON.stringify(["이미지 품질·AI 티"]),
      created_at: 100 - index,
    })),
    feedbackEvent: {
      event_id: "feedback-event-1",
      candidate_id: "candidate-1",
      candidate_revision: 2,
      capture_task_id: "previous-capture-task",
      artifact_sha256: "b".repeat(64),
      rating: 2,
      tags_json: JSON.stringify(["앱 화면·데이터 오류"]),
      note: "일정 한 줄이 승인본과 다릅니다.",
    },
  });

  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/candidates/candidate-1/generate-image", {
      method: "POST",
    }),
    state.env,
    "context",
  );

  assert.equal(response.status, 201);
  assert.equal(state.row().capture_state, "queued");
  assert.equal(state.queuedTasks.length, 0);
  assert.equal(state.captureTasks.length, 1);
  assert.equal(state.captureTasks[0][7], "worker_broker");
  const task = JSON.parse(state.captureTasks[0][6]);
  assert.equal(task.kind, "capture");
  assert.equal(task.payload.pipeline, "hosted_workspace_capture_v1");
  assert.equal(task.payload.workspace_id, "cloudflare:trace_demo_kr");
  assert.equal(task.payload.candidate_revision, 4);
  assert.equal(task.payload.image_inputs.device_time, "07:20");
  assert.deepEqual(task.payload.image_inputs.trace_items,
    Array.from({ length: 5 }, (_, index) => ({
      title: `주간 일정 ${index + 1}`,
      day: index,
      days: 1,
      time: null,
      color: null,
    })));
  assert.equal(task.payload.caption, "기존 캡션");
  assert.equal(task.payload.hypothesis, "기존 가설");
  assert.deepEqual(task.payload.reference_ids, ["kr-study-day", "kr-020"]);
  assert.match(task.payload.creative_direction, /기존 Appium 프롬프트/u);
  assert.match(task.payload.creative_direction, /과장 없이 실제 사용 장면/u);
  assert.match(task.payload.creative_direction, /실제 잠금화면처럼 자연스럽게 구성/u);
  assert.match(task.payload.creative_direction, /일정 한 줄이 승인본과 다릅니다/u);
  assert.equal(task.payload.background_intent, "scenery: 이른 아침 캠퍼스 창가");
  assert.equal(task.payload.feedback_context.schema_version, "trace.feedback-context.v1");
  assert.equal(task.payload.feedback_context.rules.length, 1);
  assert.equal(task.payload.feedback_context.rules[0].rule_id, "image-natural-quality");
  assert.equal(
    task.payload.feedback_context.immediate_correction.source_event_id,
    "feedback-event-1",
  );
  assert.equal(task.payload.feedback_context.immediate_correction.note,
    "일정 한 줄이 승인본과 다릅니다.");
  assert.match(task.payload.feedback_context_sha256, /^[a-f0-9]{64}$/u);
});

test("built public workspace has no login form and keeps candidate controls", async () => {
  const markup = await readFile(new URL("../dist/index.html", import.meta.url), "utf8");
  const styles = await readFile(new URL("../dist/static/workspace.css", import.meta.url), "utf8");
  const liveScript = await readFile(
    new URL("../dist/static/workspace-live.js", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(markup, /workspace-entry/);
  assert.doesNotMatch(markup, /워크스페이스 접속 ID/);
  assert.match(liveScript, /워커 소비 확인/u);
  assert.match(liveScript, /이미지 워커 소비 확인/u);
  assert.match(markup, /data-workspace-live aria-busy="false"/);
  assert.match(markup, /오늘 후보 4개 생성/);
  assert.match(markup, /data-account-select/);
  assert.match(markup, /오전 2개·저녁 2개 후보 자동 생성/);
  assert.match(markup, /다음 생성에 반영되는 신호/);
  assert.match(markup, /data-context-select/);
  assert.match(markup, /href="#workspace-content">워크스페이스로 건너뛰기/);
  assert.match(markup, /Cloudflare D1 lease → Mac Appium → R2/);
  assert.match(markup, /data-worker-title/);
  assert.match(markup, /data-worker-manager-open/);
  assert.match(markup, /data-worker-manager/);
  assert.match(markup, /data-worker-admin-form/);
  assert.match(markup, /data-worker-enrollment-form/);
  assert.match(markup, /data-worker-list/);
  assert.match(markup, /id="worker-control-token" name="control-token" type="password" required autocomplete="off"/);
  assert.doesNotMatch(markup, /id="worker-control-token"[^>]*value=/);
  assert.match(markup, /부팅 가능한 Simulator를 동적으로 찾습니다/);
  assert.doesNotMatch(markup, /Cloudflare 검수용 SVG 미리보기/);
  assert.match(markup, /data-candidate-submit/);
  assert.match(markup, /data-image-preview/);
  assert.match(markup, /id="image-preview-dialog"/);
  assert.match(markup, /data-image-preview-close/);
  assert.match(markup, /data-image-preview-image/);
  assert.match(
    styles,
    /@media \(prefers-reduced-transparency: reduce\)[\s\S]*?background:\s*var\(--color-canvas\)/,
  );
});

function workerEventsEnvironment(events) {
  const rows = events.map((event) => ({ ...event }));
  const account = {
    account_id: "trace_demo_kr",
    display_name: "Trace Korea",
    country: "KR",
    language: "ko",
    timezone: "Asia/Seoul",
    morning_time: "07:30",
    evening_time: "19:30",
    generation_enabled: 1,
    next_generation_at: null,
    enabled: 1,
    revision: 1,
  };
  return {
    env: {
      PUBLIC_WORKSPACE_ACCOUNT_ID: account.account_id,
      DB: {
        prepare(sql) {
          return {
            bind(...values) {
              return {
                async first() {
                  if (sql.includes("FROM hosted_workspace_accounts")) return account;
                  throw new Error(`unexpected worker event first SQL: ${sql}`);
                },
                async all() {
                  if (!sql.includes("FROM mac_worker_task_events")) {
                    throw new Error(`unexpected worker event all SQL: ${sql}`);
                  }
                  const [accountId, retainedAfter, limit] = values;
                  return {
                    results: rows
                      .filter((event) => event.account_id === accountId && event.created_at >= retainedAfter)
                      .sort((left, right) =>
                        right.created_at.localeCompare(left.created_at)
                        || right.event_id.localeCompare(left.event_id),
                      )
                      .slice(0, limit),
                  };
                },
                async run() {
                  if (sql.includes("INSERT OR IGNORE INTO hosted_workspace_accounts")) {
                    return { meta: { changes: 0 } };
                  }
                  if (sql.includes("DELETE FROM mac_worker_task_events")) {
                    const [retainedAfter] = values;
                    for (let index = rows.length - 1; index >= 0; index -= 1) {
                      if (rows[index].created_at < retainedAfter) rows.splice(index, 1);
                    }
                    return { meta: { changes: 0 } };
                  }
                  throw new Error(`unexpected worker event run SQL: ${sql}`);
                },
              };
            },
          };
        },
      },
    },
    rows,
  };
}

test("workspace worker events are account-scoped, newest-first, bounded, and redacted", async () => {
  const state = workerEventsEnvironment([
    {
      event_id: "event-1", task_id: "task-1", account_id: "trace_demo_kr", worker_id: "secret-1",
      worker_name: "Studio Mac", task_kind: "capture", event_type: "execution_started",
      failure_code: null, created_at: "2099-09-02T00:00:00.000Z", task_json: "private",
    },
    {
      event_id: "event-2", task_id: "task-2", account_id: "trace_demo_kr", worker_id: "secret-1",
      worker_name: "Studio Mac", task_kind: "capture", event_type: "execution_failed",
      failure_code: "native_capture_failed", created_at: "2099-09-02T00:01:00.000Z", prompt: "private",
    },
    {
      event_id: "event-other", task_id: "task-other", account_id: "trace_jp", worker_id: "secret-2",
      worker_name: "Tokyo Mac", task_kind: "generate_candidates", event_type: "preparation_started",
      failure_code: null, created_at: "2099-09-02T00:02:00.000Z",
    },
    {
      event_id: "event-expired", task_id: "task-expired", account_id: "trace_demo_kr", worker_id: "secret-1",
      worker_name: "Studio Mac", task_kind: "capture", event_type: "preparation_started",
      failure_code: null, created_at: "2000-01-01T00:00:00.000Z",
    },
  ]);

  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/worker-events?limit=2"),
    state.env,
    "context",
  );
  const { events: listed } = await response.json();

  assert.equal(response.status, 200);
  assert.deepEqual(listed.map((event) => event.event_id), ["event-2", "event-1"]);
  assert.deepEqual(Object.keys(listed[0]).sort(), [
    "created_at", "event_id", "event_type", "failure_code", "task_id", "task_kind", "worker_name",
  ]);
  assert.equal(JSON.stringify(listed).includes("secret-1"), false);
  assert.equal(JSON.stringify(listed).includes("private"), false);
  assert.equal(state.rows.some((event) => event.event_id === "event-expired"), false);

  const invalidLimit = await handleHostedWorkspace(
    new Request("https://workspace.example/api/worker-events?limit=101"),
    state.env,
    "context",
  );
  assert.equal(invalidLimit.status, 400);
});

// The persona layer is the only hosted surface that both reads and writes rows across
// several statements, so its tests need a store rather than a canned answer. This is a
// deliberately small D1 stand-in: it recognizes the four statements the persona functions
// issue and keeps the rows in an array.
function personaEnvironment(rows = [], candidateRows = []) {
  const store = rows.map((row) => ({ ...row }));
  const candidates = candidateRows.map((row) => ({ ...row }));
  const workspace = "cloudflare:trace_demo_kr";
  const match = (sql, ...fragments) => fragments.every((fragment) => sql.includes(fragment));
  const env = {
    PUBLIC_WORKSPACE_ACCOUNT_ID: "trace_demo_kr",
    DB: {
      prepare(sql) {
        return {
          bind(...args) {
            return {
              async all() {
                if (match(sql, "SELECT * FROM hosted_workspace_candidates")) {
                  const scoped = sql.includes("AND persona_id = ?")
                    ? candidates.filter(
                        (row) => row.account_id === args[0] && row.persona_id === args[1],
                      )
                    : candidates.filter((row) => row.account_id === args[0]);
                  return { results: [...scoped].sort((a, b) => b.created_at - a.created_at) };
                }
                if (match(sql, "SELECT * FROM hosted_marketing_personas")) {
                  const scoped = sql.includes("AND country = ?")
                    ? store.filter((row) => row.workspace_id === args[0] && row.country === args[1])
                    : store.filter((row) => row.workspace_id === args[0]);
                  return { results: [...scoped].sort((a, b) => b.created_at - a.created_at) };
                }
                return { results: [] };
              },
              async first() {
                if (match(sql, "SELECT * FROM hosted_workspace_candidates")) {
                  return candidates.find(
                    (row) => row.account_id === args[0] && row.candidate_id === args[1],
                  ) ?? null;
                }
                if (match(sql, "SELECT * FROM hosted_marketing_personas", "account_id = ?")) {
                  return store.find(
                    (row) => row.workspace_id === args[0] && row.account_id === args[1],
                  ) ?? null;
                }
                if (match(sql, "hosted_workspace_accounts")) {
                  return {
                    account_id: "trace_demo_kr",
                    display_name: "Trace Korea",
                    country: "KR",
                    language: "ko",
                    timezone: "Asia/Seoul",
                    morning_time: "07:30",
                    evening_time: "19:30",
                    generation_enabled: 1,
                    next_generation_at: null,
                    revision: 1,
                  };
                }
                return null;
              },
              async run() {
                if (match(sql, "DELETE FROM hosted_workspace_candidates")) {
                  const [accountIdValue, candidateIdValue, revision] = args;
                  const index = candidates.findIndex(
                    (row) => row.account_id === accountIdValue
                      && row.candidate_id === candidateIdValue
                      && row.revision === revision,
                  );
                  if (index === -1) return { meta: { changes: 0 } };
                  candidates.splice(index, 1);
                  return { meta: { changes: 1 } };
                }
                if (match(sql, "INSERT INTO hosted_marketing_personas")) {
                  const [
                    workspaceIdValue, accountIdValue, country, identityJson, scheduleJson,
                    status, note, createdAt, updatedAt,
                  ] = args;
                  store.push({
                    workspace_id: workspaceIdValue,
                    account_id: accountIdValue,
                    country,
                    identity_json: identityJson,
                    schedule_json: scheduleJson,
                    status,
                    note,
                    revision: 1,
                    created_at: createdAt,
                    updated_at: updatedAt,
                  });
                  return { meta: { changes: 1 } };
                }
                if (match(sql, "UPDATE hosted_marketing_personas", "identity_json = ?")) {
                  const [identityJson, scheduleJson, note, updatedAt, ws, id, revision] = args;
                  const row = store.find(
                    (entry) => entry.workspace_id === ws && entry.account_id === id,
                  );
                  if (!row || row.revision !== revision) return { meta: { changes: 0 } };
                  Object.assign(row, {
                    identity_json: identityJson,
                    schedule_json: scheduleJson,
                    note,
                    updated_at: updatedAt,
                    revision: row.revision + 1,
                  });
                  return { meta: { changes: 1 } };
                }
                if (match(sql, "UPDATE hosted_marketing_personas", "SET status = ?")) {
                  const [status, updatedAt, ws, id, revision] = args;
                  const row = store.find(
                    (entry) => entry.workspace_id === ws && entry.account_id === id,
                  );
                  if (!row || row.revision !== revision) return { meta: { changes: 0 } };
                  Object.assign(row, {
                    status,
                    updated_at: updatedAt,
                    revision: row.revision + 1,
                  });
                  return { meta: { changes: 1 } };
                }
                return { meta: { changes: 1 } };
              },
            };
          },
        };
      },
    },
  };
  env.ARTIFACTS = { async delete() {} };
  return { env, store, candidates, workspace };
}

function personaBody(overrides = {}) {
  return {
    country: "KR",
    identity: {
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
    },
    schedule: { language: "ko", timezone: "Asia/Seoul" },
    ...overrides,
  };
}

async function personaRequest(env, path, method = "GET", body = null, registry = "context") {
  return handleHostedWorkspace(
    new Request(`https://workspace.example${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    }),
    env,
    registry,
  );
}

test("a hosted persona is created and listed in the shape the local shell renders", async () => {
  const state = personaEnvironment();

  const created = await (await personaRequest(state.env, "/api/personas", "POST", personaBody()))
    .json();

  // The response is the flattened account shape, so one browser file renders both surfaces.
  assert.equal(created.display_name, "이서진");
  assert.equal(created.country, "KR");
  assert.equal(created.language, "ko");
  assert.equal(created.status, "observing");
  assert.equal(created.revision, 1);
  assert.equal(created.identity.occupation, "병동 간호사");
  assert.equal(created.identity.taste.font, "sf_pro_rounded");
  assert.match(created.account_id, /^persona-/u);
  // And it is scoped to the hosted workspace, not to the operating account row.
  assert.equal(created.workspace_id, state.workspace);
  assert.equal(state.store.length, 1);

  const listed = await (await personaRequest(state.env, "/api/personas")).json();
  assert.deepEqual(listed.map((persona) => persona.display_name), ["이서진"]);
});

test("hosted personas are listed within one country", async () => {
  // Two countries whose context documents are both registered; a country without them is
  // refused below rather than quietly created.
  const registry = { countries: { KR: {}, JP: {} } };
  const state = personaEnvironment();
  await personaRequest(state.env, "/api/personas", "POST", personaBody(), registry);
  await personaRequest(state.env, "/api/personas", "POST", personaBody({
    country: "JP",
    identity: { ...personaBody().identity, display_name: "사토" },
  }), registry);

  const korean = await (await personaRequest(state.env, "/api/personas?country=KR")).json();
  const japanese = await (await personaRequest(state.env, "/api/personas?country=JP")).json();
  const all = await (await personaRequest(state.env, "/api/personas")).json();

  assert.deepEqual(korean.map((persona) => persona.display_name), ["이서진"]);
  assert.deepEqual(japanese.map((persona) => persona.display_name), ["사토"]);
  assert.equal(all.length, 2);
});


test("a persona for a country with no context documents is refused", async () => {
  // The country layer only means something if the corpus behind it exists; creating a
  // persona for an unregistered country would put a card on screen that cannot generate.
  const state = personaEnvironment();

  const response = await personaRequest(state.env, "/api/personas", "POST", personaBody({
    country: "JP",
  }));

  assert.equal(response.status, 409);
  assert.match((await response.json()).detail, /context 문서가 아직 등록되지 않았습니다/u);
  assert.equal(state.store.length, 0);
});

test("a hosted persona is edited and its status changed under a revision check", async () => {
  const state = personaEnvironment();
  const created = await (await personaRequest(state.env, "/api/personas", "POST", personaBody()))
    .json();
  const path = `/api/personas/${created.account_id}`;

  const edited = await (await personaRequest(state.env, path, "PUT", {
    ...personaBody(),
    identity: { ...personaBody().identity, concept: "야간 근무 뒤의 하루를 쓰는 간호사" },
    note: "컨셉 조정",
    expected_revision: 1,
  })).json();
  assert.equal(edited.identity.concept, "야간 근무 뒤의 하루를 쓰는 간호사");
  assert.equal(edited.note, "컨셉 조정");
  assert.equal(edited.revision, 2);

  const promoted = await (await personaRequest(state.env, `${path}/status`, "POST", {
    status: "active",
    expected_revision: 2,
  })).json();
  assert.equal(promoted.status, "active");
  assert.equal(promoted.revision, 3);

  const fetched = await (await personaRequest(state.env, path)).json();
  assert.equal(fetched.revision, 3);
  assert.equal(fetched.status, "active");
});

test("a stale revision loses to the write that landed first", async () => {
  const state = personaEnvironment();
  const created = await (await personaRequest(state.env, "/api/personas", "POST", personaBody()))
    .json();
  const path = `/api/personas/${created.account_id}/status`;

  const first = await personaRequest(state.env, path, "POST", {
    status: "active",
    expected_revision: 1,
  });
  const second = await personaRequest(state.env, path, "POST", {
    status: "retired",
    expected_revision: 1,
  });

  assert.equal(first.status, 200);
  assert.equal(second.status, 409);
  const conflict = await second.json();
  assert.match(conflict.detail, /먼저 변경/u);
  // The losing write changed nothing.
  assert.equal(JSON.parse(JSON.stringify(state.store[0])).status, "active");
});

test("a persona missing its required identity is refused rather than half stored", async () => {
  const state = personaEnvironment();

  const noInterests = await personaRequest(state.env, "/api/personas", "POST", personaBody({
    identity: { ...personaBody().identity, interests: [] },
  }));
  const unknownDomain = await personaRequest(state.env, "/api/personas", "POST", personaBody({
    identity: { ...personaBody().identity, domain: "우주비행사" },
  }));
  const unknownSubject = await personaRequest(state.env, "/api/personas", "POST", personaBody({
    identity: {
      ...personaBody().identity,
      taste: { ...personaBody().identity.taste, background_subject: "예쁜 것" },
    },
  }));

  assert.equal(noInterests.status, 400);
  assert.equal(unknownDomain.status, 400);
  assert.equal(unknownSubject.status, 400);
  assert.equal(state.store.length, 0);
});

test("an unknown persona is a 404 rather than an empty card", async () => {
  const state = personaEnvironment();

  const response = await personaRequest(state.env, "/api/personas/persona-missing");

  assert.equal(response.status, 404);
});

function personaCandidate(candidateId, personaId, topic) {
  return {
    candidate_id: candidateId,
    account_id: "trace_demo_kr",
    persona_id: personaId,
    source: "auto",
    country: "KR",
    topic,
    caption: "캡션",
    hypothesis: "가설",
    refs_json: "[]",
    principles_json: "[]",
    appium_prompt: "입력_일정",
    image_inputs_json: JSON.stringify({
      trace_items: ["09:00 통계학"],
      device_time: "07:20",
      background_subject: "scenery",
      background_mood: "늦은 밤 스탠드 불빛",
      language: "ko",
    }),
    ai_verdict: null,
    image_key: null,
    image_sha256: null,
    context_profile_id: null,
    context_snapshot_json: null,
    posting_slot: "manual",
    generation_batch_id: null,
    generation_prompt_version: null,
    generation_prompt_sha256: null,
    generation_model: null,
    feedback_rules_json: "[]",
    status: "awaiting_review",
    review_note: null,
    revision: 1,
    created_at: 100,
    updated_at: 100,
  };
}

async function personaCandidateRequest(env, path, method = "GET", body = null, personaId = null) {
  const headers = body ? { "Content-Type": "application/json" } : {};
  if (personaId) headers["X-Trace-Persona-ID"] = personaId;
  return handleHostedWorkspace(
    new Request(`https://workspace.example${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    }),
    env,
    "context",
  );
}

test("a hosted persona sees only the candidates it wrote", async () => {
  // Two personas under one country shared a single pool: 김도현's screen listed 이서진's
  // drafts, and either could delete the other's.
  const state = personaEnvironment();
  const mine = await (await personaRequest(state.env, "/api/personas", "POST", personaBody())).json();
  const theirs = await (await personaRequest(state.env, "/api/personas", "POST", personaBody({
    identity: { ...personaBody().identity, display_name: "김도현" },
  }))).json();
  state.candidates.push(
    personaCandidate("candidate-1", mine.account_id, "이서진의 후보"),
    personaCandidate("candidate-2", theirs.account_id, "김도현의 후보"),
    personaCandidate("candidate-legacy", null, "페르소나 이전 후보"),
  );

  const listed = await (await personaCandidateRequest(
    state.env, "/api/candidates", "GET", null, mine.account_id,
  )).json();

  assert.deepEqual(listed.map((record) => record.topic), ["이서진의 후보"]);
  assert.equal(listed[0].persona_id, mine.account_id);
});

test("without a persona the hosted list stays the whole country pool", async () => {
  // The pre-persona rows and any surface that has not opened a persona still need this.
  const state = personaEnvironment();
  state.candidates.push(
    personaCandidate("candidate-1", "persona-a", "이서진의 후보"),
    personaCandidate("candidate-legacy", null, "페르소나 이전 후보"),
  );

  const listed = await (await personaCandidateRequest(state.env, "/api/candidates")).json();

  assert.equal(listed.length, 2);
  assert.deepEqual(listed.map((record) => record.persona_id), ["persona-a", null]);
});

test("a candidate written inside a persona is stamped with it", async () => {
  const state = personaEnvironment();
  const persona = await (await personaRequest(
    state.env, "/api/personas", "POST", personaBody(),
  )).json();
  const inserted = [];
  const env = {
    ...state.env,
    DB: {
      prepare(sql) {
        const inner = state.env.DB.prepare(sql);
        return {
          bind(...args) {
            if (sql.includes("INSERT INTO hosted_workspace_candidates")) {
              inserted.push({ sql, args });
              return {
                async run() {
                  // Store enough for the read-back the insert does, with the persona the
                  // statement carried.
                  const columns = sql.slice(sql.indexOf("(") + 1, sql.indexOf(")")).split(",");
                  const personaIndex = columns.findIndex(
                    (name) => name.trim() === "persona_id",
                  );
                  state.candidates.push({
                    ...personaCandidate(args[0], args[personaIndex] ?? null, args[4]),
                    account_id: args[1],
                  });
                  return { meta: { changes: 1 } };
                },
              };
            }
            return inner.bind(...args);
          },
        };
      },
      async batch(statements) {
        for (const statement of statements) await statement.run();
      },
    },
  };

  const response = await personaCandidateRequest(
    env, "/api/candidates", "POST", candidate(), persona.account_id,
  );

  assert.equal(response.status, 201, await response.text());
  // The persona rides in the INSERT, so the row knows who wrote it.
  assert.equal(inserted.length, 1);
  assert.ok(inserted[0].sql.includes("persona_id"));
  assert.ok(inserted[0].args.includes(persona.account_id));
});

test("deleting a candidate from inside a persona no longer looks it up as an account", async () => {
  // The reported bug: the persona id travelled as the account id, so the account lookup
  // searched the country table for it and answered 404.
  const state = personaEnvironment();
  const persona = await (await personaRequest(
    state.env, "/api/personas", "POST", personaBody(),
  )).json();
  state.candidates.push(personaCandidate("candidate-1", persona.account_id, "지울 후보"));

  const response = await personaCandidateRequest(
    state.env, "/api/candidates/candidate-1", "DELETE", { expected_revision: 1 },
    persona.account_id,
  );

  assert.equal(response.status, 204, await response.text());
  assert.equal(state.candidates.length, 0);
});

test("an unknown persona is refused in the persona's own words", async () => {
  const state = personaEnvironment();

  const response = await personaCandidateRequest(
    state.env, "/api/candidates", "GET", null, "persona-missing",
  );

  assert.equal(response.status, 404);
  assert.match((await response.json()).detail, /페르소나를 찾을 수 없습니다/u);
});

test("hosted account proposals stand on the reference index and refuse maker material", async () => {
  const state = personaEnvironment();
  await personaRequest(state.env, "/api/personas", "POST", personaBody());
  let seenPrompt = "";
  const env = {
    ...state.env,
    DB: state.env.DB,
    AI: {
      async run(_model, options) {
        seenPrompt = options.messages[1].content;
        return {
          response: JSON.stringify({
            proposals: [
              { identity: personaBody().identity, reason: "kr-001 직장인 공감 계열" },
              {
                identity: { ...personaBody().identity, display_name: "김도현" },
                reason: "kr-014 질문형 훅",
              },
            ],
          }),
        };
      },
    },
  };

  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/personas/proposals", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ country: "KR" }),
    }),
    env,
    WORKSPACE_CONTEXT,
  );

  assert.equal(response.status, 200);
  const proposals = await response.json();
  // Each proposal is a whole identity plus the evidence, in the shape the create form takes.
  assert.equal(proposals.length, 2);
  assert.equal(proposals[0].identity.display_name, "이서진");
  assert.equal(proposals[0].identity.taste.font, "sf_pro_rounded");
  assert.match(proposals[1].reason, /kr-014/u);
  // The prompt names the ban against the evidence, and quotes the account already running.
  assert.match(seenPrompt, /개발·메이커 소재를 제안하지 마세요/u);
  assert.match(seenPrompt, /소재 통이 오염됩니다/u);
  assert.match(seenPrompt, /- 이서진 \(병동 간호사, office_worker\)/u);
  // And asking produced no rows.
  assert.equal(state.store.length, 1);
});

test("a hosted proposal carrying an unknown token is refused rather than offered", async () => {
  const state = personaEnvironment();
  const env = {
    ...state.env,
    AI: {
      async run() {
        return {
          response: JSON.stringify({
            proposals: [{
              identity: {
                ...personaBody().identity,
                taste: { ...personaBody().identity.taste, background_subject: "예쁜 것" },
              },
              reason: "kr-001",
            }],
          }),
        };
      },
    },
  };

  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/personas/proposals", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ country: "KR" }),
    }),
    env,
    WORKSPACE_CONTEXT,
  );

  // A suggestion the create route would refuse must never reach the card grid.
  assert.equal(response.status, 400);
  assert.equal(state.store.length, 0);
});

test("hosted proposals need Workers AI to be bound", async () => {
  const state = personaEnvironment();

  const response = await handleHostedWorkspace(
    new Request("https://workspace.example/api/personas/proposals", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ country: "KR" }),
    }),
    state.env,
    WORKSPACE_CONTEXT,
  );

  assert.equal(response.status, 503);
});
