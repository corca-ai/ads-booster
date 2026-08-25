const DEFAULT_ACCOUNT_ID = "trace_demo_kr";
const DEFAULT_MODEL = "@cf/meta/llama-3.1-8b-instruct-fast";
const DEFAULT_AI_MAX_TOKENS = 4096;
const DEFAULT_GENERATION_COOLDOWN_SECONDS = 60;
const MAX_CANDIDATES = 200;
const MAX_CONTEXT_PROFILES = 100;
const MAX_HOSTED_ACCOUNTS = 100;
const POSTING_SLOTS = new Set(["morning", "evening", "manual"]);
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
      return json(await listCandidates(scopedEnv));
    }
    if (request.method === "POST" && url.pathname === "/api/candidates") {
      await ensureStarterProfiles(scopedEnv, starterProfiles);
      const body = await readJson(request);
      const profile = Object.prototype.hasOwnProperty.call(body, "context_profile_id")
        ? await optionalContextProfile(scopedEnv, body.context_profile_id)
        : undefined;
      const draft = normalizeCandidateDraft(body);
      await assertAccountCountry(scopedEnv, draft.country);
      return json(await insertCandidate(scopedEnv, draft, "manual", profile), 201);
    }
    if (request.method === "POST" && url.pathname === "/api/candidates/generate") {
      await ensureStarterProfiles(scopedEnv, starterProfiles);
      const body = await readOptionalJson(request);
      const profile = await resolveContextProfile(scopedEnv, body?.context_profile_id);
      return json(await generateCandidates(scopedEnv, contextRegistry, profile), 201);
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
  const refsUsed = stringList(input.refs_used, 16, 120);
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

export function renderCandidatePreview(candidate) {
  const inputs = candidate.image_inputs;
  const items = inputs.trace_items
    .map(
      (item, index) =>
        `<text x="92" y="${680 + index * 122}" class="item">${escapeXml(item)}</text>`,
    )
    .join("");
  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1179" height="2556" viewBox="0 0 1179 2556">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#1d2939"/><stop offset="0.55" stop-color="#344054"/><stop offset="1" stop-color="#101828"/>
    </linearGradient>
    <filter id="shadow"><feDropShadow dx="0" dy="18" stdDeviation="24" flood-opacity="0.28"/></filter>
  </defs>
  <style>
    .time{font:700 176px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;fill:#fff;letter-spacing:-8px}
    .date{font:500 34px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;fill:#e4e7ec}
    .title{font:700 40px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;fill:#101828}
    .item{font:500 34px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;fill:#344054}
    .meta{font:500 27px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;fill:#d0d5dd}
  </style>
  <rect width="1179" height="2556" fill="url(#bg)"/>
  <circle cx="950" cy="330" r="330" fill="#7f56d9" opacity="0.28"/>
  <circle cx="210" cy="2050" r="420" fill="#2e90fa" opacity="0.18"/>
  <text x="589.5" y="300" text-anchor="middle" class="date">오늘의 Trace</text>
  <text x="589.5" y="500" text-anchor="middle" class="time">${escapeXml(inputs.device_time)}</text>
  <rect x="48" y="570" width="1083" height="${Math.max(510, 210 + inputs.trace_items.length * 122)}" rx="54" fill="#fff" fill-opacity="0.94" filter="url(#shadow)"/>
  <text x="92" y="632" class="title">${escapeXml(candidate.topic)}</text>
  ${items}
  <text x="72" y="2440" class="meta">${escapeXml(inputs.background_mood)}</text>
  <text x="72" y="2488" class="meta">Cloudflare hosted preview · native Appium capture 아님</text>
</svg>`;
}

export function candidateResponseSchema(country = "KR") {
  const language = languageForCountry(country);
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
            topic: { type: "string" },
            country: { type: "string", enum: [country] },
            caption: { type: "string" },
            hypothesis: { type: "string" },
            posting_slot: { type: "string", enum: ["morning", "evening"] },
            refs_used: { type: "array", items: { type: "string" } },
            principles_applied: { type: "array", items: { type: "integer" } },
            appium_prompt: { type: "string" },
            image_inputs: {
              type: "object",
              additionalProperties: false,
              properties: {
                trace_items: { type: "array", minItems: 1, maxItems: 8, items: { type: "string" } },
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
  return {
    country,
    name: requiredString(input.name, "name", 80),
    persona_id: personaId,
    audience: requiredString(input.audience, "audience", 500),
    situation: requiredString(input.situation, "situation", 500),
    tone: requiredString(input.tone, "tone", 300),
    guidance: requiredString(input.guidance, "guidance", 2000),
    reference_ids: stringList(input.reference_ids ?? [], 16, 120),
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

async function listCandidates(env) {
  const result = await env.DB.prepare(
    `SELECT * FROM hosted_workspace_candidates
     WHERE account_id = ? ORDER BY created_at DESC LIMIT ?`,
  )
    .bind(accountId(env), MAX_CANDIDATES)
    .all();
  return result.results.map(candidateFromRow);
}

async function generateCandidates(env, contextRegistry, profile) {
  if (!env.AI || typeof env.AI.run !== "function") {
    throw new WorkspaceHttpError(503, "Cloudflare Workers AI 연결이 준비되지 않았습니다.");
  }
  const contextDocuments = contextForCountry(contextRegistry, profile.country);
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
  let detail = "AI 응답 형식이 올바르지 않습니다.";

  for (let attempt = 0; attempt < 2; attempt += 1) {
    let result;
    try {
      result = await env.AI.run(env.WORKSPACE_AI_MODEL || DEFAULT_MODEL, {
        messages: [
          {
            role: "system",
            content: `당신은 Trace 마케팅 후보 생성기입니다. 제공된 문서만 근거로 ${profile.country} / ${languageForCountry(profile.country)} 후보를 만드세요.`,
          },
          {
            role: "user",
            content: attempt === 0 ? prompt : `${prompt}\n\n직전 응답 검증 오류: ${detail}\n형식을 정확히 지켜 다시 만드세요.`,
          },
        ],
        max_tokens: positiveInteger(env.WORKSPACE_AI_MAX_TOKENS, DEFAULT_AI_MAX_TOKENS),
        temperature: 0.4,
        response_format: { type: "json_schema", json_schema: candidateResponseSchema(profile.country) },
      });
    } catch (error) {
      throw new WorkspaceHttpError(502, "Cloudflare AI 후보 생성에 실패했습니다.");
    }
    try {
      const raw = aiCandidates(result);
      const drafts = raw.map(normalizeCandidateDraft);
      if (drafts.length !== 4) throw new Error("후보는 정확히 4개여야 합니다.");
      if (new Set(drafts.map((draft) => draft.topic)).size !== drafts.length) {
        throw new Error("후보 주제가 서로 달라야 합니다.");
      }
      const slots = drafts.reduce((counts, draft) => {
        counts[draft.posting_slot] = (counts[draft.posting_slot] ?? 0) + 1;
        return counts;
      }, {});
      if (slots.morning !== 2 || slots.evening !== 2) {
        throw new Error("오전 후보 2개와 저녁 후보 2개가 필요합니다.");
      }
      return insertCandidates(env, drafts, "auto", profile);
    } catch (error) {
      detail = error instanceof Error ? error.message : detail;
    }
  }
  throw new WorkspaceHttpError(502, `AI 후보 형식 검증에 실패했습니다: ${detail}`);
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
  const learnedRules = learnedFeedback?.rule_candidates?.length
    ? learnedFeedback.rule_candidates.map((rule) => `- ${rule}`).join("\n")
    : "- 아직 3회 이상 반복된 반려 규칙 없음";
  return `아래 Trace context, 계정 지침, 선택한 국가·페르소나 컨텍스트만 근거로 서로 다른 게시물 후보 4개를 만드세요.
posting_slot=morning 후보 2개, posting_slot=evening 후보 2개를 정확히 만드세요.
오전 슬롯 기준 시각은 ${morningTime}, 저녁 슬롯 기준 시각은 ${eveningTime}${account?.timezone ? ` (${account.timezone})` : ""}입니다.
사실 문서 밖의 수치나 기능은 주장하지 마세요. appium_prompt와 image_inputs를 비우지 마세요.
trace_items는 실제 하루처럼 읽히는 일정 5~7개를 권장합니다.

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

function contextForCountry(registry, country) {
  if (typeof registry === "string") return registry;
  const globalContext = typeof registry?.global === "string" ? registry.global : "";
  const countryContext = registry?.countries?.[country];
  if (typeof countryContext !== "string" || !countryContext.trim()) {
    throw new WorkspaceHttpError(
      409,
      `${country} 국가 context 문서가 아직 등록되지 않았습니다. context manifest를 확장해 주세요.`,
    );
  }
  return `${globalContext}\n\n${countryContext}`.trim();
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

function aiCandidates(result) {
  let response = result?.response ?? result;
  if (typeof response === "string") response = JSON.parse(response);
  if (!response || typeof response !== "object" || !Array.isArray(response.candidates)) {
    throw new Error("candidates 배열이 없습니다.");
  }
  return response.candidates;
}

async function insertCandidates(env, drafts, source, profile = null) {
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
         status, revision, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'awaiting_review', 1, ?, ?)`,
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
        now + index / 1000,
        now + index / 1000,
      ),
    };
  });
  await env.DB.batch(inserts.map(({ statement }) => statement));
  return Promise.all(inserts.map(({ candidateId }) => requireCandidate(env, candidateId)));
}

async function insertCandidate(env, draft, source, profile = null) {
  return (await insertCandidates(env, [draft], source, profile))[0];
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
         image_sha256 = NULL, last_review_rating = NULL, last_review_tags_json = '[]',
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
  const current = await requireCandidate(env, candidateId);
  if (current.status !== "awaiting_review") {
    throw new WorkspaceHttpError(409, "검수 대기 중인 캡션·주제를 찾을 수 없습니다.");
  }
  const status = accepted ? "caption_approved" : "rejected";
  await transitionCandidate(
    env,
    candidateId,
    revision,
    "awaiting_review",
    status,
    feedback,
  );
  await recordFeedbackEvent(env, current, "caption", accepted, feedback);
  return requireCandidate(env, candidateId);
}

async function generateCandidateImage(env, candidateId) {
  const candidate = await requireCandidate(env, candidateId);
  if (candidate.status !== "caption_approved") {
    throw new WorkspaceHttpError(409, "캡션·주제가 승인된 후보만 이미지를 만들 수 있습니다.");
  }
  const svg = renderCandidatePreview(candidate);
  const digest = await sha256(svg);
  const key = `workspace/${accountId(env)}/candidates/${candidateId}.svg`;
  await env.ARTIFACTS.put(key, svg, {
    httpMetadata: { contentType: "image/svg+xml; charset=utf-8" },
    customMetadata: { sha256: digest, account_id: accountId(env), candidate_id: candidateId },
  });
  const result = await env.DB.prepare(
    `UPDATE hosted_workspace_candidates
     SET status = 'image_awaiting_review', image_key = ?, image_sha256 = ?,
         revision = revision + 1, updated_at = ?
     WHERE account_id = ? AND candidate_id = ? AND status = 'caption_approved' AND revision = ?`,
  )
    .bind(key, digest, Date.now() / 1000, accountId(env), candidateId, candidate.revision)
    .run();
  if (result.meta.changes !== 1) {
    throw new WorkspaceHttpError(409, "후보가 다른 요청에서 먼저 변경되었습니다.");
  }
  return requireCandidate(env, candidateId);
}

async function reviewCandidateImage(env, candidateId, body) {
  const accepted = booleanField(body?.accepted, "accepted");
  const revision = positiveInteger(body?.expected_revision, null);
  if (revision === null) throw new WorkspaceHttpError(400, "expected_revision이 필요합니다.");
  const feedback = normalizeReviewFeedback(body, accepted);
  const current = await requireCandidate(env, candidateId);
  if (current.status !== "image_awaiting_review") {
    throw new WorkspaceHttpError(409, "검수 대기 중인 이미지를 찾을 수 없습니다.");
  }
  const status = accepted ? "submitted" : "caption_approved";
  const result = await env.DB.prepare(
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
    )
    .run();
  if (result.meta.changes !== 1) {
    throw new WorkspaceHttpError(409, "후보가 다른 요청에서 먼저 변경되었습니다.");
  }
  await recordFeedbackEvent(env, current, "image", accepted, feedback);
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

async function transitionCandidate(env, candidateId, revision, from, to, feedback) {
  const result = await env.DB.prepare(
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
    )
    .run();
  if (result.meta.changes !== 1) {
    const existing = await findCandidate(env, candidateId);
    if (!existing) throw new WorkspaceHttpError(404, "후보를 찾을 수 없습니다.");
    throw new WorkspaceHttpError(409, "후보가 다른 요청에서 먼저 변경되었습니다.");
  }
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

async function recordFeedbackEvent(env, candidate, stage, accepted, feedback) {
  await env.DB.prepare(
    `INSERT INTO hosted_workspace_feedback_events
      (event_id, account_id, candidate_id, context_profile_id, stage, decision,
       rating, tags_json, note, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
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
      Date.now() / 1000,
    )
    .run();
}

async function feedbackSummary(env, requestedProfileId) {
  const profileId = optionalString(requestedProfileId, 100) || null;
  const query = profileId
    ? `SELECT tags_json, note, rating, stage, created_at
       FROM hosted_workspace_feedback_events
       WHERE account_id = ? AND context_profile_id = ? AND decision = 'rejected'
       ORDER BY created_at DESC LIMIT 200`
    : `SELECT tags_json, note, rating, stage, created_at
       FROM hosted_workspace_feedback_events
       WHERE account_id = ? AND decision = 'rejected'
       ORDER BY created_at DESC LIMIT 200`;
  const statement = env.DB.prepare(query);
  const result = profileId
    ? await statement.bind(accountId(env), profileId).all()
    : await statement.bind(accountId(env)).all();
  const counts = new Map();
  const recentNotes = [];
  for (const row of result.results) {
    const tags = JSON.parse(row.tags_json);
    for (const tag of tags) counts.set(tag, (counts.get(tag) ?? 0) + 1);
    if (row.note && recentNotes.length < 5) recentNotes.push(row.note);
  }
  const topTags = [...counts.entries()]
    .map(([tag, count]) => ({ tag, count }))
    .sort((left, right) => right.count - left.count || left.tag.localeCompare(right.tag));
  return {
    rejected_reviews: result.results.length,
    top_tags: topTags,
    rule_candidates: topTags
      .filter(({ count }) => count >= 3)
      .map(({ tag, count }) => `“${tag}” 반려가 ${count}회 누적됨 — 같은 패턴을 피할 것`),
    recent_notes: recentNotes,
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
    image_path: row.image_key,
    image_sha256: row.image_sha256,
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

function principleList(value) {
  if (!Array.isArray(value) || value.length > 32) {
    throw new WorkspaceHttpError(400, "적용 원리는 최대 32개까지 입력할 수 있습니다.");
  }
  return value.map((item) => {
    const number = Number(item);
    if (!Number.isInteger(number) || number < 1) {
      throw new WorkspaceHttpError(400, "적용 원리는 1 이상의 정수여야 합니다.");
    }
    return number;
  });
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

function escapeXml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
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
