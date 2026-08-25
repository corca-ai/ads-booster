import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  candidateResponseSchema,
  generationPrompt,
  handleHostedWorkspace,
  nextDailyGenerationAt,
  normalizeCandidateDraft,
  normalizeContextProfile,
  normalizeHostedAccount,
  normalizeReviewFeedback,
  renderCandidatePreview,
} from "../src/hosted-workspace.js";
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

function candidateEnvironment(initial = candidateRow()) {
  let row = { ...initial };
  const deletedArtifacts = [];
  const DB = {
    prepare(sql) {
      return {
        bind(...values) {
          return {
            async first() {
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
                  status: "awaiting_review",
                  review_note: null,
                  image_key: null,
                  image_sha256: null,
                  revision: row.revision + 1,
                  updated_at: updatedAt,
                };
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
          };
        },
      };
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
      PUBLIC_WORKSPACE_ACCOUNT_ID: "trace_demo_kr",
    },
    deletedArtifacts,
    row: () => row,
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

  assert.match(normalized.appium_prompt, /입력_일정: 09:00 통계학/);
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

test("Workers AI schema requires two morning and two evening-ready candidates", () => {
  const schema = candidateResponseSchema();
  const candidates = schema.properties.candidates;

  assert.equal(candidates.minItems, 4);
  assert.equal(candidates.maxItems, 4);
  assert.deepEqual(candidates.items.properties.posting_slot.enum, ["morning", "evening"]);
  assert.ok(candidates.items.required.includes("posting_slot"));
  assert.ok(candidates.items.required.includes("appium_prompt"));
  assert.ok(candidates.items.required.includes("image_inputs"));
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
  const schema = candidateResponseSchema(profile.country);

  assert.match(prompt, new RegExp(profile.persona_id));
  assert.match(prompt, new RegExp(profile.audience));
  assert.deepEqual(schema.properties.candidates.items.properties.country.enum, ["KR"]);
  assert.deepEqual(
    schema.properties.candidates.items.properties.image_inputs.properties.language.enum,
    ["ko"],
  );
});

test("hosted preview renders candidate schedule and escapes input", () => {
  const normalized = normalizeCandidateDraft(candidate({ topic: "시험 <주간>" }));
  const svg = renderCandidatePreview(normalized);

  assert.match(svg, /시험 &lt;주간&gt;/);
  assert.match(svg, /09:00 통계학/);
  assert.match(svg, /Cloudflare hosted preview · native Appium capture 아님/);
  assert.doesNotMatch(svg, /시험 <주간>/);
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

test("built public workspace has no login form and keeps candidate controls", async () => {
  const markup = await readFile(new URL("../dist/index.html", import.meta.url), "utf8");

  assert.doesNotMatch(markup, /workspace-entry/);
  assert.doesNotMatch(markup, /워크스페이스 접속 ID/);
  assert.match(markup, /data-workspace-live aria-busy="false"/);
  assert.match(markup, /오늘 후보 4개 생성/);
  assert.match(markup, /data-account-select/);
  assert.match(markup, /오전 2개·저녁 2개 후보 자동 생성/);
  assert.match(markup, /다음 생성에 반영되는 신호/);
  assert.match(markup, /data-context-select/);
  assert.match(markup, /data-stat-review/);
  assert.match(markup, /href="#workspace-content">워크스페이스로 건너뛰기/);
  assert.match(markup, /Cloudflare가 일정과 기기 시각을 넣은 검수용 잠금화면/);
  assert.match(markup, /네이티브 Appium 캡처 worker는 별도 Mac 실행 경계/);
  assert.doesNotMatch(markup, /네이티브 캡처 환경\(Appium\/시뮬레이터\)을 연결하기 전까지/);
  assert.match(markup, /data-candidate-submit/);
});
