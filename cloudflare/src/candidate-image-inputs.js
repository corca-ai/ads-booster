export const MAX_TRACE_ITEMS = 24;
export const MAX_TRACE_TODOS = 20;
const WEEK_DAYS = 7;

export const ALLOWED_BACKGROUND_SUBJECTS = new Set([
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
export const ALLOWED_EVENT_COLORS = new Set([
  "6E86F7", "3D73DD", "8A2BE2", "9B5DE5", "F9C74F",
  "F26419", "D62246", "DA4C93", "B598F9", "00B4D8",
  "5FBDB0", "2D936C", "FF9E00", "FF6B6B", "AF3B6E",
]);

export class CandidateImageInputsError extends Error {}

export function normalizeCandidateImageInputs(input, options = {}) {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new CandidateImageInputsError("image_inputs가 필요합니다.");
  }
  const minimumTraceItems = options.minimumTraceItems ?? 1;
  const maximumTraceItems = options.maximumTraceItems ?? MAX_TRACE_ITEMS;
  const minimumTraceTodos = options.minimumTraceTodos ?? 0;
  const maximumTraceTodos = options.maximumTraceTodos ?? MAX_TRACE_TODOS;
  const traceItems = scheduleList(input.trace_items, {
    minimum: minimumTraceItems,
    maximum: maximumTraceItems,
    acceptLegacy: options.acceptLegacy !== false,
    includeStructuredMarker: options.includeStructuredMarker !== false,
  });
  const traceTodos = stringList(
    input.trace_todos ?? [],
    minimumTraceTodos,
    maximumTraceTodos,
    60,
  );
  const deviceTime = requiredString(input.device_time, "device_time", 5);
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(deviceTime)) {
    throw new CandidateImageInputsError("device_time은 HH:MM 형식이어야 합니다.");
  }
  const backgroundSubject = requiredString(input.background_subject, "background_subject", 40);
  if (!ALLOWED_BACKGROUND_SUBJECTS.has(backgroundSubject)) {
    throw new CandidateImageInputsError("지원하지 않는 background_subject입니다.");
  }
  const normalized = {
    trace_items: traceItems,
    trace_todos: traceTodos,
    device_time: deviceTime,
    background_subject: backgroundSubject,
    background_mood: requiredString(input.background_mood, "background_mood", 40),
    language: normalizedLanguage(input.language ?? "ko"),
  };
  const searchQuery = optionalString(input.background_search_query, 200);
  if (searchQuery) normalized.background_search_query = searchQuery;
  else if (options.includeNullSearchQuery === true) normalized.background_search_query = null;
  return normalized;
}

export function describeScheduleEntry(entry) {
  const when = entry.days > 1 ? `D+${entry.day}~D+${entry.day + entry.days - 1}` : `D+${entry.day}`;
  const clock = entry.time ? ` ${entry.time}` : " 종일";
  return `${when}${clock} ${entry.title}`;
}

function scheduleList(value, options) {
  if (
    !Array.isArray(value)
    || value.length < options.minimum
    || value.length > options.maximum
  ) {
    throw new CandidateImageInputsError(
      `일정은 ${options.minimum}~${options.maximum}개여야 합니다.`,
    );
  }
  return value.map((item) => normalizeScheduleEntry(item, options));
}

function normalizeScheduleEntry(item, options) {
  if (typeof item === "string") {
    if (!options.acceptLegacy) {
      throw new CandidateImageInputsError("일정 항목은 주간 일정 객체여야 합니다.");
    }
    const match = /^((?:[01]\d|2[0-3]):[0-5]\d)\s+(\S.*)$/u.exec(item);
    const title = match ? match[2] : item.trim();
    if (!title) throw new CandidateImageInputsError("일정 제목이 비어 있습니다.");
    return {
      title: title.slice(0, 40),
      day: 0,
      days: 1,
      time: match ? match[1] : null,
      color: null,
      ...(options.includeStructuredMarker ? { structured: false } : {}),
    };
  }
  if (!item || typeof item !== "object" || Array.isArray(item)) {
    throw new CandidateImageInputsError("일정 항목의 형식이 올바르지 않습니다.");
  }
  const title = requiredString(item.title, "일정 제목", 40);
  const day = boundedInteger(item.day ?? 0, 0, WEEK_DAYS - 1, "일정 day");
  const days = boundedInteger(item.days ?? 1, 1, WEEK_DAYS, "일정 days");
  if (day + days > WEEK_DAYS) {
    throw new CandidateImageInputsError("일정이 이번 주를 넘어갑니다.");
  }
  const time = optionalString(item.time, 5);
  if (time && !/^(?:[01]\d|2[0-3]):[0-5]\d$/u.test(time)) {
    throw new CandidateImageInputsError("일정 시각은 HH:MM 형식이어야 합니다.");
  }
  const color = optionalString(item.color, 6);
  if (color && !ALLOWED_EVENT_COLORS.has(color)) {
    throw new CandidateImageInputsError("일정 색상이 Trace 팔레트에 없습니다.");
  }
  return {
    title,
    day,
    days,
    time: time || null,
    color: color || null,
    ...(options.includeStructuredMarker ? { structured: item.structured !== false } : {}),
  };
}

function stringList(value, minimum, maximum, maximumLength) {
  if (!Array.isArray(value) || value.length < minimum || value.length > maximum) {
    throw new CandidateImageInputsError(`목록은 ${minimum}~${maximum}개여야 합니다.`);
  }
  return value.map((item) => requiredString(item, "목록 항목", maximumLength));
}

function boundedInteger(value, minimum, maximum, label) {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new CandidateImageInputsError(
      `${label}은(는) ${minimum}~${maximum} 사이의 정수여야 합니다.`,
    );
  }
  return value;
}

function normalizedLanguage(value) {
  const language = requiredString(value, "language", 20);
  if (!/^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$/.test(language)) {
    throw new CandidateImageInputsError("language 형식이 올바르지 않습니다.");
  }
  return language;
}

function requiredString(value, field, maximum) {
  const normalized = optionalString(value, maximum);
  if (!normalized) throw new CandidateImageInputsError(`${field} 값이 필요합니다.`);
  return normalized;
}

function optionalString(value, maximum) {
  if (value === undefined || value === null) return "";
  if (typeof value !== "string") throw new CandidateImageInputsError("문자열 입력이 필요합니다.");
  const normalized = value.trim();
  if (normalized.length > maximum) {
    throw new CandidateImageInputsError(`입력이 ${maximum}자를 초과했습니다.`);
  }
  return normalized;
}
