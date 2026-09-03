// The catalog is the source of truth for which adapters may be used.  A binding
// freezes the exact descriptor that planning saw; a later action additionally
// proves that the same descriptor is still active.  Capability names alone are
// intentionally never sufficient authority.

export const CREATIVE_FORMAT_REQUIREMENTS = Object.freeze([
  ["native_sequence", Object.freeze(["capture.native_png"])],
  ["screen_recording", Object.freeze(["capture.screen_recording"])],
  ["explanatory_carousel", Object.freeze(["compose.carousel"])],
  ["designed_static", Object.freeze(["design.static_image"])],
  ["text_only", Object.freeze(["copy.text", "materialize.text_candidate"])],
]);

// This is the set the creative planner knows how to configure, not a list of tools every account
// must install. Runtime availability is read from the account catalog below.
export const CREATIVE_CAPABILITY_IDS = Object.freeze([
  ...new Set(CREATIVE_FORMAT_REQUIREMENTS.flatMap(([, required]) => required)),
].sort());

const DESCRIPTOR_KEYS = Object.freeze([
  "activation_state",
  "capability_id",
  "effect_class",
  "owner_id",
  "receipt_schema_sha256",
  "request_schema_sha256",
  "schema_version",
]);
const BINDING_KEYS = Object.freeze([
  "binding_sha256",
  "capability_id",
  "descriptor_sha256",
  "effect_class",
  "owner_id",
  "receipt_schema_sha256",
  "request_schema_sha256",
]);
const SHA256 = /^[a-f0-9]{64}$/;
const GENERIC_REQUEST_SCHEMA_SHA256 = "fa609647bf7cfc267927f5e42c63ed3ae42d60a1f14962b80a0664107e8f2a80";
const GENERIC_RECEIPT_SCHEMA_SHA256 = "368888b194fc57a818cf666788ff2e8fe79dab96c17a4c6b79f908e92a9dd91c";

export const MARKETING_TOOL_CATALOG = Object.freeze({
  "publish.threads": Object.freeze({
    effect_class: "external", owner_id: "threads.publisher", setup_path: "/api/threads/oauth/start",
  }),
  "deliver.slack": Object.freeze({
    effect_class: "external", owner_id: "slack.delivery", setup_path: null,
  }),
});

export class MarketingCapabilityError extends Error {}

export async function listMarketingToolInstallations(db, accountId) {
  const rows = await db.prepare(
    `SELECT capability_id, enabled, activation_state, updated_at
     FROM hosted_marketing_adapter_capabilities WHERE account_id = ? ORDER BY capability_id`,
  ).bind(accountId).all();
  const installed = new Map((rows.results ?? []).map((row) => [row.capability_id, row]));
  return Object.entries(MARKETING_TOOL_CATALOG).map(([capabilityId, definition]) => {
    const row = installed.get(capabilityId);
    return {
      capability_id: capabilityId,
      owner_id: definition.owner_id,
      effect_class: definition.effect_class,
      installed: Boolean(row),
      enabled: Number(row?.enabled ?? 0) === 1,
      activation_state: row?.activation_state ?? "not_installed",
      setup_path: definition.setup_path,
      updated_at: row?.updated_at ?? null,
    };
  });
}

function catalogDescriptor(capabilityId, activationState) {
  const definition = MARKETING_TOOL_CATALOG[capabilityId];
  if (!definition) throw new MarketingCapabilityError("marketing tool is not in the supported catalog");
  return {
    activation_state: activationState,
    capability_id: capabilityId,
    effect_class: definition.effect_class,
    owner_id: definition.owner_id,
    receipt_schema_sha256: GENERIC_RECEIPT_SCHEMA_SHA256,
    request_schema_sha256: GENERIC_REQUEST_SCHEMA_SHA256,
    schema_version: "trace.adapter-capability.v1",
  };
}

export async function installMarketingToolReference(db, accountId, capabilityId, now) {
  const descriptor = catalogDescriptor(capabilityId, "registered_reference");
  const descriptorJson = canonicalJson(descriptor);
  await db.prepare(
    `INSERT INTO hosted_marketing_adapter_capabilities
       (account_id, capability_id, descriptor_json, descriptor_sha256, effect_class,
        request_schema_sha256, receipt_schema_sha256, owner_id, enabled, activation_state,
        created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'registered_reference', ?, ?)
     ON CONFLICT(account_id, capability_id) DO UPDATE SET enabled = 1,
       activation_state = 'registered_reference', descriptor_json = excluded.descriptor_json,
       descriptor_sha256 = excluded.descriptor_sha256, updated_at = excluded.updated_at`,
  ).bind(
    accountId, capabilityId, descriptorJson, await canonicalSha256(descriptor),
    descriptor.effect_class, GENERIC_REQUEST_SCHEMA_SHA256, GENERIC_RECEIPT_SCHEMA_SHA256,
    descriptor.owner_id, now, now,
  ).run();
  return {
    capability_id: capabilityId,
    activation_state: "registered_reference",
    setup_path: MARKETING_TOOL_CATALOG[capabilityId].setup_path,
    operator_action_required: capabilityId === "publish.threads" ? "oauth_consent" : "provider_setup",
  };
}

export async function activateMarketingTool(db, accountId, capabilityId, now) {
  const descriptor = catalogDescriptor(capabilityId, "active");
  await db.prepare(
    `UPDATE hosted_marketing_adapter_capabilities SET descriptor_json = ?, descriptor_sha256 = ?,
       activation_state = 'active', enabled = 1, updated_at = ?
     WHERE account_id = ? AND capability_id = ?`,
  ).bind(
    canonicalJson(descriptor), await canonicalSha256(descriptor), now, accountId, capabilityId,
  ).run();
}

export async function resolveCreativeCapabilityBindings(db, accountId) {
  const bindings = await resolveAvailableCapabilityBindings(
    db,
    accountId,
    CREATIVE_CAPABILITY_IDS,
  );
  if (creativeFormatsForCapabilities(bindings.map((item) => item.capability_id)).length === 0) {
    throw new MarketingCapabilityError("no executable creative format is currently available");
  }
  return bindings;
}

export async function resolveActiveCapabilityBindings(db, accountId, capabilityIds) {
  const ids = normalizedCapabilityIds(capabilityIds);
  const placeholders = ids.map(() => "?").join(", ");
  const rows = await db.prepare(
    `SELECT capability_id, descriptor_json, descriptor_sha256, effect_class,
            request_schema_sha256, receipt_schema_sha256, owner_id, enabled, activation_state
     FROM hosted_marketing_adapter_capabilities
     WHERE account_id = ? AND capability_id IN (${placeholders})`,
  ).bind(accountId, ...ids).all();
  const byId = new Map((rows.results ?? []).map((row) => [row.capability_id, row]));
  if (byId.size !== ids.length || ids.some((id) => !byId.has(id))) {
    throw new MarketingCapabilityError("required marketing capability is not registered");
  }
  return Promise.all(ids.map((id) => normalizeActiveCatalogRow(byId.get(id), id)));
}

export async function validateCreativeCapabilitySnapshot(
  payload,
  { allowLegacyCreativeV1 = false } = {},
) {
  if (
    (allowLegacyCreativeV1 && payload?.creative_contract_version != null)
    || (!allowLegacyCreativeV1 && payload?.creative_contract_version !== "v2")
  ) {
    throw new MarketingCapabilityError("creative contract version does not match its task");
  }
  const advertised = normalizedCapabilityIds(payload?.available_capabilities);
  if (
    canonicalJson([...advertised].sort()) !== canonicalJson(advertised)
    || advertised.some((capabilityId) => !CREATIVE_CAPABILITY_IDS.includes(capabilityId))
  ) {
    throw new MarketingCapabilityError("creative capability IDs are not a known sorted subset");
  }
  const frozen = await normalizeCapabilityBindings(payload?.capability_bindings, advertised);
  const availableFormats = creativeFormatsForCapabilities(advertised);
  const legacyFormatProjection = allowLegacyCreativeV1 && payload?.available_formats == null;
  if (
    !legacyFormatProjection
    && canonicalJson(payload?.available_formats) !== canonicalJson(availableFormats)
  ) {
    throw new MarketingCapabilityError("creative formats do not match executable capabilities");
  }
  const snapshot = await canonicalSha256({ capability_bindings: frozen });
  if (payload?.capability_snapshot_sha256 !== snapshot) {
    throw new MarketingCapabilityError("creative capability snapshot digest is invalid");
  }
  return frozen;
}

export function creativeFormatsForCapabilities(capabilityIds) {
  const available = new Set(capabilityIds);
  return CREATIVE_FORMAT_REQUIREMENTS
    .filter(([, required]) => required.every((capabilityId) => available.has(capabilityId)))
    .map(([format]) => format);
}

export function requiredCapabilitiesForCreativeFormat(format) {
  return CREATIVE_FORMAT_REQUIREMENTS.find(([candidate]) => candidate === format)?.[1] ?? null;
}

export async function assertCreativeCapabilitySnapshot(db, accountId, payload, options = {}) {
  const frozen = await validateCreativeCapabilitySnapshot(payload, options);
  const active = await resolveActiveCapabilityBindings(
    db,
    accountId,
    frozen.map((binding) => binding.capability_id),
  );
  if (canonicalJson(frozen) !== canonicalJson(active)) {
    throw new MarketingCapabilityError("marketing capability catalog changed after planning");
  }
  return frozen;
}

async function resolveAvailableCapabilityBindings(db, accountId, capabilityIds) {
  const ids = normalizedCapabilityIds(capabilityIds);
  const placeholders = ids.map(() => "?").join(", ");
  const rows = await db.prepare(
    `SELECT capability_id, descriptor_json, descriptor_sha256, effect_class,
            request_schema_sha256, receipt_schema_sha256, owner_id, enabled, activation_state
     FROM hosted_marketing_adapter_capabilities
     WHERE account_id = ? AND capability_id IN (${placeholders})`,
  ).bind(accountId, ...ids).all();
  const byId = new Map((rows.results ?? []).map((row) => [row.capability_id, row]));
  return Promise.all(ids
    .filter((id) => {
      const row = byId.get(id);
      return row && Number(row.enabled) === 1 && row.activation_state === "active";
    })
    .map((id) => normalizeActiveCatalogRow(byId.get(id), id)));
}

export async function assertCurrentCapabilityBinding(db, accountId, capabilityId, bindingSha256) {
  const [binding] = await resolveActiveCapabilityBindings(db, accountId, [capabilityId]);
  if (binding.binding_sha256 !== bindingSha256) {
    throw new MarketingCapabilityError("marketing capability binding is no longer active");
  }
  return binding;
}

export function capabilityBindingStatements(db, contextReceiptId, bindings, createdAt) {
  return bindings.map((binding) => db.prepare(
    `INSERT INTO hosted_marketing_capability_bindings
      (context_receipt_id, capability_id, binding_sha256, descriptor_sha256, effect_class,
       request_schema_sha256, receipt_schema_sha256, owner_id, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  ).bind(
    contextReceiptId,
    binding.capability_id,
    binding.binding_sha256,
    binding.descriptor_sha256,
    binding.effect_class,
    binding.request_schema_sha256,
    binding.receipt_schema_sha256,
    binding.owner_id,
    createdAt,
  ));
}

export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) =>
      `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export async function canonicalSha256(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonicalJson(value)));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function normalizeActiveCatalogRow(row, expectedId) {
  if (!row || row.capability_id !== expectedId || Number(row.enabled) !== 1 || row.activation_state !== "active") {
    throw new MarketingCapabilityError("marketing capability is not active");
  }
  const descriptor = parseDescriptor(row.descriptor_json);
  if (
    descriptor.capability_id !== row.capability_id
    || descriptor.effect_class !== row.effect_class
    || descriptor.owner_id !== row.owner_id
    || descriptor.request_schema_sha256 !== row.request_schema_sha256
    || descriptor.receipt_schema_sha256 !== row.receipt_schema_sha256
    || descriptor.activation_state !== row.activation_state
  ) {
    throw new MarketingCapabilityError("marketing capability descriptor does not match catalog fields");
  }
  const descriptorSha256 = await canonicalSha256(descriptor);
  if (descriptorSha256 !== row.descriptor_sha256) {
    throw new MarketingCapabilityError("marketing capability descriptor digest is invalid");
  }
  const binding = {
    capability_id: descriptor.capability_id,
    descriptor_sha256: descriptorSha256,
    effect_class: descriptor.effect_class,
    request_schema_sha256: descriptor.request_schema_sha256,
    receipt_schema_sha256: descriptor.receipt_schema_sha256,
    owner_id: descriptor.owner_id,
  };
  return {
    ...binding,
    binding_sha256: await canonicalSha256(binding),
  };
}

function parseDescriptor(raw) {
  let descriptor;
  try {
    descriptor = JSON.parse(raw);
  } catch {
    throw new MarketingCapabilityError("marketing capability descriptor is invalid JSON");
  }
  if (!descriptor || typeof descriptor !== "object" || Array.isArray(descriptor)) {
    throw new MarketingCapabilityError("marketing capability descriptor is invalid");
  }
  const keys = Object.keys(descriptor).sort();
  if (canonicalJson(keys) !== canonicalJson(DESCRIPTOR_KEYS)) {
    throw new MarketingCapabilityError("marketing capability descriptor fields are invalid");
  }
  if (
    descriptor.schema_version !== "trace.adapter-capability.v1"
    || !validIdentifier(descriptor.capability_id)
    || !["none", "local_artifact", "external"].includes(descriptor.effect_class)
    || !validIdentifier(descriptor.owner_id)
    || !SHA256.test(descriptor.request_schema_sha256)
    || !SHA256.test(descriptor.receipt_schema_sha256)
    || !["active", "registered_reference"].includes(descriptor.activation_state)
  ) {
    throw new MarketingCapabilityError("marketing capability descriptor values are invalid");
  }
  return descriptor;
}

async function normalizeCapabilityBindings(value, expectedIds) {
  if (!Array.isArray(value) || value.length !== expectedIds.length) {
    throw new MarketingCapabilityError("marketing capability bindings are missing");
  }
  return Promise.all(value.map(async (binding, index) => {
    if (!binding || typeof binding !== "object" || Array.isArray(binding)) {
      throw new MarketingCapabilityError("marketing capability binding is invalid");
    }
    const keys = Object.keys(binding).sort();
    if (canonicalJson(keys) !== canonicalJson(BINDING_KEYS)) {
      throw new MarketingCapabilityError("marketing capability binding fields are invalid");
    }
    const normalized = {
      capability_id: binding.capability_id,
      descriptor_sha256: binding.descriptor_sha256,
      effect_class: binding.effect_class,
      request_schema_sha256: binding.request_schema_sha256,
      receipt_schema_sha256: binding.receipt_schema_sha256,
      owner_id: binding.owner_id,
      binding_sha256: binding.binding_sha256,
    };
    if (
      normalized.capability_id !== expectedIds[index]
      || !validIdentifier(normalized.capability_id)
      || !validIdentifier(normalized.owner_id)
      || !["none", "local_artifact", "external"].includes(normalized.effect_class)
      || !SHA256.test(normalized.descriptor_sha256)
      || !SHA256.test(normalized.request_schema_sha256)
      || !SHA256.test(normalized.receipt_schema_sha256)
      || !SHA256.test(normalized.binding_sha256)
    ) {
      throw new MarketingCapabilityError("marketing capability binding values are invalid");
    }
    const boundValue = {
      capability_id: normalized.capability_id,
      descriptor_sha256: normalized.descriptor_sha256,
      effect_class: normalized.effect_class,
      request_schema_sha256: normalized.request_schema_sha256,
      receipt_schema_sha256: normalized.receipt_schema_sha256,
      owner_id: normalized.owner_id,
    };
    if (normalized.binding_sha256 !== await canonicalSha256(boundValue)) {
      throw new MarketingCapabilityError("marketing capability binding digest is invalid");
    }
    return normalized;
  }));
}

function normalizedCapabilityIds(value) {
  if (!Array.isArray(value) || !value.length || value.length > 32) {
    throw new MarketingCapabilityError("marketing capability IDs are invalid");
  }
  const ids = value.map((value) => {
    if (!validIdentifier(value)) {
      throw new MarketingCapabilityError("marketing capability ID is invalid");
    }
    return value;
  });
  if (new Set(ids).size !== ids.length) {
    throw new MarketingCapabilityError("marketing capability IDs must be unique");
  }
  return ids;
}

function validIdentifier(value) {
  return typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value);
}
