import {
  malformed,
  requireNonNegativeInteger,
  requireRecord,
  requireString,
} from "./errors.js";

export const INSIGHT_METRICS = Object.freeze([
  "views",
  "likes",
  "replies",
  "reposts",
  "quotes",
  "shares",
]);

export const parseJson = async (response) => {
  try {
    return await response.json();
  } catch {
    throw malformed("JSON");
  }
};

export const parseAuthorizationToken = (payload) => {
  const record = requireRecord(payload, "authorization token");
  return {
    accessToken: requireString(record.access_token, "authorization token"),
    userId: requireString(record.user_id, "authorization token"),
  };
};

export const parseLongLivedToken = (payload, context) => {
  const record = requireRecord(payload, context);
  const tokenType = requireString(record.token_type, context);
  if (tokenType !== "bearer") throw malformed(context);
  return {
    accessToken: requireString(record.access_token, context),
    tokenType,
    expiresIn: requireNonNegativeInteger(record.expires_in, context),
  };
};

export const parseTokenInspection = (payload) => {
  const data = requireRecord(requireRecord(payload, "token inspection").data, "token inspection");
  if (!Array.isArray(data.scopes) || typeof data.is_valid !== "boolean") {
    throw malformed("token inspection");
  }
  return {
    appId: requireString(data.app_id, "token inspection"),
    userId: requireString(data.user_id, "token inspection"),
    isValid: data.is_valid,
    expiresAt: requireNonNegativeInteger(data.expires_at, "token inspection"),
    scopes: data.scopes.map((scope) => requireString(scope, "token inspection")),
  };
};

export const parseProfile = (payload) => {
  const record = requireRecord(payload, "profile");
  return {
    id: requireString(record.id, "profile"),
    username: requireString(record.username, "profile"),
  };
};

export const parsePublishingLimit = (payload) => {
  const record = requireRecord(payload, "publishing limit");
  if (!Array.isArray(record.data) || record.data.length !== 1) {
    throw malformed("publishing limit");
  }
  const item = requireRecord(record.data[0], "publishing limit");
  const config = requireRecord(item.config, "publishing limit");
  return {
    quotaUsage: requireNonNegativeInteger(item.quota_usage, "publishing limit"),
    quotaTotal: requireNonNegativeInteger(config.quota_total, "publishing limit"),
    quotaDuration: requireNonNegativeInteger(config.quota_duration, "publishing limit"),
  };
};

export const parseId = (payload, context) => ({
  id: requireString(requireRecord(payload, context).id, context),
});

export const parsePost = (payload) => {
  const record = requireRecord(payload, "post");
  const permalink = requireString(record.permalink, "post");
  try {
    if (new URL(permalink).protocol !== "https:") throw malformed("post");
  } catch (error) {
    if (error?.code === "THREADS_RESPONSE_MALFORMED") throw error;
    throw malformed("post");
  }
  return {
    id: requireString(record.id, "post"),
    permalink,
    mediaType: requireString(record.media_type, "post"),
    timestamp: requireString(record.timestamp, "post"),
  };
};

export const parseInsights = (payload) => {
  const record = requireRecord(payload, "post insights");
  if (!Array.isArray(record.data) || record.data.length !== INSIGHT_METRICS.length) {
    throw malformed("post insights");
  }
  const result = {};
  for (const rawInsight of record.data) {
    const insight = requireRecord(rawInsight, "post insights");
    const name = requireString(insight.name, "post insights");
    if (!INSIGHT_METRICS.includes(name) || insight.period !== "lifetime" || name in result) {
      throw malformed("post insights");
    }
    if (!Array.isArray(insight.values) || insight.values.length !== 1) {
      throw malformed("post insights");
    }
    const value = requireRecord(insight.values[0], "post insights").value;
    result[name] = requireNonNegativeInteger(value, "post insights");
  }
  return result;
};

export const parseReplyPage = (payload) => {
  const record = requireRecord(payload, "replies");
  if (!Array.isArray(record.data)) throw malformed("replies");
  const replies = record.data.map((rawReply) => {
    const reply = requireRecord(rawReply, "reply");
    return {
      id: requireString(reply.id, "reply"),
      text: requireString(reply.text, "reply"),
      timestamp: requireString(reply.timestamp, "reply"),
    };
  });
  if (record.paging === undefined) return { replies, nextCursor: null };
  const paging = requireRecord(record.paging, "replies paging");
  if (paging.cursors === undefined) return { replies, nextCursor: null };
  const cursors = requireRecord(paging.cursors, "replies paging");
  const cursor = cursors.after;
  if (cursor !== undefined && (typeof cursor !== "string" || cursor.length === 0)) {
    throw malformed("replies paging");
  }
  return { replies, nextCursor: cursor ?? null };
};
