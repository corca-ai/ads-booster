import { hasRegisteredBrokerWorker } from "./mac-workers.js";

const DEFAULT_ACCOUNT_ID = "trace_demo_kr";
export const DEFAULT_WORKSPACE_AI_MODEL = "@cf/openai/gpt-oss-20b";
export const WORKSPACE_GENERATION_PROMPT_VERSION = "trace.workspace-generation.v3";
const DEFAULT_AI_MAX_TOKENS = 4096;
const DEFAULT_GENERATION_COOLDOWN_SECONDS = 60;
const MAX_CANDIDATES = 200;
const MAX_CONTEXT_PROFILES = 100;
const MAX_REFERENCE_BODIES = 5;
const MAX_REFERENCE_BODY_BYTES = 24_000;
const MAX_HOSTED_ACCOUNTS = 100;
const POSTING_SLOTS = new Set(["morning", "evening", "manual"]);
const MAX_PERSONAS = 200;
const PERSONA_STATUSES = new Set(["proposed", "observing", "active", "retired"]);
const PERSONA_DOMAINS = new Set([
  "sports_fan",
  "idol_fandom",
  "exam_prepper",
  "parenting",
  "office_worker",
  "fitness_crew",
  "pet_owner",
  "cert_student",
  "small_business",
]);
const PERSONA_FONTS = new Set(["sf_pro", "sf_pro_rounded", "sf_compact", "new_york", "sf_mono"]);
const MAX_PERSONA_INTERESTS = 8;
const MAX_ACCOUNT_PROPOSALS = 3;
export const REVIEW_TAGS = Object.freeze([
  "이미지 품질·AI 티",
  "앱 화면·데이터 오류",
  "국가·언어 부적합",
  "계정 페르소나 불일치",
  "컨셉이 약함",
  "기존 게시물과 중복",
  "캡션 부적합",
  "브랜드·정책 위험",
  "기타",
]);
const FEEDBACK_RULE_DEFINITIONS = Object.freeze({
  "caption:국가·언어 부적합": Object.freeze({
    rule_id: "caption-market-language",
    dimension: "persona",
    instruction: "선택한 국가의 실제 언어와 자연스러운 현지 표현만 사용한다.",
  }),
  "caption:계정 페르소나 불일치": Object.freeze({
    rule_id: "caption-persona-fit",
    dimension: "persona",
    instruction: "선택한 대상·상황·문체가 캡션과 가설에 구체적으로 드러나게 한다.",
  }),
  "caption:컨셉이 약함": Object.freeze({
    rule_id: "caption-concept-specificity",
    dimension: "concept",
    instruction: "일반적인 생산성 문구 대신 한 장면과 한 갈등이 보이는 구체적인 컨셉을 만든다.",
  }),
  "caption:기존 게시물과 중복": Object.freeze({
    rule_id: "caption-concept-novelty",
    dimension: "concept",
    instruction: "직전 후보의 주제·훅·일정 조합을 반복하지 않고 다른 장면과 관점을 선택한다.",
  }),
  "caption:캡션 부적합": Object.freeze({
    rule_id: "caption-voice-fit",
    dimension: "caption",
    instruction: "선택한 문체를 지키고 짧고 자연스러운 한 가지 메시지로 캡션을 쓴다.",
  }),
  "caption:브랜드·정책 위험": Object.freeze({
    rule_id: "caption-policy-safety",
    dimension: "policy",
    instruction: "근거 없는 성과 약속, 타사 상표 오용, 민감하거나 오해를 부르는 주장을 피한다.",
  }),
  "image:이미지 품질·AI 티": Object.freeze({
    rule_id: "image-natural-quality",
    dimension: "design",
    instruction: "과도한 합성 느낌과 부자연스러운 장식을 피하고 실제 잠금화면처럼 자연스럽게 구성한다.",
  }),
  "image:앱 화면·데이터 오류": Object.freeze({
    rule_id: "image-ui-data-accuracy",
    dimension: "design",
    instruction: "Trace UI 구조와 승인된 일정·시각·언어를 정확히 보존하고 임의 데이터를 추가하지 않는다.",
  }),
  "image:국가·언어 부적합": Object.freeze({
    rule_id: "image-market-language",
    dimension: "design",
    instruction: "잠금화면의 언어·시간·시각 요소를 선택한 국가 컨텍스트와 일치시킨다.",
  }),
  "image:계정 페르소나 불일치": Object.freeze({
    rule_id: "image-persona-fit",
    dimension: "design",
    instruction: "배경과 일정 장면이 선택한 대상과 상황에서 실제로 사용할 법하게 보이도록 구성한다.",
  }),
  "image:브랜드·정책 위험": Object.freeze({
    rule_id: "image-policy-safety",
    dimension: "policy",
    instruction: "승인되지 않은 브랜드·로고·개인정보·정책 위험 요소를 이미지에 넣지 않는다.",
  }),
});
const ALLOWED_BACKGROUND_SUBJECTS = new Set([
  "scenery",
  "character_kitty",
  "character_other",
  "family_photo",
  "person",
  "pet",
  "minimal",
  "sports_team",
  "none",
]);

export async function handleHostedWorkspace(request, env, contextRegistry, starterProfiles = []) {
  const url = new URL(request.url);
  if (!url.pathname.startsWith("/api/")) return null;

  try {
    const requestedAccountId = accountIdFromRequest(request, env);
    const scopedEnv = withHostedAccount(env, requestedAccountId);
    // A persona is a different layer from the operating account, so it is a different
    // parameter. Sending it as the account id is what made "delete this candidate" answer
    // "워크스페이스 계정을 찾을 수 없습니다".
    const requestedPersonaId = personaIdFromRequest(request);
    if (request.method === "GET" && url.pathname === "/api/auth/session") {
      return json({
        workspace_id: workspaceId(scopedEnv),
        account_id: accountId(scopedEnv),
        member_id: "public",
        display_name: "Trace Team",
        is_admin: false,
      });
    }
    if (request.method === "POST" && url.pathname === "/api/auth/logout") {
      return new Response(null, { status: 204 });
    }
    if (request.method === "GET" && url.pathname === "/api/context-countries") {
      return json(configuredContextCountries(contextRegistry));
    }
    if (request.method === "GET" && url.pathname === "/api/accounts") {
      await ensureDefaultHostedAccount(env);
      return json(await listHostedAccounts(env));
    }
    if (request.method === "POST" && url.pathname === "/api/accounts") {
      const settings = normalizeHostedAccount(await readJson(request), contextRegistry);
      return json(await createHostedAccount(env, settings), 201);
    }
    const accountRoute = url.pathname.match(/^\/api\/accounts\/([^/]+)$/);
    if (accountRoute && ["PATCH", "DELETE"].includes(request.method)) {
      const targetAccountId = safeAccountId(decodeURIComponent(accountRoute[1]));
      const targetEnv = withHostedAccount(env, targetAccountId);
      const body = await readJson(request);
      const revision = positiveInteger(body?.expected_revision, null);
      if (revision === null) throw new WorkspaceHttpError(400, "expected_revision이 필요합니다.");
      if (request.method === "PATCH") {
        return json(await updateHostedAccount(targetEnv, revision, body));
      }
      await disableHostedAccount(targetEnv, revision);
      return new Response(null, { status: 204 });
    }

    await ensureDefaultHostedAccount(env);
    await requireHostedAccount(scopedEnv);
    if (request.method === "GET" && url.pathname === "/api/personas") {
      return json(await listHostedPersonas(scopedEnv, url.searchParams.get("country")));
    }
    if (request.method === "POST" && url.pathname === "/api/personas/proposals") {
      const body = await readOptionalJson(request);
      const country = requiredString(body?.country ?? "KR", "country", 2).toUpperCase();
      assertConfiguredContextCountry(contextRegistry, country);
      return json(
        await proposeAccounts(
          scopedEnv,
          contextRegistry,
          country,
          await listHostedPersonas(scopedEnv, country),
        ),
      );
    }
    if (request.method === "POST" && url.pathname === "/api/personas") {
      const persona = normalizeHostedPersona(await readJson(request), contextRegistry);
      return json(await createHostedPersona(scopedEnv, persona), 201);
    }
    const personaStatusRoute = url.pathname.match(/^\/api\/personas\/([^/]+)\/status$/);
    if (personaStatusRoute && request.method === "POST") {
      const body = await readJson(request);
      return json(
        await setHostedPersonaStatus(
          scopedEnv,
          decodeURIComponent(personaStatusRoute[1]),
          personaStatus(body?.status),
          expectedRevision(body),
        ),
      );
    }
    const personaRoute = url.pathname.match(/^\/api\/personas\/([^/]+)$/);
    if (personaRoute && ["GET", "PUT"].includes(request.method)) {
      const personaId = decodeURIComponent(personaRoute[1]);
      if (request.method === "GET") {
        return json(await requireHostedPersona(scopedEnv, personaId));
      }
      const body = await readJson(request);
      return json(
        await updateHostedPersona(
          scopedEnv,
          personaId,
          normalizeHostedPersonaSettings(body),
          expectedRevision(body),
        ),
      );
    }
    if (request.method === "GET" && url.pathname === "/api/context-profiles") {
      await ensureStarterProfiles(scopedEnv, starterProfiles);
      return json(await listContextProfiles(scopedEnv));
    }
    if (request.method === "POST" && url.pathname === "/api/context-profiles") {
      const profile = normalizeContextProfile(await readJson(request));
      assertConfiguredContextCountry(contextRegistry, profile.country);
      await assertAccountCountry(scopedEnv, profile.country);
      return json(await createContextProfile(scopedEnv, profile), 201);
    }
    const contextRoute = url.pathname.match(/^\/api\/context-profiles\/([^/]+)$/);
    if (contextRoute && ["PATCH", "DELETE"].includes(request.method)) {
      const profileId = decodeURIComponent(contextRoute[1]);
      const body = await readJson(request);
      const revision = positiveInteger(body?.expected_revision, null);
      if (revision === null) throw new WorkspaceHttpError(400, "expected_revision이 필요합니다.");
      if (request.method === "PATCH") {
        const profile = normalizeContextProfile(body);
        assertConfiguredContextCountry(contextRegistry, profile.country);
        await assertAccountCountry(scopedEnv, profile.country);
        return json(
          await updateContextProfile(scopedEnv, profileId, revision, profile),
        );
      }
      if (request.method === "DELETE") {
        await disableContextProfile(scopedEnv, profileId, revision);
        return new Response(null, { status: 204 });
      }
    }
    if (request.method === "GET" && url.pathname === "/api/feedback-summary") {
      return json(await feedbackSummary(scopedEnv, url.searchParams.get("context_profile_id")));
    }
    if (request.method === "GET" && url.pathname === "/api/candidates") {
      return json(
        await listCandidates(scopedEnv, await requirePersonaScope(scopedEnv, requestedPersonaId)),
      );
    }
    if (request.method === "POST" && url.pathname === "/api/candidates") {
      await ensureStarterProfiles(scopedEnv, starterProfiles);
      const body = await readJson(request);
      const profile = Object.prototype.hasOwnProperty.call(body, "context_profile_id")
        ? await optionalContextProfile(scopedEnv, body.context_profile_id)
        : undefined;
      const draft = normalizeCandidateDraft(body);
      await assertAccountCountry(scopedEnv, draft.country);
      const personaId = await requirePersonaScope(scopedEnv, requestedPersonaId);
      return json(await insertCandidate(scopedEnv, draft, "manual", profile, personaId), 201);
    }
    if (request.method === "POST" && url.pathname === "/api/candidates/generate") {
      await ensureStarterProfiles(scopedEnv, starterProfiles);
      const body = await readOptionalJson(request);
      const profile = await resolveContextProfile(scopedEnv, body?.context_profile_id);
      const personaId = await requirePersonaScope(scopedEnv, requestedPersonaId);
      return json(
        await generateCandidates(scopedEnv, contextRegistry, profile, personaId),
        201,
      );
    }

    const route = url.pathname.match(
      /^\/api\/candidates\/([^/]+)(?:\/(review|generate-image|review-image|image))?$/,
    );
    if (!route) return null;
    const candidateId = decodeURIComponent(route[1]);
    const action = route[2];
    if (request.method === "POST" && action === "review") {
      return json(await reviewCandidate(scopedEnv, candidateId, await readJson(request)));
    }
    if (request.method === "POST" && action === "generate-image") {
      return json(await generateCandidateImage(scopedEnv, candidateId), 201);
    }
    if (request.method === "POST" && action === "review-image") {
      return json(await reviewCandidateImage(scopedEnv, candidateId, await readJson(request)));
    }
    if (request.method === "GET" && action === "image") {
      return readCandidateImage(scopedEnv, candidateId);
    }
    if (request.method === "PATCH" && !action) {
      await ensureStarterProfiles(scopedEnv, starterProfiles);
      const body = await readJson(request);
      const revision = positiveInteger(body?.expected_revision, null);
      if (revision === null) throw new WorkspaceHttpError(400, "expected_revision이 필요합니다.");
      const profile = Object.prototype.hasOwnProperty.call(body, "context_profile_id")
        ? await optionalContextProfile(scopedEnv, body.context_profile_id)
        : undefined;
      const draft = normalizeCandidateDraft(body);
      await assertAccountCountry(scopedEnv, draft.country);
      return json(
        await updateCandidate(scopedEnv, candidateId, revision, draft, profile),
      );
    }
    if (request.method === "DELETE" && !action) {
      const body = await readJson(request);
      const revision = positiveInteger(body?.expected_revision, null);
      if (revision === null) throw new WorkspaceHttpError(400, "expected_revision이 필요합니다.");
      await deleteCandidate(scopedEnv, candidateId, revision);
      return new Response(null, { status: 204 });
    }
    return null;
  } catch (error) {
    const status = error instanceof WorkspaceHttpError ? error.status : 500;
    return json(
      { detail: status === 500 ? "워크스페이스 요청을 처리하지 못했습니다." : error.message },
      status,
    );
  }
}

export async function runHostedWorkspaceSchedules(env, contextRegistry, starterProfiles = []) {
  await ensureDefaultHostedAccount(env);
  await redispatchHostedCaptureTasks(env);
  const now = new Date();
  const due = await env.DB.prepare(
    `SELECT account_id FROM hosted_workspace_accounts
     WHERE enabled = 1 AND generation_enabled = 1 AND next_generation_at <= ?
     ORDER BY next_generation_at LIMIT 20`,
  )
    .bind(now.toISOString())
    .all();
  for (const row of due.results) {
    const retryAt = new Date(now.getTime() + 15 * 60_000).toISOString();
    const claimed = await env.DB.prepare(
      `UPDATE hosted_workspace_accounts SET next_generation_at = ?, updated_at = ?
       WHERE account_id = ? AND enabled = 1 AND generation_enabled = 1
         AND next_generation_at <= ?`,
    )
      .bind(retryAt, Date.now() / 1000, row.account_id, now.toISOString())
      .run();
    if (claimed.meta.changes !== 1) continue;
    const scopedEnv = withHostedAccount(env, row.account_id);
    try {
      await ensureStarterProfiles(scopedEnv, starterProfiles);
      const [account, profile] = await Promise.all([
        requireHostedAccount(scopedEnv),
        resolveContextProfile(scopedEnv, null),
      ]);
      await generateCandidates(scopedEnv, contextRegistry, profile);
      const next = nextDailyGenerationAt(
        account.timezone,
        account.morning_time,
        new Date(now.getTime() + 60_000),
      ).toISOString();
      await env.DB.prepare(
        `UPDATE hosted_workspace_accounts SET next_generation_at = ?, updated_at = ?
         WHERE account_id = ? AND enabled = 1 AND generation_enabled = 1`,
      )
        .bind(next, Date.now() / 1000, account.account_id)
        .run();
    } catch (error) {
      console.error("hosted workspace scheduled generation failed", {
        account_id: row.account_id,
        message: error instanceof Error ? error.message : "unknown error",
      });
    }
  }
}

async function redispatchHostedCaptureTasks(env) {
  if (!env.TASK_QUEUE || typeof env.TASK_QUEUE.send !== "function") return;
  const retryBefore = new Date(Date.now() - 5 * 60_000).toISOString();
  const pending = await env.DB.prepare(
    `SELECT task_id, task_json FROM hosted_workspace_capture_tasks
     WHERE state = 'queued' AND dispatch_mode = 'legacy_queue'
       AND (last_dispatched_at IS NULL OR last_dispatched_at <= ?)
     ORDER BY created_at LIMIT 50`,
  )
    .bind(retryBefore)
    .all();
  for (const task of pending.results) {
    const now = new Date().toISOString();
    const claimed = await env.DB.prepare(
      `UPDATE hosted_workspace_capture_tasks SET last_dispatched_at = ?, updated_at = ?
       WHERE task_id = ? AND state = 'queued' AND dispatch_mode = 'legacy_queue'
         AND (last_dispatched_at IS NULL OR last_dispatched_at <= ?)`,
    )
      .bind(now, now, task.task_id, retryBefore)
      .run();
    if (claimed.meta.changes !== 1) continue;
    try {
      await env.TASK_QUEUE.send(task.task_json, { contentType: "text" });
    } catch (error) {
      console.error("hosted capture task redispatch failed", {
        task_id: task.task_id,
        message: error instanceof Error ? error.message : "unknown error",
      });
    }
  }
}

export function normalizeCandidateDraft(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new WorkspaceHttpError(400, "후보 입력 형식이 올바르지 않습니다.");
  }
  const topic = requiredString(input.topic, "topic", 200);
  const country = requiredString(input.country ?? "KR", "country", 2).toUpperCase();
  if (!/^[A-Z]{2}$/.test(country)) {
    throw new WorkspaceHttpError(400, "country는 두 자리 국가 코드여야 합니다.");
  }
  const caption = requiredString(input.caption, "caption", 10000);
  const hypothesis = requiredString(input.hypothesis, "hypothesis", 2000);
  const refsUsed = referenceIdList(input.refs_used);
  const principlesApplied = principleList(input.principles_applied);
  const imageInputs = normalizeImageInputs(input.image_inputs);
  const requestedPrompt = optionalString(input.appium_prompt ?? input.shooting_order, 10000);
  const appiumPrompt = isCompleteAppiumPrompt(requestedPrompt)
    ? requestedPrompt
    : appiumPromptFrom(imageInputs);
  const postingSlot = optionalString(input.posting_slot, 16) || "manual";
  if (!POSTING_SLOTS.has(postingSlot)) {
    throw new WorkspaceHttpError(400, "posting_slot은 morning, evening, manual 중 하나여야 합니다.");
  }
  return {
    topic,
    country,
    caption,
    hypothesis,
    refs_used: refsUsed,
    principles_applied: principlesApplied,
    appium_prompt: appiumPrompt,
    image_inputs: imageInputs,
    posting_slot: postingSlot,
  };
}

export function candidateResponseSchema(country = "KR", referenceIds = []) {
  const language = languageForCountry(country);
  const referenceItems = referenceIds.length
    ? { type: "string", enum: [...referenceIds] }
    : { type: "string" };
  // Workers AI's grammar rejects `uniqueItems`; normalization and batch validation below
  // enforce the same uniqueness invariants before any candidate can be persisted.
  return {
    type: "object",
    properties: {
      candidates: {
        type: "array",
        minItems: 4,
        maxItems: 4,
        items: {
          type: "object",
          additionalProperties: false,
          properties: {
            topic: { type: "string", minLength: 1 },
            country: { type: "string", enum: [country] },
            caption: { type: "string", minLength: 1 },
            hypothesis: { type: "string", minLength: 1 },
            posting_slot: { type: "string", enum: ["morning", "evening"] },
            refs_used: {
              type: "array",
              minItems: referenceIds.length ? 1 : 0,
              maxItems: referenceIds.length,
              items: referenceItems,
            },
            principles_applied: {
              type: "array",
              minItems: 1,
              items: { type: "integer", minimum: 1 },
            },
            appium_prompt: { type: "string" },
            image_inputs: {
              type: "object",
              additionalProperties: false,
              properties: {
                trace_items: { type: "array", minItems: 5, maxItems: 7, items: { type: "string" } },
                device_time: { type: "string" },
                background_subject: { type: "string", enum: [...ALLOWED_BACKGROUND_SUBJECTS] },
                background_mood: { type: "string" },
                language: { type: "string", enum: [language] },
              },
              required: ["trace_items", "device_time", "background_subject", "background_mood", "language"],
            },
          },
          required: ["topic", "country", "caption", "hypothesis", "posting_slot", "refs_used", "principles_applied", "appium_prompt", "image_inputs"],
        },
      },
    },
    required: ["candidates"],
  };
}

export function validateGeneratedCandidateBatch(drafts, profile) {
  if (drafts.length !== 4) throw new Error("후보는 정확히 4개여야 합니다.");
  requireUniqueGeneratedValues(drafts, "topic", "후보 주제가 서로 달라야 합니다.");
  requireUniqueGeneratedValues(drafts, "caption", "후보 캡션이 서로 달라야 합니다.");

  const slots = drafts.reduce((counts, draft) => {
    counts[draft.posting_slot] = (counts[draft.posting_slot] ?? 0) + 1;
    return counts;
  }, {});
  if (slots.morning !== 2 || slots.evening !== 2) {
    throw new Error("오전 후보 2개와 저녁 후보 2개가 필요합니다.");
  }

  const allowedReferences = new Set(profile.reference_ids ?? []);
  const scheduleSignatures = new Set();
  for (const draft of drafts) {
    if (draft.principles_applied.length === 0) {
      throw new Error("자동 생성 후보에는 적용 원리가 한 개 이상 필요합니다.");
    }
    if (new Set(draft.principles_applied).size !== draft.principles_applied.length) {
      throw new Error("자동 생성 후보의 적용 원리는 중복될 수 없습니다.");
    }
    if (allowedReferences.size > 0 && draft.refs_used.length === 0) {
      throw new Error("자동 생성 후보에는 선택한 레퍼런스가 한 개 이상 필요합니다.");
    }
    if (draft.refs_used.some((referenceId) => !allowedReferences.has(referenceId))) {
      throw new Error("자동 생성 후보가 선택한 페르소나 밖의 레퍼런스를 사용했습니다.");
    }
    const traceItems = draft.image_inputs.trace_items;
    if (traceItems.length < 5 || traceItems.length > 7) {
      throw new Error("자동 생성 일정은 5~7개여야 합니다.");
    }
    if (traceItems.some((item) => !/^(?:[01]\d|2[0-3]):[0-5]\d\s+\S/u.test(item))) {
      throw new Error("자동 생성 일정은 모두 'HH:MM 제목' 형식이어야 합니다.");
    }
    const scheduleSignature = traceItems.map(normalizeGeneratedValue).join("\n");
    if (scheduleSignatures.has(scheduleSignature)) {
      throw new Error("후보마다 서로 다른 일정 장면이 필요합니다.");
    }
    scheduleSignatures.add(scheduleSignature);
  }
}

function requireUniqueGeneratedValues(drafts, field, message) {
  const normalized = drafts.map((draft) => normalizeGeneratedValue(draft[field]));
  if (new Set(normalized).size !== normalized.length) throw new Error(message);
}

function normalizeGeneratedValue(value) {
  return value.normalize("NFKC").toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

export function normalizeContextProfile(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new WorkspaceHttpError(400, "컨텍스트 입력 형식이 올바르지 않습니다.");
  }
  const country = requiredString(input.country ?? "KR", "country", 2).toUpperCase();
  if (!/^[A-Z]{2}$/.test(country)) {
    throw new WorkspaceHttpError(400, "country는 두 자리 국가 코드여야 합니다.");
  }
  const personaId = requiredString(input.persona_id, "persona_id", 80);
  if (!/^[a-z0-9][a-z0-9_-]{1,79}$/.test(personaId)) {
    throw new WorkspaceHttpError(400, "persona_id는 영문 소문자, 숫자, -, _만 사용할 수 있습니다.");
  }
  const referenceIds = referenceIdList(input.reference_ids ?? []);
  return {
    country,
    name: requiredString(input.name, "name", 80),
    persona_id: personaId,
    audience: requiredString(input.audience, "audience", 500),
    situation: requiredString(input.situation, "situation", 500),
    tone: requiredString(input.tone, "tone", 300),
    guidance: requiredString(input.guidance, "guidance", 2000),
    reference_ids: referenceIds,
  };
}

export function normalizeHostedAccount(input, contextRegistry) {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new WorkspaceHttpError(400, "계정 입력 형식이 올바르지 않습니다.");
  }
  const country = requiredString(input.country ?? "KR", "country", 2).toUpperCase();
  assertConfiguredContextCountry(contextRegistry, country);
  const timezone = requiredString(input.timezone ?? defaultTimezone(country), "timezone", 80);
  assertTimezone(timezone);
  const morningTime = clockTime(input.morning_time ?? "07:30", "morning_time");
  const eveningTime = clockTime(input.evening_time ?? "19:30", "evening_time");
  if (morningTime === eveningTime) {
    throw new WorkspaceHttpError(400, "오전·저녁 게시 시각은 서로 달라야 합니다.");
  }
  return {
    account_id: safeAccountId(input.account_id),
    display_name: requiredString(input.display_name, "display_name", 80),
    country,
    language: languageForCountry(country),
    timezone,
    morning_time: morningTime,
    evening_time: eveningTime,
    generation_enabled: input.generation_enabled === true,
  };
}

  /**
   * Validate one persona the hosted control plane is asked to create.
   *
   * Personas are the layer between a country and its posts: many people writing under one
   * country's operating account. The vocabulary checked here — domain, background subject,
   * font, status — is the same closed vocabulary the local models enforce, because the same
   * generator reads both.
   */
export function accountProposalSchema() {
  return {
    type: "object",
    properties: {
      proposals: {
        type: "array",
        minItems: 2,
        maxItems: MAX_ACCOUNT_PROPOSALS,
        items: {
          type: "object",
          properties: {
            identity: {
              type: "object",
              properties: {
                display_name: { type: "string" },
                age: { type: "integer", minimum: 13, maximum: 99 },
                region: { type: "string" },
                occupation: { type: "string" },
                concept: { type: "string" },
                domain: { type: "string", enum: [...PERSONA_DOMAINS] },
                interests: {
                  type: "array",
                  minItems: 1,
                  maxItems: MAX_PERSONA_INTERESTS,
                  items: { type: "string" },
                },
                life_rhythm: { type: "string" },
                taste: {
                  type: "object",
                  properties: {
                    background_subject: {
                      type: "string",
                      enum: [...ALLOWED_BACKGROUND_SUBJECTS],
                    },
                    background_mood: { type: "string" },
                    font: { type: "string", enum: [...PERSONA_FONTS] },
                  },
                  required: ["background_subject", "background_mood", "font"],
                },
              },
              required: [
                "display_name", "age", "region", "occupation", "concept",
                "domain", "interests", "life_rhythm", "taste",
              ],
            },
            reason: { type: "string" },
          },
          required: ["identity", "reason"],
        },
      },
    },
    required: ["proposals"],
  };
}

export function accountProposalPrompt(contextDocuments, country, existing) {
  const listed = existing.length
    ? existing
        .map((persona) => `- ${persona.display_name} (${persona.identity.occupation}, `
          + `${persona.identity.domain}): ${persona.identity.concept}`)
        .join("\n")
    : "아직 이 국가에 운영 중인 계정이 없습니다.";
  return `당신은 Trace 마케팅 계정을 제안하는 에이전트입니다.
아래 context 의 레퍼런스 인덱스와 이미 운영 중인 계정 목록만 근거로,
${country} 계정 후보 ${MAX_ACCOUNT_PROPOSALS}개를 서로 다른 화자 유형으로 제안하세요.

[context]
${contextDocuments}

[이미 운영 중인 계정]
${listed}
위 계정들과 도메인·직업·컨셉이 겹치지 않게 하세요.

[반드시 지킬 규칙]
1. 개발·메이커 소재를 제안하지 마세요. 인덱스에 1인개발 계열의 성과가 있더라도 우리가 쓸
   수 있는 유형이 아닙니다. 계정 필드에 배포·코딩·개발·출시·앱 제작 같은 말이 들어가면 그
   계정이 쓰는 모든 글의 소재 통이 오염됩니다. 직업이 개발자인 계정도 제안하지 마세요.
2. display_name 은 실제로 있을 법한 한국 이름입니다. 별명·영문 아이디를 쓰지 마세요.
3. region 은 "서울 성동구" 처럼 구·군까지, occupation 은 실제 직업명 하나입니다.
4. concept 은 사람이 읽고 바로 그림이 그려지는 한 문장입니다.
5. interests 는 3개이고 고유명사급으로 구체적이어야 합니다. "운동", "음악" 은 금지입니다.
6. life_rhythm 은 시각이 드러나는 구체적인 하루입니다.
7. reason 에는 이 유형이 통한다고 보는 이유와 근거 레퍼런스 id 를 함께 적습니다.`;
}

async function proposeAccounts(env, contextRegistry, country, existing) {
  if (!env.AI || typeof env.AI.run !== "function") {
    throw new WorkspaceHttpError(503, "Cloudflare Workers AI 연결이 준비되지 않았습니다.");
  }
  const prompt = accountProposalPrompt(
    contextForCountry(contextRegistry, country),
    country,
    existing,
  );
  let result;
  try {
    result = await env.AI.run(env.WORKSPACE_AI_MODEL || DEFAULT_WORKSPACE_AI_MODEL, {
      messages: [
        {
          role: "system",
          content: `당신은 Trace 마케팅 계정 제안기입니다. 제공된 문서만 근거로 ${country} 계정을 제안하세요.`,
        },
        { role: "user", content: prompt },
      ],
      max_tokens: positiveInteger(env.WORKSPACE_AI_MAX_TOKENS, DEFAULT_AI_MAX_TOKENS),
      temperature: 0.7,
      response_format: { type: "json_schema", json_schema: accountProposalSchema() },
    });
  } catch {
    throw new WorkspaceHttpError(502, "Cloudflare AI 계정 제안에 실패했습니다.");
  }
  return aiAccountProposals(result).slice(0, MAX_ACCOUNT_PROPOSALS).map(normalizeAccountProposal);
}

export function aiAccountProposals(result) {
  let response = result?.response ?? result?.choices?.[0]?.message?.content ?? result;
  if (typeof response === "string") response = JSON.parse(response);
  if (!response || typeof response !== "object" || !Array.isArray(response.proposals)) {
    throw new WorkspaceHttpError(502, "AI 계정 제안 형식이 올바르지 않습니다.");
  }
  return response.proposals;
}

export function normalizeAccountProposal(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new WorkspaceHttpError(502, "AI 계정 제안 형식이 올바르지 않습니다.");
  }
  // The identity goes through the same validator a hand-written persona does, so a
  // suggestion can never carry a token the create route would refuse.
  return {
    identity: personaIdentity(input.identity),
    reason: requiredString(input.reason, "reason", 400),
  };
}

export function normalizeHostedPersona(input, contextRegistry) {
  const country = requiredString(input?.country ?? "KR", "country", 2).toUpperCase();
  assertConfiguredContextCountry(contextRegistry, country);
  const settings = normalizeHostedPersonaSettings(input);
  return { country, ...settings, status: personaStatus(input?.status ?? "observing") };
}

export function normalizeHostedPersonaSettings(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new WorkspaceHttpError(400, "페르소나 입력 형식이 올바르지 않습니다.");
  }
  return {
    identity: personaIdentity(input.identity),
    schedule: personaSchedule(input.schedule),
    note: optionalString(input.note, 400),
  };
}

function personaIdentity(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new WorkspaceHttpError(400, "페르소나 정체성 입력이 필요합니다.");
  }
  const age = positiveInteger(input.age, null);
  if (age === null || age < 13 || age > 99) {
    throw new WorkspaceHttpError(400, "나이는 13에서 99 사이여야 합니다.");
  }
  const interests = Array.isArray(input.interests)
    ? input.interests.map((value) => requiredString(value, "interests", 40))
    : [];
  if (!interests.length || interests.length > MAX_PERSONA_INTERESTS) {
    throw new WorkspaceHttpError(400, "관심사는 1개 이상 8개 이하로 입력해 주세요.");
  }
  const domain = requiredString(input.domain, "domain", 40);
  if (!PERSONA_DOMAINS.has(domain)) {
    throw new WorkspaceHttpError(400, `도메인 토큰이 올바르지 않습니다: ${domain}`);
  }
  return {
    display_name: requiredString(input.display_name, "display_name", 40),
    age,
    region: requiredString(input.region, "region", 40),
    occupation: requiredString(input.occupation, "occupation", 60),
    concept: requiredString(input.concept, "concept", 200),
    domain,
    interests,
    life_rhythm: requiredString(input.life_rhythm, "life_rhythm", 200),
    taste: personaTaste(input.taste),
  };
}

function personaTaste(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new WorkspaceHttpError(400, "배경 취향 입력이 필요합니다.");
  }
  const subject = requiredString(input.background_subject, "background_subject", 40);
  if (!ALLOWED_BACKGROUND_SUBJECTS.has(subject)) {
    throw new WorkspaceHttpError(400, `배경 소재 토큰이 올바르지 않습니다: ${subject}`);
  }
  const font = optionalString(input.font, 40) || "sf_pro";
  if (!PERSONA_FONTS.has(font)) {
    throw new WorkspaceHttpError(400, `폰트 토큰이 올바르지 않습니다: ${font}`);
  }
  return {
    background_subject: subject,
    background_mood: requiredString(input.background_mood, "background_mood", 40),
    font,
  };
}

function personaSchedule(input) {
  const source = input && typeof input === "object" && !Array.isArray(input) ? input : {};
  const timezone = requiredString(source.timezone ?? "Asia/Seoul", "timezone", 64);
  assertTimezone(timezone);
  return {
    language: requiredString(source.language ?? "ko", "language", 8),
    timezone,
    morning_time: clockTime(source.morning_time ?? "08:00", "morning_time"),
    evening_time: clockTime(source.evening_time ?? "20:00", "evening_time"),
    generation_enabled: source.generation_enabled === true,
  };
}

function personaStatus(value) {
  const status = requiredString(value ?? "", "status", 20);
  if (!PERSONA_STATUSES.has(status)) {
    throw new WorkspaceHttpError(400, `페르소나 상태가 올바르지 않습니다: ${status}`);
  }
  return status;
}

function expectedRevision(body) {
  const revision = positiveInteger(body?.expected_revision, null);
  if (revision === null) throw new WorkspaceHttpError(400, "expected_revision이 필요합니다.");
  return revision;
}

async function listHostedPersonas(env, country) {
  const scope = country ? requiredString(country, "country", 2).toUpperCase() : null;
  const statement = scope
    ? env.DB.prepare(
        `SELECT * FROM hosted_marketing_personas
         WHERE workspace_id = ? AND country = ? ORDER BY created_at DESC LIMIT ?`,
      ).bind(workspaceId(env), scope, MAX_PERSONAS)
    : env.DB.prepare(
        `SELECT * FROM hosted_marketing_personas
         WHERE workspace_id = ? ORDER BY created_at DESC LIMIT ?`,
      ).bind(workspaceId(env), MAX_PERSONAS);
  const result = await statement.all();
  return result.results.map(hostedPersonaFromRow);
}

async function createHostedPersona(env, persona) {
  const now = Date.now() / 1000;
  const personaId = `persona-${crypto.randomUUID()}`;
  await env.DB.prepare(
    `INSERT INTO hosted_marketing_personas
      (workspace_id, account_id, country, identity_json, schedule_json,
       status, note, revision, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)`,
  )
    .bind(
      workspaceId(env),
      personaId,
      persona.country,
      JSON.stringify(persona.identity),
      JSON.stringify(persona.schedule),
      persona.status,
      persona.note,
      now,
      now,
    )
    .run();
  return requireHostedPersona(env, personaId);
}

async function updateHostedPersona(env, personaId, settings, revision) {
  const current = await requireHostedPersona(env, personaId);
  const result = await env.DB.prepare(
    `UPDATE hosted_marketing_personas
     SET identity_json = ?, schedule_json = ?, note = ?,
         revision = revision + 1, updated_at = ?
     WHERE workspace_id = ? AND account_id = ? AND revision = ?`,
  )
    .bind(
      JSON.stringify(settings.identity),
      JSON.stringify(settings.schedule),
      settings.note,
      Date.now() / 1000,
      workspaceId(env),
      current.account_id,
      revision,
    )
    .run();
  if (result.meta.changes !== 1) throw personaConflict();
  return requireHostedPersona(env, personaId);
}

async function setHostedPersonaStatus(env, personaId, status, revision) {
  const current = await requireHostedPersona(env, personaId);
  const result = await env.DB.prepare(
    `UPDATE hosted_marketing_personas
     SET status = ?, revision = revision + 1, updated_at = ?
     WHERE workspace_id = ? AND account_id = ? AND revision = ?`,
  )
    .bind(status, Date.now() / 1000, workspaceId(env), current.account_id, revision)
    .run();
  if (result.meta.changes !== 1) throw personaConflict();
  return requireHostedPersona(env, personaId);
}

async function requireHostedPersona(env, personaId) {
  const row = await env.DB.prepare(
    "SELECT * FROM hosted_marketing_personas WHERE workspace_id = ? AND account_id = ?",
  )
    .bind(workspaceId(env), personaId)
    .first();
  if (!row) throw new WorkspaceHttpError(404, "페르소나를 찾을 수 없습니다.");
  return hostedPersonaFromRow(row);
}

function personaConflict() {
  return new WorkspaceHttpError(409, "페르소나가 다른 요청에서 먼저 변경되었습니다.");
}

function hostedPersonaFromRow(row) {
  // Flattened the way the local `/api/accounts` response is, so one browser file renders a
  // persona card whether the row came from SQLite or from D1.
  const identity = JSON.parse(row.identity_json);
  const schedule = JSON.parse(row.schedule_json);
  return {
    workspace_id: row.workspace_id,
    account_id: row.account_id,
    display_name: identity.display_name,
    country: row.country,
    language: schedule.language,
    timezone: schedule.timezone,
    morning_time: schedule.morning_time,
    evening_time: schedule.evening_time,
    generation_enabled: schedule.generation_enabled === true,
    identity,
    status: row.status,
    note: row.note,
    revision: row.revision,
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

async function ensureDefaultHostedAccount(env) {
  const id = accountId(env);
  const now = Date.now() / 1000;
  const next = nextDailyGenerationAt("Asia/Seoul", "07:30").toISOString();
  await env.DB.prepare(
    `INSERT OR IGNORE INTO hosted_workspace_accounts
      (account_id, display_name, country, language, timezone, morning_time, evening_time,
       generation_enabled, next_generation_at, enabled, revision, created_at, updated_at)
     VALUES (?, 'Trace Korea', 'KR', 'ko', 'Asia/Seoul', '07:30', '19:30', 1, ?, 1, 1, ?, ?)`,
  )
    .bind(id, next, now, now)
    .run();
}

async function listHostedAccounts(env) {
  const result = await env.DB.prepare(
    `SELECT * FROM hosted_workspace_accounts
     WHERE enabled = 1 ORDER BY country, display_name LIMIT ?`,
  )
    .bind(MAX_HOSTED_ACCOUNTS)
    .all();
  return result.results.map(hostedAccountFromRow);
}

async function createHostedAccount(env, settings) {
  const now = Date.now() / 1000;
  const next = settings.generation_enabled
    ? nextDailyGenerationAt(settings.timezone, settings.morning_time).toISOString()
    : null;
  try {
    await env.DB.prepare(
      `INSERT INTO hosted_workspace_accounts
        (account_id, display_name, country, language, timezone, morning_time, evening_time,
         generation_enabled, next_generation_at, enabled, revision, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?)`,
    )
      .bind(
        settings.account_id,
        settings.display_name,
        settings.country,
        settings.language,
        settings.timezone,
        settings.morning_time,
        settings.evening_time,
        Number(settings.generation_enabled),
        next,
        now,
        now,
      )
      .run();
  } catch (error) {
    const existing = await env.DB.prepare(
      "SELECT account_id FROM hosted_workspace_accounts WHERE account_id = ?",
    )
      .bind(settings.account_id)
      .first();
    if (existing) throw new WorkspaceHttpError(409, "이미 사용 중인 계정 ID입니다.");
    throw error;
  }
  return requireHostedAccount(withHostedAccount(env, settings.account_id));
}

async function updateHostedAccount(env, revision, input) {
  const current = await requireHostedAccount(env);
  const displayName = Object.prototype.hasOwnProperty.call(input, "display_name")
    ? requiredString(input.display_name, "display_name", 80)
    : current.display_name;
  const timezone = Object.prototype.hasOwnProperty.call(input, "timezone")
    ? requiredString(input.timezone, "timezone", 80)
    : current.timezone;
  assertTimezone(timezone);
  const morningTime = Object.prototype.hasOwnProperty.call(input, "morning_time")
    ? clockTime(input.morning_time, "morning_time")
    : current.morning_time;
  const eveningTime = Object.prototype.hasOwnProperty.call(input, "evening_time")
    ? clockTime(input.evening_time, "evening_time")
    : current.evening_time;
  if (morningTime === eveningTime) {
    throw new WorkspaceHttpError(400, "오전·저녁 게시 시각은 서로 달라야 합니다.");
  }
  const generationEnabled = Object.prototype.hasOwnProperty.call(input, "generation_enabled")
    ? booleanField(input.generation_enabled, "generation_enabled")
    : current.generation_enabled;
  const scheduleChanged = timezone !== current.timezone || morningTime !== current.morning_time;
  const next = generationEnabled
    ? (!current.generation_enabled || scheduleChanged
        ? nextDailyGenerationAt(timezone, morningTime).toISOString()
        : current.next_generation_at)
    : null;
  const result = await env.DB.prepare(
    `UPDATE hosted_workspace_accounts
     SET display_name = ?, timezone = ?, morning_time = ?, evening_time = ?,
         generation_enabled = ?, next_generation_at = ?, revision = revision + 1, updated_at = ?
     WHERE account_id = ? AND enabled = 1 AND revision = ?`,
  )
    .bind(
      displayName,
      timezone,
      morningTime,
      eveningTime,
      Number(generationEnabled),
      next,
      Date.now() / 1000,
      accountId(env),
      revision,
    )
    .run();
  if (result.meta.changes !== 1) {
    throw new WorkspaceHttpError(409, "계정 설정이 다른 요청에서 먼저 변경되었습니다.");
  }
  return requireHostedAccount(env);
}

async function disableHostedAccount(env, revision) {
  const result = await env.DB.prepare(
    `UPDATE hosted_workspace_accounts
     SET enabled = 0, generation_enabled = 0, next_generation_at = NULL,
         revision = revision + 1, updated_at = ?
     WHERE account_id = ? AND enabled = 1 AND revision = ?`,
  )
    .bind(Date.now() / 1000, accountId(env), revision)
    .run();
  if (result.meta.changes !== 1) {
    throw new WorkspaceHttpError(409, "계정 설정이 다른 요청에서 먼저 변경되었습니다.");
  }
}

async function requireHostedAccount(env) {
  const row = await env.DB.prepare(
    "SELECT * FROM hosted_workspace_accounts WHERE account_id = ? AND enabled = 1",
  )
    .bind(accountId(env))
    .first();
  if (!row) throw new WorkspaceHttpError(404, "워크스페이스 계정을 찾을 수 없습니다.");
  return hostedAccountFromRow(row);
}

function hostedAccountFromRow(row) {
  return {
    account_id: row.account_id,
    display_name: row.display_name,
    country: row.country,
    language: row.language,
    timezone: row.timezone,
    morning_time: row.morning_time,
    evening_time: row.evening_time,
    generation_enabled: row.generation_enabled === 1,
    next_generation_at: row.next_generation_at,
    revision: row.revision,
  };
}

async function assertAccountCountry(env, country) {
  const account = await requireHostedAccount(env);
  if (account.country !== country) {
    throw new WorkspaceHttpError(
      400,
      `계정 국가 ${account.country}와 후보·컨텍스트 국가 ${country}가 일치해야 합니다.`,
    );
  }
}

export function nextDailyGenerationAt(timezone, time, after = new Date()) {
  assertTimezone(timezone);
  const normalizedTime = clockTime(time, "generation_time");
  const [hour, minute] = normalizedTime.split(":").map(Number);
  const current = zonedParts(after, timezone);
  let desired = {
    year: current.year,
    month: current.month,
    day: current.day,
    hour,
    minute,
  };
  let next = instantForZonedParts(desired, timezone);
  if (next.getTime() <= after.getTime()) {
    const tomorrow = new Date(Date.UTC(desired.year, desired.month - 1, desired.day + 1));
    desired = {
      ...desired,
      year: tomorrow.getUTCFullYear(),
      month: tomorrow.getUTCMonth() + 1,
      day: tomorrow.getUTCDate(),
    };
    next = instantForZonedParts(desired, timezone);
  }
  return next;
}

function instantForZonedParts(desired, timezone) {
  const desiredWall = Date.UTC(
    desired.year,
    desired.month - 1,
    desired.day,
    desired.hour,
    desired.minute,
  );
  let timestamp = desiredWall;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const actual = zonedParts(new Date(timestamp), timezone);
    const actualWall = Date.UTC(actual.year, actual.month - 1, actual.day, actual.hour, actual.minute);
    const adjustment = desiredWall - actualWall;
    timestamp += adjustment;
    if (adjustment === 0) break;
  }
  return new Date(timestamp);
}

function zonedParts(date, timezone) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return {
    year: Number(values.year),
    month: Number(values.month),
    day: Number(values.day),
    hour: Number(values.hour),
    minute: Number(values.minute),
  };
}

function clockTime(value, field) {
  const normalized = requiredString(value, field, 5);
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(normalized)) {
    throw new WorkspaceHttpError(400, `${field}은 HH:MM 형식이어야 합니다.`);
  }
  return normalized;
}

function assertTimezone(value) {
  try {
    new Intl.DateTimeFormat("en", { timeZone: value }).format();
  } catch (error) {
    throw new WorkspaceHttpError(400, "timezone은 올바른 IANA 시간대여야 합니다.");
  }
}

function defaultTimezone(country) {
  return {
    KR: "Asia/Seoul",
    JP: "Asia/Tokyo",
    TW: "Asia/Taipei",
    US: "America/New_York",
    DE: "Europe/Berlin",
    FR: "Europe/Paris",
    BR: "America/Sao_Paulo",
  }[country] ?? "UTC";
}

async function ensureStarterProfiles(env, profiles) {
  if (!Array.isArray(profiles) || profiles.length === 0) return;
  const account = await requireHostedAccount(env);
  const now = Date.now() / 1000;
  const statements = profiles.filter((input) => input.country === account.country).map((input) => {
    const profile = normalizeContextProfile(input);
    const profileId = requiredString(input.profile_id, "profile_id", 100);
    return env.DB.prepare(
      `INSERT OR IGNORE INTO hosted_workspace_context_profiles
        (account_id, profile_id, country, name, persona_id, audience, situation, tone,
         guidance, reference_ids_json, source, is_default, enabled, revision, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'starter', ?, 1, 1, ?, ?)`,
    ).bind(
      accountId(env),
      profileId,
      profile.country,
      profile.name,
      profile.persona_id,
      profile.audience,
      profile.situation,
      profile.tone,
      profile.guidance,
      JSON.stringify(profile.reference_ids),
      input.is_default === true ? 1 : 0,
      now,
      now,
    );
  });
  if (statements.length) await env.DB.batch(statements);
}

async function listContextProfiles(env) {
  const result = await env.DB.prepare(
    `SELECT * FROM hosted_workspace_context_profiles
     WHERE account_id = ? AND enabled = 1
     ORDER BY is_default DESC, country, name LIMIT ?`,
  )
    .bind(accountId(env), MAX_CONTEXT_PROFILES)
    .all();
  return result.results.map(contextProfileFromRow);
}

async function createContextProfile(env, profile) {
  const now = Date.now() / 1000;
  const profileId = `custom-${crypto.randomUUID()}`;
  await env.DB.prepare(
    `INSERT INTO hosted_workspace_context_profiles
      (account_id, profile_id, country, name, persona_id, audience, situation, tone,
       guidance, reference_ids_json, source, is_default, enabled, revision, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'custom', 0, 1, 1, ?, ?)`,
  )
    .bind(
      accountId(env),
      profileId,
      profile.country,
      profile.name,
      profile.persona_id,
      profile.audience,
      profile.situation,
      profile.tone,
      profile.guidance,
      JSON.stringify(profile.reference_ids),
      now,
      now,
    )
    .run();
  return requireContextProfile(env, profileId);
}

async function updateContextProfile(env, profileId, revision, profile) {
  const result = await env.DB.prepare(
    `UPDATE hosted_workspace_context_profiles
     SET country = ?, name = ?, persona_id = ?, audience = ?, situation = ?, tone = ?,
         guidance = ?, reference_ids_json = ?, revision = revision + 1, updated_at = ?
     WHERE account_id = ? AND profile_id = ? AND enabled = 1 AND revision = ?`,
  )
    .bind(
      profile.country,
      profile.name,
      profile.persona_id,
      profile.audience,
      profile.situation,
      profile.tone,
      profile.guidance,
      JSON.stringify(profile.reference_ids),
      Date.now() / 1000,
      accountId(env),
      profileId,
      revision,
    )
    .run();
  if (result.meta.changes !== 1) await contextMutationConflict(env, profileId);
  return requireContextProfile(env, profileId);
}

async function disableContextProfile(env, profileId, revision) {
  const result = await env.DB.prepare(
    `UPDATE hosted_workspace_context_profiles
     SET enabled = 0, is_default = 0, revision = revision + 1, updated_at = ?
     WHERE account_id = ? AND profile_id = ? AND enabled = 1 AND revision = ?`,
  )
    .bind(Date.now() / 1000, accountId(env), profileId, revision)
    .run();
  if (result.meta.changes !== 1) await contextMutationConflict(env, profileId);
}

async function contextMutationConflict(env, profileId) {
  const existing = await findContextProfile(env, profileId, false);
  if (!existing) throw new WorkspaceHttpError(404, "컨텍스트를 찾을 수 없습니다.");
  throw new WorkspaceHttpError(409, "컨텍스트가 다른 요청에서 먼저 변경되었습니다.");
}

async function optionalContextProfile(env, profileId) {
  if (profileId === undefined || profileId === null || profileId === "") return null;
  return requireContextProfile(env, requiredString(profileId, "context_profile_id", 100));
}

async function resolveContextProfile(env, requestedProfileId) {
  const requested = await optionalContextProfile(env, requestedProfileId);
  if (requested) return requested;
  const row = await env.DB.prepare(
    `SELECT * FROM hosted_workspace_context_profiles
     WHERE account_id = ? AND enabled = 1
     ORDER BY is_default DESC, country, name LIMIT 1`,
  )
    .bind(accountId(env))
    .first();
  if (!row) throw new WorkspaceHttpError(409, "사용할 수 있는 생성 컨텍스트가 없습니다.");
  return contextProfileFromRow(row);
}

async function requireContextProfile(env, profileId) {
  const profile = await findContextProfile(env, profileId, true);
  if (!profile) throw new WorkspaceHttpError(404, "컨텍스트를 찾을 수 없습니다.");
  return profile;
}

async function findContextProfile(env, profileId, enabledOnly) {
  const row = await env.DB.prepare(
    `SELECT * FROM hosted_workspace_context_profiles
     WHERE account_id = ? AND profile_id = ?${enabledOnly ? " AND enabled = 1" : ""}`,
  )
    .bind(accountId(env), profileId)
    .first();
  return row ? contextProfileFromRow(row) : null;
}

function contextProfileFromRow(row) {
  return {
    profile_id: row.profile_id,
    country: row.country,
    name: row.name,
    persona_id: row.persona_id,
    audience: row.audience,
    situation: row.situation,
    tone: row.tone,
    guidance: row.guidance,
    reference_ids: JSON.parse(row.reference_ids_json),
    source: row.source,
    is_default: row.is_default === 1,
    revision: row.revision,
  };
}

async function requirePersonaScope(env, personaId) {
  // No persona means the country-wide view, which is what the pre-persona rows and any
  // surface without a persona open still need. A named persona has to exist.
  if (!personaId) return null;
  const persona = await requireHostedPersona(env, personaId);
  return persona.account_id;
}

async function listCandidates(env, personaId = null) {
  const scope = personaId ? " AND persona_id = ?" : "";
  const parameters = personaId
    ? [accountId(env), personaId, MAX_CANDIDATES]
    : [accountId(env), MAX_CANDIDATES];
  const result = await env.DB.prepare(
    `SELECT * FROM hosted_workspace_candidates
     WHERE account_id = ?${scope} ORDER BY created_at DESC LIMIT ?`,
  )
    .bind(...parameters)
    .all();
  return result.results.map(candidateFromRow);
}

async function generateCandidates(env, contextRegistry, profile, personaId = null) {
  if (!env.AI || typeof env.AI.run !== "function") {
    throw new WorkspaceHttpError(503, "Cloudflare Workers AI 연결이 준비되지 않았습니다.");
  }
  const contextDocuments = contextForCountry(contextRegistry, profile.country, profile);
  await claimGenerationWindow(env);
  const [sharedInstruction, account, learnedFeedback] = await Promise.all([
    loadSharedInstruction(env),
    requireHostedAccount(env),
    feedbackSummary(env, profile.profile_id),
  ]);
  const prompt = generationPrompt(
    contextDocuments,
    sharedInstruction,
    profile,
    account,
    learnedFeedback,
  );
  const model = env.WORKSPACE_AI_MODEL || DEFAULT_WORKSPACE_AI_MODEL;
  let detail = "AI 응답 형식이 올바르지 않습니다.";
  let acceptedDrafts = null;
  let acceptedPrompt = null;

  for (let attempt = 0; attempt < 2; attempt += 1) {
    const attemptPrompt = attempt === 0
      ? prompt
      : `${prompt}\n\n직전 응답 검증 오류: ${detail}\n형식을 정확히 지켜 다시 만드세요.`;
    let result;
    try {
      result = await env.AI.run(model, {
        messages: [
          {
            role: "system",
            content: `당신은 Trace 마케팅 후보 생성기입니다. 제공된 문서만 근거로 ${profile.country} / ${languageForCountry(profile.country)} 후보를 만드세요.`,
          },
          {
            role: "user",
            content: attemptPrompt,
          },
        ],
        max_tokens: positiveInteger(env.WORKSPACE_AI_MAX_TOKENS, DEFAULT_AI_MAX_TOKENS),
        temperature: 0.4,
        response_format: {
          type: "json_schema",
          json_schema: candidateResponseSchema(profile.country, profile.reference_ids),
        },
      });
    } catch (error) {
      throw new WorkspaceHttpError(502, "Cloudflare AI 후보 생성에 실패했습니다.");
    }
    try {
      const raw = aiCandidates(result);
      const drafts = raw.map(normalizeCandidateDraft);
      validateGeneratedCandidateBatch(drafts, profile);
      acceptedDrafts = drafts;
      acceptedPrompt = attemptPrompt;
      break;
    } catch (error) {
      detail = error instanceof Error ? error.message : detail;
    }
  }
  if (!acceptedDrafts || !acceptedPrompt) {
    throw new WorkspaceHttpError(502, `AI 후보 형식 검증에 실패했습니다: ${detail}`);
  }
  return insertCandidates(env, acceptedDrafts, "auto", profile, personaId, {
    prompt_version: WORKSPACE_GENERATION_PROMPT_VERSION,
    prompt_sha256: await sha256(acceptedPrompt),
    model,
    feedback_rules: learnedFeedback.active_rules,
  });
}

async function claimGenerationWindow(env) {
  const now = Math.floor(Date.now() / 1000);
  const cooldown = positiveInteger(
    env.WORKSPACE_GENERATION_COOLDOWN_SECONDS,
    DEFAULT_GENERATION_COOLDOWN_SECONDS,
  );
  const result = await env.DB.prepare(
    `INSERT INTO hosted_workspace_generation_locks (account_id, last_started_at)
     VALUES (?, ?)
     ON CONFLICT(account_id) DO UPDATE SET last_started_at = excluded.last_started_at
     WHERE hosted_workspace_generation_locks.last_started_at <= ?`,
  )
    .bind(accountId(env), now, now - cooldown)
    .run();
  if (result.meta.changes !== 1) {
    throw new WorkspaceHttpError(429, `${cooldown}초 후에 다시 생성해 주세요.`);
  }
}

async function loadSharedInstruction(env) {
  const account = await env.DB.prepare(
    `SELECT instructions.body AS body
     FROM marketing_accounts AS accounts
     JOIN shared_instructions AS instructions
       ON instructions.revision = accounts.instruction_revision
     WHERE accounts.account_id = ?`,
  )
    .bind(accountId(env))
    .first();
  if (typeof account?.body === "string") return account.body;
  const active = await env.DB.prepare(
    "SELECT body FROM shared_instructions WHERE active = 1 LIMIT 1",
  ).first();
  return typeof active?.body === "string" ? active.body : "";
}

export function generationPrompt(
  contextDocuments,
  sharedInstruction,
  profile,
  account = null,
  learnedFeedback = null,
) {
  const morningTime = account?.morning_time ?? "07:30";
  const eveningTime = account?.evening_time ?? "19:30";
  const learnedRules = feedbackRulePrompt(learnedFeedback?.active_rules ?? []);
  return `아래 Trace context, 계정 지침, 선택한 국가·페르소나 컨텍스트만 근거로 서로 다른 게시물 후보 4개를 만드세요.
posting_slot=morning 후보 2개, posting_slot=evening 후보 2개를 정확히 만드세요.
오전 슬롯 기준 시각은 ${morningTime}, 저녁 슬롯 기준 시각은 ${eveningTime}${account?.timezone ? ` (${account.timezone})` : ""}입니다.
사실 문서 밖의 수치나 기능은 주장하지 마세요. appium_prompt와 image_inputs를 비우지 마세요.
trace_items는 실제 하루처럼 읽히는 'HH:MM 제목' 일정 5~7개를 정확히 만드세요.
topic, caption, trace_items 전체 일정은 네 후보에서 각각 서로 달라야 하며 복사하지 마세요.

[Trace 기본 context]
${contextDocuments}

[현재 계정 공통 지침]
${sharedInstruction || "추가 지침 없음"}

[선택한 컨텍스트]
국가: ${profile.country}
이름: ${profile.name}
페르소나 ID: ${profile.persona_id}
대상: ${profile.audience}
상황: ${profile.situation}
문체: ${profile.tone}
추가 지침: ${profile.guidance}
레퍼런스 ID: ${profile.reference_ids.join(", ") || "없음"}

[같은 계정·페르소나의 반복 피드백]
${learnedRules}`;
}

function feedbackRulePrompt(rules) {
  if (!Array.isArray(rules) || rules.length === 0) {
    return "- 아직 동일 단계에서 3회 이상 반복된 1~2점 반려 규칙 없음";
  }
  const labels = {
    caption: "캡션",
    concept: "컨셉",
    design: "디자인",
    persona: "페르소나",
    policy: "브랜드·정책",
  };
  return ["caption", "concept", "design", "persona", "policy"]
    .map((dimension) => {
      const selected = rules.filter((rule) => rule.dimension === dimension);
      if (selected.length === 0) return null;
      return `[${labels[dimension]} 규칙]\n${selected.map((rule) => `- ${rule.instruction}`).join("\n")}`;
    })
    .filter(Boolean)
    .join("\n\n");
}

export function contextForCountry(registry, country, profile = null) {
  if (typeof registry === "string") return registry;
  const globalContext = typeof registry?.global === "string" ? registry.global : "";
  const countryContext = registry?.countries?.[country];
  if (typeof countryContext !== "string" || !countryContext.trim()) {
    throw new WorkspaceHttpError(
      409,
      `${country} 국가 context 문서가 아직 등록되지 않았습니다. context manifest를 확장해 주세요.`,
    );
  }
  const sections = [globalContext, countryContext];
  const references = referenceBodiesForProfile(registry, country, profile);
  if (references) sections.push(references);
  return sections.join("\n\n").trim();
}

/**
 * Only the reference records a persona names are inlined. The corpus is far larger than one prompt
 * can carry, so an unbounded profile is trimmed rather than allowed to blow the context window.
 */
function referenceBodiesForProfile(registry, country, profile) {
  const corpus = registry?.referenceBodies?.[country];
  if (!corpus || !Array.isArray(profile?.reference_ids)) return "";
  const sections = [];
  let bytes = 0;
  for (const id of profile.reference_ids) {
    if (sections.length >= MAX_REFERENCE_BODIES) break;
    const body = corpus[id];
    if (typeof body !== "string" || !body.trim()) continue;
    const section = `[레퍼런스 본문: ${id}]\n${body}`;
    const size = utf8Length(section);
    if (bytes + size > MAX_REFERENCE_BODY_BYTES) break;
    bytes += size;
    sections.push(section);
  }
  if (sections.length === 0) return "";
  return `[선택한 레퍼런스 본문]
문장 구조와 훅 전개 방식만 참고하고 문장이나 사실을 그대로 옮기지 마세요.

${sections.join("\n\n")}`;
}

function utf8Length(text) {
  return new TextEncoder().encode(text).length;
}

function configuredContextCountries(registry) {
  if (typeof registry === "string") return [{ country: "KR", language: "ko" }];
  return Object.keys(registry?.countries ?? {})
    .sort()
    .map((country) => ({ country, language: languageForCountry(country) }));
}

function assertConfiguredContextCountry(registry, country) {
  if (!configuredContextCountries(registry).some((entry) => entry.country === country)) {
    throw new WorkspaceHttpError(
      409,
      `${country} 국가 context 문서가 아직 등록되지 않았습니다. context manifest를 확장해 주세요.`,
    );
  }
}

function languageForCountry(country) {
  return {
    KR: "ko",
    JP: "ja",
    TW: "zh",
    US: "en",
    DE: "de",
    FR: "fr",
    BR: "pt",
  }[country] ?? "en";
}

function assertProfileCountry(profile, country) {
  if (profile && profile.country !== country) {
    throw new WorkspaceHttpError(
      400,
      `후보 국가 ${country}와 선택한 context 국가 ${profile.country}가 일치해야 합니다.`,
    );
  }
}

export function aiCandidates(result) {
  let response = result?.response ?? result?.choices?.[0]?.message?.content ?? result;
  if (typeof response === "string") response = JSON.parse(response);
  if (!response || typeof response !== "object" || !Array.isArray(response.candidates)) {
    throw new Error("candidates 배열이 없습니다.");
  }
  return response.candidates;
}

async function insertCandidates(
  env,
  drafts,
  source,
  profile = null,
  personaId = null,
  generationProvenance = null,
) {
  const now = Date.now() / 1000;
  const contextSnapshot = profile ? JSON.stringify(profile) : null;
  const batchId = source === "auto" ? crypto.randomUUID() : null;
  const inserts = drafts.map((draft, index) => {
    assertProfileCountry(profile, draft.country);
    const candidateId = crypto.randomUUID();
    return {
      candidateId,
      statement: env.DB.prepare(
        `INSERT INTO hosted_workspace_candidates
        (candidate_id, account_id, source, country, topic, caption, hypothesis,
         refs_json, principles_json, appium_prompt, image_inputs_json, ai_verdict,
         context_profile_id, context_snapshot_json, posting_slot, generation_batch_id,
         generation_prompt_version, generation_prompt_sha256, generation_model,
         feedback_rules_json, persona_id,
         status, revision, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
               'awaiting_review', 1, ?, ?)`,
      ).bind(
        candidateId,
        accountId(env),
        source,
        draft.country,
        draft.topic,
        draft.caption,
        draft.hypothesis,
        JSON.stringify(draft.refs_used),
        JSON.stringify(draft.principles_applied),
        draft.appium_prompt,
        JSON.stringify(draft.image_inputs),
        source === "auto" ? "기계 검수 통과 · 필수 필드/국가/언어/시간 형식" : null,
        profile?.profile_id ?? null,
        contextSnapshot,
        draft.posting_slot,
        batchId,
        generationProvenance?.prompt_version ?? null,
        generationProvenance?.prompt_sha256 ?? null,
        generationProvenance?.model ?? null,
        JSON.stringify(generationProvenance?.feedback_rules ?? []),
        personaId,
        now + index / 1000,
        now + index / 1000,
      ),
    };
  });
  await env.DB.batch(inserts.map(({ statement }) => statement));
  return Promise.all(inserts.map(({ candidateId }) => requireCandidate(env, candidateId)));
}

async function insertCandidate(env, draft, source, profile = null, personaId = null) {
  return (await insertCandidates(env, [draft], source, profile, personaId))[0];
}

async function updateCandidate(env, candidateId, revision, draft, requestedProfile) {
  const current = await requireCandidate(env, candidateId);
  const profile = requestedProfile === undefined ? current.context_profile : requestedProfile;
  assertProfileCountry(profile, draft.country);
  const result = await env.DB.prepare(
    `UPDATE hosted_workspace_candidates
     SET country = ?, topic = ?, caption = ?, hypothesis = ?, refs_json = ?,
         principles_json = ?, appium_prompt = ?, image_inputs_json = ?,
         context_profile_id = ?, context_snapshot_json = ?, posting_slot = ?,
         ai_verdict = NULL, status = 'awaiting_review', review_note = NULL, image_key = NULL,
         generation_prompt_version = NULL, generation_prompt_sha256 = NULL,
         generation_model = NULL, feedback_rules_json = '[]',
         image_sha256 = NULL, capture_state = NULL, capture_task_id = NULL,
         capture_error = NULL, capture_requested_at = NULL,
         last_review_rating = NULL, last_review_tags_json = '[]',
         revision = revision + 1, updated_at = ?
     WHERE account_id = ? AND candidate_id = ? AND revision = ?`,
  )
    .bind(
      draft.country,
      draft.topic,
      draft.caption,
      draft.hypothesis,
      JSON.stringify(draft.refs_used),
      JSON.stringify(draft.principles_applied),
      draft.appium_prompt,
      JSON.stringify(draft.image_inputs),
      profile?.profile_id ?? null,
      profile ? JSON.stringify(profile) : null,
      draft.posting_slot,
      Date.now() / 1000,
      accountId(env),
      candidateId,
      revision,
    )
    .run();
  if (result.meta.changes !== 1) {
    throw new WorkspaceHttpError(409, "후보가 다른 요청에서 먼저 변경되었습니다.");
  }
  await deleteCandidateArtifact(env, current.image_path);
  return requireCandidate(env, candidateId);
}

async function deleteCandidate(env, candidateId, revision) {
  const current = await requireCandidate(env, candidateId);
  const result = await env.DB.prepare(
    `DELETE FROM hosted_workspace_candidates
     WHERE account_id = ? AND candidate_id = ? AND revision = ?`,
  )
    .bind(accountId(env), candidateId, revision)
    .run();
  if (result.meta.changes !== 1) {
    throw new WorkspaceHttpError(409, "후보가 다른 요청에서 먼저 변경되었습니다.");
  }
  await deleteCandidateArtifact(env, current.image_path);
}

async function deleteCandidateArtifact(env, key) {
  if (!key) return;
  try {
    await env.ARTIFACTS.delete(key);
  } catch (error) {
    console.error("hosted workspace candidate artifact cleanup failed", {
      key,
      message: error instanceof Error ? error.message : "unknown error",
    });
  }
}

async function reviewCandidate(env, candidateId, body) {
  const accepted = booleanField(body?.accepted, "accepted");
  const revision = positiveInteger(body?.expected_revision, null);
  if (revision === null) throw new WorkspaceHttpError(400, "expected_revision이 필요합니다.");
  const feedback = normalizeReviewFeedback(body, accepted);
  assertReviewTagsForStage("caption", feedback);
  const current = await requireCandidate(env, candidateId);
  if (current.status !== "awaiting_review") {
    throw new WorkspaceHttpError(409, "검수 대기 중인 캡션·주제를 찾을 수 없습니다.");
  }
  const status = accepted ? "caption_approved" : "rejected";
  await transitionCandidate(
    env,
    current,
    candidateId,
    revision,
    "awaiting_review",
    status,
    feedback,
  );
  return requireCandidate(env, candidateId);
}

async function generateCandidateImage(env, candidateId) {
  const candidate = await requireCandidate(env, candidateId);
  if (candidate.status !== "caption_approved") {
    throw new WorkspaceHttpError(409, "캡션·주제가 승인된 후보만 이미지를 만들 수 있습니다.");
  }
  if (candidate.capture_state === "queued") {
    throw new WorkspaceHttpError(409, "이미지 캡처가 이미 Mac worker를 기다리고 있습니다.");
  }
  const dispatchMode = "worker_broker";
  const taskId = crypto.randomUUID();
  const runId = crypto.randomUUID();
  const now = new Date().toISOString();
  const nextRevision = candidate.revision + 1;
  const idempotencyKey = `hosted:${accountId(env)}:${candidateId}:${candidate.revision}`;
  const referenceIds = [...new Set([
    ...candidate.refs_used,
    ...(candidate.context_profile?.reference_ids ?? []),
  ])];
  if (referenceIds.length > 16) {
    throw new WorkspaceHttpError(400, "이미지 생성에 사용할 레퍼런스는 16개 이하여야 합니다.");
  }
  const generatedFeedbackRules = candidate.generation_provenance?.feedback_rules ?? [];
  const immediateRetryRules = candidate.status === "caption_approved"
    ? candidate.review_tags
        .map((tag) => ({ tag, rule: FEEDBACK_RULE_DEFINITIONS[`image:${tag}`] }))
        .filter(({ rule }) => Boolean(rule))
        .map(({ tag, rule }) => ({
          ...rule,
          stage: "image",
          tag,
          evidence: "current_candidate_rejection",
        }))
    : [];
  const captureFeedbackRules = [...new Map(
    [...generatedFeedbackRules, ...immediateRetryRules].map((rule) => [rule.rule_id, rule]),
  ).values()];
  const designFeedback = captureFeedbackRules
    .filter((rule) => ["design", "policy"].includes(rule.dimension))
    .map((rule) => rule.instruction)
    .join("\n");
  const creativeDirection = [
    candidate.shooting_order,
    candidate.context_profile?.guidance,
    designFeedback ? `[검수에서 학습된 디자인 규칙]\n${designFeedback}` : null,
  ]
    .filter(Boolean)
    .join("\n\n");
  const backgroundIntent = [
    candidate.image_inputs.background_subject,
    candidate.image_inputs.background_mood,
  ].join(": ");
  const body = {
    schema_version: "1",
    task_id: taskId,
    run_id: runId,
    account_id: accountId(env),
    kind: "capture",
    idempotency_key: idempotencyKey,
    payload: {
      pipeline: "hosted_workspace_capture_v1",
      candidate_id: candidateId,
      candidate_revision: nextRevision,
      country: candidate.country,
      topic: candidate.topic,
      caption: candidate.caption,
      hypothesis: candidate.hypothesis,
      reference_ids: referenceIds,
      creative_direction: creativeDirection,
      background_intent: backgroundIntent,
      appium_prompt: candidate.shooting_order,
      image_inputs: candidate.image_inputs,
      context_profile: candidate.context_profile,
      feedback_rules: captureFeedbackRules,
    },
    created_at: now,
    credential_ref: null,
  };
  const taskStatement = env.DB.prepare(
    `INSERT INTO hosted_workspace_capture_tasks
      (task_id, run_id, account_id, candidate_id, candidate_revision, idempotency_key,
       task_json, state, dispatch_mode, created_at, updated_at)
     SELECT ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?
     WHERE EXISTS (
       SELECT 1 FROM mac_workers WHERE state != 'revoked'
     ) AND EXISTS (
       SELECT 1 FROM hosted_workspace_candidates
       WHERE account_id = ? AND candidate_id = ?
         AND status = 'caption_approved' AND revision = ?
     )`,
  )
    .bind(
      taskId,
      runId,
      accountId(env),
      candidateId,
      nextRevision,
      idempotencyKey,
      JSON.stringify(body),
      dispatchMode,
      now,
      now,
      accountId(env),
      candidateId,
      candidate.revision,
    );
  const candidateStatement = env.DB.prepare(
    `UPDATE hosted_workspace_candidates
     SET capture_state = 'queued', capture_task_id = ?, capture_error = NULL,
         capture_requested_at = ?, revision = revision + 1, updated_at = ?
     WHERE account_id = ? AND candidate_id = ? AND status = 'caption_approved' AND revision = ?
       AND EXISTS (SELECT 1 FROM mac_workers WHERE state != 'revoked')`,
  )
    .bind(taskId, now, Date.now() / 1000, accountId(env), candidateId, candidate.revision);
  const [taskCreated, candidateQueued] = await env.DB.batch([taskStatement, candidateStatement]);
  if (taskCreated.meta.changes !== 1 || candidateQueued.meta.changes !== 1) {
    if (!(await hasRegisteredBrokerWorker(env.DB))) {
      throw new WorkspaceHttpError(
        503,
        "등록된 Mac worker가 없어 이미지를 만들 수 없습니다. Mac 연결 관리에서 worker를 먼저 등록해 주세요.",
      );
    }
    throw new WorkspaceHttpError(409, "후보가 다른 요청에서 먼저 변경되었습니다.");
  }
  return requireCandidate(env, candidateId);
}

async function reviewCandidateImage(env, candidateId, body) {
  const accepted = booleanField(body?.accepted, "accepted");
  const revision = positiveInteger(body?.expected_revision, null);
  if (revision === null) throw new WorkspaceHttpError(400, "expected_revision이 필요합니다.");
  const feedback = normalizeReviewFeedback(body, accepted);
  assertReviewTagsForStage("image", feedback);
  const current = await requireCandidate(env, candidateId);
  if (current.status !== "image_awaiting_review") {
    throw new WorkspaceHttpError(409, "검수 대기 중인 이미지를 찾을 수 없습니다.");
  }
  const status = accepted ? "submitted" : "caption_approved";
  const feedbackStatement = await feedbackEventStatement(
    env,
    current,
    "image",
    accepted,
    feedback,
    "image_awaiting_review",
    revision,
  );
  const transitionStatement = env.DB.prepare(
    `UPDATE hosted_workspace_candidates
     SET status = ?, review_note = ?, image_key = ?, image_sha256 = ?,
         last_review_rating = ?, last_review_tags_json = ?,
         revision = revision + 1, updated_at = ?
     WHERE account_id = ? AND candidate_id = ? AND status = 'image_awaiting_review' AND revision = ?`,
  )
    .bind(
      status,
      feedback.note || null,
      accepted ? current.image_path : null,
      accepted ? current.image_sha256 : null,
      feedback.rating,
      JSON.stringify(feedback.tags),
      Date.now() / 1000,
      accountId(env),
      candidateId,
      revision,
    );
  const [recorded, transitioned] = await env.DB.batch([feedbackStatement, transitionStatement]);
  await assertReviewBatchCommitted(env, candidateId, recorded, transitioned);
  if (!accepted && current.image_path) await env.ARTIFACTS.delete(current.image_path);
  return requireCandidate(env, candidateId);
}

async function readCandidateImage(env, candidateId) {
  const candidate = await requireCandidate(env, candidateId);
  if (!candidate.image_path) throw new WorkspaceHttpError(404, "후보 이미지가 없습니다.");
  const object = await env.ARTIFACTS.get(candidate.image_path);
  if (!object) throw new WorkspaceHttpError(404, "후보 이미지 artifact가 없습니다.");
  return new Response(object.body, {
    headers: {
      "content-type": object.httpMetadata?.contentType || "image/svg+xml; charset=utf-8",
      etag: candidate.image_sha256 || "",
      "cache-control": "public, max-age=300",
    },
  });
}

async function transitionCandidate(env, current, candidateId, revision, from, to, feedback) {
  const feedbackStatement = await feedbackEventStatement(
    env,
    current,
    "caption",
    to === "caption_approved",
    feedback,
    from,
    revision,
  );
  const transitionStatement = env.DB.prepare(
    `UPDATE hosted_workspace_candidates
     SET status = ?, review_note = ?, last_review_rating = ?, last_review_tags_json = ?,
         revision = revision + 1, updated_at = ?
     WHERE account_id = ? AND candidate_id = ? AND status = ? AND revision = ?`,
  )
    .bind(
      to,
      feedback.note || null,
      feedback.rating,
      JSON.stringify(feedback.tags),
      Date.now() / 1000,
      accountId(env),
      candidateId,
      from,
      revision,
    );
  const [recorded, transitioned] = await env.DB.batch([feedbackStatement, transitionStatement]);
  await assertReviewBatchCommitted(env, candidateId, recorded, transitioned);
}

async function assertReviewBatchCommitted(env, candidateId, recorded, transitioned) {
  if (recorded.meta.changes === 1 && transitioned.meta.changes === 1) return;
  const existing = await findCandidate(env, candidateId);
  if (!existing) throw new WorkspaceHttpError(404, "후보를 찾을 수 없습니다.");
  throw new WorkspaceHttpError(409, "후보가 다른 요청에서 먼저 변경되었습니다.");
}

export function normalizeReviewFeedback(input, accepted) {
  const tags = stringList(input?.tags ?? [], REVIEW_TAGS.length, 40);
  if (new Set(tags).size !== tags.length || tags.some((tag) => !REVIEW_TAGS.includes(tag))) {
    throw new WorkspaceHttpError(400, "지원하지 않는 반려 태그가 포함되어 있습니다.");
  }
  const fallbackRating = accepted ? 5 : 2;
  const rating = Number(input?.rating ?? fallbackRating);
  if (!Number.isInteger(rating) || rating < 1 || rating > 5) {
    throw new WorkspaceHttpError(400, "평점은 1~5의 정수여야 합니다.");
  }
  if (accepted && rating < 4) {
    throw new WorkspaceHttpError(400, "승인 평점은 4점 또는 5점이어야 합니다.");
  }
  if (!accepted && rating > 3) {
    throw new WorkspaceHttpError(400, "반려 평점은 1~3점이어야 합니다.");
  }
  const note = optionalString(input?.note, 2000);
  if (!accepted && tags.length === 0) {
    throw new WorkspaceHttpError(400, "반려할 때는 이유 태그를 하나 이상 선택해 주세요.");
  }
  if (tags.includes("기타") && !note) {
    throw new WorkspaceHttpError(400, "기타를 선택하면 상세 이유를 입력해야 합니다.");
  }
  return { rating, tags: accepted ? [] : tags, note };
}

function assertReviewTagsForStage(stage, feedback) {
  if (feedback.tags.some((tag) => tag !== "기타" && !FEEDBACK_RULE_DEFINITIONS[`${stage}:${tag}`])) {
    throw new WorkspaceHttpError(400, `${stage} 검수 단계에서 사용할 수 없는 반려 태그입니다.`);
  }
}

async function feedbackEventStatement(
  env,
  candidate,
  stage,
  accepted,
  feedback,
  expectedStatus,
  expectedRevision,
) {
  const candidateSnapshot = JSON.stringify({
    schema_version: "trace.workspace-feedback-candidate.v1",
    candidate_id: candidate.candidate_id,
    candidate_revision: candidate.revision,
    country: candidate.country,
    topic: candidate.topic,
    caption: candidate.caption,
    hypothesis: candidate.hypothesis,
    refs_used: candidate.refs_used,
    principles_applied: candidate.principles_applied,
    shooting_order: candidate.shooting_order,
    image_inputs: candidate.image_inputs,
    image_sha256: candidate.image_sha256,
    context_profile: candidate.context_profile,
    posting_slot: candidate.posting_slot,
  });
  return env.DB.prepare(
    `INSERT INTO hosted_workspace_feedback_events
      (event_id, account_id, candidate_id, context_profile_id, stage, decision,
       rating, tags_json, note, candidate_revision, candidate_snapshot_json,
       candidate_snapshot_sha256, generation_prompt_version, generation_prompt_sha256,
       generation_model, feedback_rules_json, created_at)
     SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
     WHERE EXISTS (
       SELECT 1 FROM hosted_workspace_candidates
       WHERE account_id = ? AND candidate_id = ? AND status = ? AND revision = ?
     )`,
  )
    .bind(
      crypto.randomUUID(),
      accountId(env),
      candidate.candidate_id,
      candidate.context_profile?.profile_id ?? null,
      stage,
      accepted ? "approved" : "rejected",
      feedback.rating,
      JSON.stringify(feedback.tags),
      feedback.note || null,
      candidate.revision,
      candidateSnapshot,
      await sha256(candidateSnapshot),
      candidate.generation_provenance?.prompt_version ?? null,
      candidate.generation_provenance?.prompt_sha256 ?? null,
      candidate.generation_provenance?.model ?? null,
      JSON.stringify(candidate.generation_provenance?.feedback_rules ?? []),
      Date.now() / 1000,
      accountId(env),
      candidate.candidate_id,
      expectedStatus,
      expectedRevision,
    );
}

export async function feedbackSummary(env, requestedProfileId) {
  const profileId = optionalString(requestedProfileId, 100) || null;
  const query = profileId
    ? `SELECT candidate_id, candidate_revision, tags_json, rating, stage, created_at
       FROM hosted_workspace_feedback_events
       WHERE account_id = ? AND context_profile_id = ? AND decision = 'rejected'
       ORDER BY created_at DESC LIMIT 200`
    : `SELECT candidate_id, candidate_revision, tags_json, rating, stage, created_at
       FROM hosted_workspace_feedback_events
       WHERE account_id = ? AND decision = 'rejected'
       ORDER BY created_at DESC LIMIT 200`;
  const statement = env.DB.prepare(query);
  const result = profileId
    ? await statement.bind(accountId(env), profileId).all()
    : await statement.bind(accountId(env)).all();
  const counts = new Map();
  const strongReviewKeys = new Map();
  for (const row of result.results) {
    const tags = JSON.parse(row.tags_json);
    for (const tag of tags) counts.set(tag, (counts.get(tag) ?? 0) + 1);
    // Pre-0011 rows have no verifiable reviewed revision. Keep them in aggregate
    // tag counts, but never promote them into a generation rule.
    if (row.rating <= 2 && row.candidate_revision !== null) {
      for (const tag of tags) {
        const definitionKey = `${row.stage}:${tag}`;
        if (!FEEDBACK_RULE_DEFINITIONS[definitionKey]) continue;
        const reviewKey = `${row.candidate_id}:${row.candidate_revision}`;
        if (!strongReviewKeys.has(definitionKey)) strongReviewKeys.set(definitionKey, new Set());
        strongReviewKeys.get(definitionKey).add(reviewKey);
      }
    }
  }
  const topTags = [...counts.entries()]
    .map(([tag, count]) => ({ tag, count }))
    .sort((left, right) => right.count - left.count || left.tag.localeCompare(right.tag));
  const activeRules = [...strongReviewKeys.entries()]
    .map(([definitionKey, reviews]) => {
      const definition = FEEDBACK_RULE_DEFINITIONS[definitionKey];
      const [stage, tag] = definitionKey.split(":", 2);
      return {
        ...definition,
        stage,
        tag,
        evidence_count: reviews.size,
      };
    })
    .filter((rule) => rule.evidence_count >= 3)
    .sort((left, right) => left.rule_id.localeCompare(right.rule_id));
  return {
    rejected_reviews: result.results.length,
    top_tags: topTags,
    rule_candidates: activeRules.map(
      (rule) => `${rule.instruction} (${rule.stage} “${rule.tag}” 강한 반려 ${rule.evidence_count}회)`,
    ),
    active_rules: activeRules,
  };
}

async function requireCandidate(env, candidateId) {
  const candidate = await findCandidate(env, candidateId);
  if (!candidate) throw new WorkspaceHttpError(404, "후보를 찾을 수 없습니다.");
  return candidate;
}

async function findCandidate(env, candidateId) {
  const row = await env.DB.prepare(
    "SELECT * FROM hosted_workspace_candidates WHERE account_id = ? AND candidate_id = ?",
  )
    .bind(accountId(env), candidateId)
    .first();
  return row ? candidateFromRow(row) : null;
}

function candidateFromRow(row) {
  return {
    workspace_id: `cloudflare:${row.account_id}`,
    candidate_id: row.candidate_id,
    persona_id: row.persona_id ?? null,
    source: row.source,
    country: row.country,
    topic: row.topic,
    caption: row.caption,
    hypothesis: row.hypothesis,
    refs_used: JSON.parse(row.refs_json),
    principles_applied: JSON.parse(row.principles_json),
    shooting_order: row.appium_prompt,
    image_inputs: JSON.parse(row.image_inputs_json),
    ai_verdict: row.ai_verdict,
    context_profile: row.context_snapshot_json ? JSON.parse(row.context_snapshot_json) : null,
    posting_slot: row.posting_slot ?? "manual",
    generation_batch_id: row.generation_batch_id ?? null,
    generation_provenance: row.generation_prompt_version
      ? {
          prompt_version: row.generation_prompt_version,
          prompt_sha256: row.generation_prompt_sha256,
          model: row.generation_model,
          feedback_rules: JSON.parse(row.feedback_rules_json ?? "[]"),
        }
      : null,
    image_path: row.image_key,
    image_sha256: row.image_sha256,
    capture_state: row.capture_state ?? null,
    capture_task_id: row.capture_task_id ?? null,
    capture_error: row.capture_error ?? null,
    capture_requested_at: row.capture_requested_at ?? null,
    status: row.status,
    review_note: row.review_note,
    review_rating: row.last_review_rating ?? null,
    review_tags: JSON.parse(row.last_review_tags_json ?? "[]"),
    revision: row.revision,
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

function normalizeImageInputs(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new WorkspaceHttpError(400, "image_inputs가 필요합니다.");
  }
  const traceItems = stringList(input.trace_items, 8, 80);
  if (traceItems.length < 1) throw new WorkspaceHttpError(400, "일정은 한 개 이상 필요합니다.");
  const deviceTime = requiredString(input.device_time, "device_time", 5);
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(deviceTime)) {
    throw new WorkspaceHttpError(400, "device_time은 HH:MM 형식이어야 합니다.");
  }
  const backgroundSubject = requiredString(input.background_subject, "background_subject", 40);
  if (!ALLOWED_BACKGROUND_SUBJECTS.has(backgroundSubject)) {
    throw new WorkspaceHttpError(400, "지원하지 않는 background_subject입니다.");
  }
  return {
    trace_items: traceItems,
    device_time: deviceTime,
    background_subject: backgroundSubject,
    background_mood: requiredString(input.background_mood, "background_mood", 40),
    language: requiredString(input.language ?? "ko", "language", 2),
  };
}

function appiumPromptFrom(inputs) {
  return [
    `입력_일정: ${inputs.trace_items.join(" | ")}`,
    `기기_시각: ${inputs.device_time}`,
    `배경화면: ${inputs.background_subject} · ${inputs.background_mood}`,
    `언어: ${inputs.language}`,
    "정지/영상: 정지 이미지",
  ].join("\n");
}

function isCompleteAppiumPrompt(value) {
  return ["입력_일정:", "기기_시각:", "배경화면:", "언어:", "정지/영상:"].every((field) =>
    value.includes(field),
  );
}

function requiredString(value, field, maxLength) {
  const normalized = optionalString(value, maxLength);
  if (!normalized) throw new WorkspaceHttpError(400, `${field} 값이 필요합니다.`);
  return normalized;
}

function optionalString(value, maxLength) {
  if (value === undefined || value === null) return "";
  if (typeof value !== "string") throw new WorkspaceHttpError(400, "문자열 입력이 필요합니다.");
  const normalized = value.trim();
  if (normalized.length > maxLength) {
    throw new WorkspaceHttpError(400, `입력이 ${maxLength}자를 초과했습니다.`);
  }
  return normalized;
}

function stringList(value, maxItems, maxLength) {
  if (!Array.isArray(value) || value.length > maxItems) {
    throw new WorkspaceHttpError(400, `목록은 최대 ${maxItems}개까지 입력할 수 있습니다.`);
  }
  return value.map((item) => requiredString(item, "목록 항목", maxLength));
}

function referenceIdList(value) {
  const identifiers = stringList(value, 16, 80);
  if (identifiers.some((item) => !/[a-zA-Z0-9]/i.test(item[0]) || /[^a-zA-Z0-9._-]/i.test(item))) {
    throw new WorkspaceHttpError(400, "레퍼런스 ID는 영문자·숫자로 시작하고 ., -, _만 사용할 수 있습니다.");
  }
  if (new Set(identifiers).size !== identifiers.length) {
    throw new WorkspaceHttpError(400, "레퍼런스 ID는 중복될 수 없습니다.");
  }
  return identifiers;
}

function principleList(value) {
  if (!Array.isArray(value) || value.length > 32) {
    throw new WorkspaceHttpError(400, "적용 원리는 최대 32개까지 입력할 수 있습니다.");
  }
  const principles = value.map((item) => {
    const number = Number(item);
    if (!Number.isInteger(number) || number < 1) {
      throw new WorkspaceHttpError(400, "적용 원리는 1 이상의 정수여야 합니다.");
    }
    return number;
  });
  if (new Set(principles).size !== principles.length) {
    throw new WorkspaceHttpError(400, "적용 원리는 중복될 수 없습니다.");
  }
  return principles;
}

function booleanField(value, field) {
  if (typeof value !== "boolean") throw new WorkspaceHttpError(400, `${field} 값이 필요합니다.`);
  return value;
}

function positiveInteger(value, fallback) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : fallback;
}

function accountId(env) {
  return safeAccountId(
    env.HOSTED_WORKSPACE_ACCOUNT_ID || env.PUBLIC_WORKSPACE_ACCOUNT_ID || DEFAULT_ACCOUNT_ID,
  );
}

function personaIdFromRequest(request) {
  const url = new URL(request.url);
  const requested = request.headers.get("x-trace-persona-id")
    || url.searchParams.get("persona_id");
  return requested ? requested.trim() || null : null;
}

function accountIdFromRequest(request, env) {
  const url = new URL(request.url);
  const requested = request.headers.get("x-trace-account-id") || url.searchParams.get("account_id");
  return requested ? safeAccountId(requested) : accountId(env);
}

function withHostedAccount(env, value) {
  const scoped = Object.create(env);
  scoped.HOSTED_WORKSPACE_ACCOUNT_ID = safeAccountId(value);
  return scoped;
}

function safeAccountId(value) {
  if (typeof value !== "string" || !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(value)) {
    throw new WorkspaceHttpError(
      400,
      "계정 ID는 영문 소문자·숫자로 시작하고 -, _만 사용할 수 있습니다.",
    );
  }
  return value;
}

function workspaceId(env) {
  return `cloudflare:${accountId(env)}`;
}

async function readJson(request) {
  try {
    return await request.json();
  } catch (error) {
    throw new WorkspaceHttpError(400, "JSON 요청 본문이 올바르지 않습니다.");
  }
}

async function readOptionalJson(request) {
  const text = await request.text();
  if (!text.trim()) return {};
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new WorkspaceHttpError(400, "JSON 요청 본문이 올바르지 않습니다.");
  }
}

function json(payload, status = 200) {
  return Response.json(payload, {
    status,
    headers: { "cache-control": "no-store" },
  });
}

async function sha256(value) {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

class WorkspaceHttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}
