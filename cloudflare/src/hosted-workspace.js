const DEFAULT_ACCOUNT_ID = "trace_demo_kr";
const DEFAULT_MODEL = "@cf/meta/llama-3.1-8b-instruct-fast";
const DEFAULT_AI_MAX_TOKENS = 4096;
const DEFAULT_GENERATION_COOLDOWN_SECONDS = 60;
const MAX_CANDIDATES = 200;
const MAX_CONTEXT_PROFILES = 100;
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
    if (request.method === "GET" && url.pathname === "/api/auth/session") {
      return json({
        workspace_id: workspaceId(env),
        account_id: accountId(env),
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
    if (request.method === "GET" && url.pathname === "/api/context-profiles") {
      await ensureStarterProfiles(env, starterProfiles);
      return json(await listContextProfiles(env));
    }
    if (request.method === "POST" && url.pathname === "/api/context-profiles") {
      const profile = normalizeContextProfile(await readJson(request));
      assertConfiguredContextCountry(contextRegistry, profile.country);
      return json(await createContextProfile(env, profile), 201);
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
        return json(
          await updateContextProfile(env, profileId, revision, profile),
        );
      }
      if (request.method === "DELETE") {
        await disableContextProfile(env, profileId, revision);
        return new Response(null, { status: 204 });
      }
    }
    if (request.method === "GET" && url.pathname === "/api/candidates") {
      return json(await listCandidates(env));
    }
    if (request.method === "POST" && url.pathname === "/api/candidates") {
      await ensureStarterProfiles(env, starterProfiles);
      const body = await readJson(request);
      const profile = Object.prototype.hasOwnProperty.call(body, "context_profile_id")
        ? await optionalContextProfile(env, body.context_profile_id)
        : undefined;
      return json(await insertCandidate(env, normalizeCandidateDraft(body), "manual", profile), 201);
    }
    if (request.method === "POST" && url.pathname === "/api/candidates/generate") {
      await ensureStarterProfiles(env, starterProfiles);
      const body = await readOptionalJson(request);
      const profile = await resolveContextProfile(env, body?.context_profile_id);
      return json(await generateCandidates(env, contextRegistry, profile), 201);
    }

    const route = url.pathname.match(
      /^\/api\/candidates\/([^/]+)(?:\/(review|generate-image|review-image|image))?$/,
    );
    if (!route) return null;
    const candidateId = decodeURIComponent(route[1]);
    const action = route[2];
    if (request.method === "POST" && action === "review") {
      return json(await reviewCandidate(env, candidateId, await readJson(request)));
    }
    if (request.method === "POST" && action === "generate-image") {
      return json(await generateCandidateImage(env, candidateId), 201);
    }
    if (request.method === "POST" && action === "review-image") {
      return json(await reviewCandidateImage(env, candidateId, await readJson(request)));
    }
    if (request.method === "GET" && action === "image") {
      return readCandidateImage(env, candidateId);
    }
    if (request.method === "PATCH" && !action) {
      await ensureStarterProfiles(env, starterProfiles);
      const body = await readJson(request);
      const revision = positiveInteger(body?.expected_revision, null);
      if (revision === null) throw new WorkspaceHttpError(400, "expected_revision이 필요합니다.");
      const profile = Object.prototype.hasOwnProperty.call(body, "context_profile_id")
        ? await optionalContextProfile(env, body.context_profile_id)
        : undefined;
      return json(
        await updateCandidate(env, candidateId, revision, normalizeCandidateDraft(body), profile),
      );
    }
    if (request.method === "DELETE" && !action) {
      const body = await readJson(request);
      const revision = positiveInteger(body?.expected_revision, null);
      if (revision === null) throw new WorkspaceHttpError(400, "expected_revision이 필요합니다.");
      await deleteCandidate(env, candidateId, revision);
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
  return {
    topic,
    country,
    caption,
    hypothesis,
    refs_used: refsUsed,
    principles_applied: principlesApplied,
    appium_prompt: appiumPrompt,
    image_inputs: imageInputs,
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
        minItems: 3,
        maxItems: 3,
        items: {
          type: "object",
          additionalProperties: false,
          properties: {
            topic: { type: "string" },
            country: { type: "string", enum: [country] },
            caption: { type: "string" },
            hypothesis: { type: "string" },
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
          required: ["topic", "country", "caption", "hypothesis", "refs_used", "principles_applied", "appium_prompt", "image_inputs"],
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

async function ensureStarterProfiles(env, profiles) {
  if (!Array.isArray(profiles) || profiles.length === 0) return;
  const now = Date.now() / 1000;
  const statements = profiles.map((input) => {
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
  await env.DB.batch(statements);
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
  const sharedInstruction = await loadSharedInstruction(env);
  const prompt = generationPrompt(contextDocuments, sharedInstruction, profile);
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
      if (drafts.length !== 3) throw new Error("후보는 정확히 3개여야 합니다.");
      if (new Set(drafts.map((draft) => draft.topic)).size !== drafts.length) {
        throw new Error("후보 주제가 서로 달라야 합니다.");
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

export function generationPrompt(contextDocuments, sharedInstruction, profile) {
  return `아래 Trace context, 계정 지침, 선택한 국가·페르소나 컨텍스트만 근거로 서로 다른 게시물 후보 3개를 만드세요.
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
레퍼런스 ID: ${profile.reference_ids.join(", ") || "없음"}`;
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
  return { KR: "ko", JP: "ja", TW: "zh", US: "en" }[country] ?? "en";
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
  const inserts = drafts.map((draft, index) => {
    assertProfileCountry(profile, draft.country);
    const candidateId = crypto.randomUUID();
    return {
      candidateId,
      statement: env.DB.prepare(
        `INSERT INTO hosted_workspace_candidates
        (candidate_id, account_id, source, country, topic, caption, hypothesis,
         refs_json, principles_json, appium_prompt, image_inputs_json, ai_verdict,
         context_profile_id, context_snapshot_json, status, revision, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'awaiting_review', 1, ?, ?)`,
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
        source === "auto" ? "Cloudflare Workers AI · context 검증" : null,
        profile?.profile_id ?? null,
        contextSnapshot,
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
         context_profile_id = ?, context_snapshot_json = ?,
         ai_verdict = NULL, status = 'awaiting_review', review_note = NULL, image_key = NULL,
         image_sha256 = NULL, revision = revision + 1, updated_at = ?
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
  const status = accepted ? "caption_approved" : "rejected";
  await transitionCandidate(env, candidateId, revision, "awaiting_review", status, body?.note);
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
  const current = await requireCandidate(env, candidateId);
  if (current.status !== "image_awaiting_review") {
    throw new WorkspaceHttpError(409, "검수 대기 중인 이미지를 찾을 수 없습니다.");
  }
  const status = accepted ? "submitted" : "caption_approved";
  const result = await env.DB.prepare(
    `UPDATE hosted_workspace_candidates
     SET status = ?, review_note = ?, image_key = ?, image_sha256 = ?,
         revision = revision + 1, updated_at = ?
     WHERE account_id = ? AND candidate_id = ? AND status = 'image_awaiting_review' AND revision = ?`,
  )
    .bind(
      status,
      optionalString(body?.note, 2000) || null,
      accepted ? current.image_path : null,
      accepted ? current.image_sha256 : null,
      Date.now() / 1000,
      accountId(env),
      candidateId,
      revision,
    )
    .run();
  if (result.meta.changes !== 1) {
    throw new WorkspaceHttpError(409, "후보가 다른 요청에서 먼저 변경되었습니다.");
  }
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

async function transitionCandidate(env, candidateId, revision, from, to, note) {
  const result = await env.DB.prepare(
    `UPDATE hosted_workspace_candidates
     SET status = ?, review_note = ?, revision = revision + 1, updated_at = ?
     WHERE account_id = ? AND candidate_id = ? AND status = ? AND revision = ?`,
  )
    .bind(
      to,
      optionalString(note, 2000) || null,
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
    image_path: row.image_key,
    image_sha256: row.image_sha256,
    status: row.status,
    review_note: row.review_note,
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
  const value = env.PUBLIC_WORKSPACE_ACCOUNT_ID || DEFAULT_ACCOUNT_ID;
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(value)) throw new Error("invalid public workspace account ID");
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
