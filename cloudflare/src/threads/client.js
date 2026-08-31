import {
  configError,
  malformed,
  parsePositiveInteger,
  requireInputString,
  ThreadsGraphError,
} from "./errors.js";
import {
  INSIGHT_METRICS,
  parseAuthorizationToken,
  parseId,
  parseInsights,
  parseLongLivedToken,
  parsePost,
  parseProfile,
  parsePublishingLimit,
  parseReplyPage,
  parseTokenInspection,
} from "./responses.js";
import { ThreadsGraphTransport } from "./transport.js";

export { ThreadsGraphError } from "./errors.js";

export const THREADS_REQUIRED_SCOPES = Object.freeze([
  "threads_basic",
  "threads_content_publish",
  "threads_manage_insights",
  "threads_read_replies",
]);

const requireHttpsUrl = (value) => {
  requireInputString(value, "imageUrl");
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new ThreadsGraphError("THREADS_INPUT_INVALID", "Threads image URL must use HTTPS");
  }
  if (parsed.protocol !== "https:") {
    throw new ThreadsGraphError("THREADS_INPUT_INVALID", "Threads image URL must use HTTPS");
  }
  return value;
};

export class ThreadsGraphClient {
  constructor({ appId, appSecret, redirectUri, apiVersion, ...transportOptions }) {
    if (typeof appId !== "string" || appId.length === 0) throw configError("THREADS_APP_ID");
    if (typeof appSecret !== "string" || appSecret.length === 0) throw configError("THREADS_APP_SECRET");
    if (typeof redirectUri !== "string" || redirectUri.length === 0) {
      throw configError("THREADS_REDIRECT_URI");
    }
    try {
      if (new URL(redirectUri).protocol !== "https:") throw configError("THREADS_REDIRECT_URI");
    } catch (error) {
      if (error instanceof ThreadsGraphError) throw error;
      throw configError("THREADS_REDIRECT_URI");
    }
    this.appId = appId;
    this.appSecret = appSecret;
    this.redirectUri = redirectUri;
    this.transport = new ThreadsGraphTransport({ apiVersion, ...transportOptions });
  }

  async exchangeAuthorizationCode(code) {
    requireInputString(code, "code");
    return parseAuthorizationToken(await this.transport.post(this.transport.authUrl(
      "oauth/access_token",
      {
        client_id: this.appId,
        client_secret: this.appSecret,
        code,
        grant_type: "authorization_code",
        redirect_uri: this.redirectUri,
      },
    )));
  }

  async exchangeLongLivedToken(shortLivedToken) {
    requireInputString(shortLivedToken, "accessToken");
    const payload = await this.transport.get(this.transport.authUrl("access_token", {
      grant_type: "th_exchange_token",
      client_secret: this.appSecret,
      access_token: shortLivedToken,
    }));
    return parseLongLivedToken(payload, "long-lived token");
  }

  async refreshLongLivedToken(longLivedToken) {
    requireInputString(longLivedToken, "accessToken");
    const payload = await this.transport.get(this.transport.authUrl("refresh_access_token", {
      grant_type: "th_refresh_token",
      access_token: longLivedToken,
    }));
    return parseLongLivedToken(payload, "refreshed token");
  }

  async inspectAccessToken(accessToken) {
    requireInputString(accessToken, "accessToken");
    return parseTokenInspection(await this.transport.get(this.transport.graphUrl("debug_token", {
      input_token: accessToken,
      access_token: `${this.appId}|${this.appSecret}`,
    })));
  }

  async getProfile(accessToken) {
    requireInputString(accessToken, "accessToken");
    return parseProfile(await this.transport.get(this.transport.graphUrl("me", {
      fields: "id,username",
      access_token: accessToken,
    })));
  }

  async getValidatedProfile(accessToken, requiredScopes = THREADS_REQUIRED_SCOPES) {
    if (!Array.isArray(requiredScopes) || requiredScopes.length === 0) {
      throw new ThreadsGraphError("THREADS_INPUT_INVALID", "Threads required scopes are invalid");
    }
    const expectedScopes = [...new Set(
      requiredScopes.map((scope) => requireInputString(scope, "scope")),
    )];
    const inspection = await this.inspectAccessToken(accessToken);
    if (
      !inspection.isValid
      || inspection.appId !== this.appId
      || inspection.expiresAt * 1000 <= this.transport.now()
    ) {
      throw new ThreadsGraphError(
        "THREADS_REAUTH_REQUIRED",
        "Threads profile authorization must be renewed",
      );
    }
    const missingScopes = expectedScopes.filter((scope) => !inspection.scopes.includes(scope));
    if (missingScopes.length > 0) {
      throw new ThreadsGraphError(
        "THREADS_REQUIRED_SCOPES_MISSING",
        "Threads profile authorization is missing required scopes",
        { details: { missingScopes } },
      );
    }
    const profile = await this.getProfile(accessToken);
    if (profile.id !== inspection.userId) throw malformed("profile identity");
    return { ...profile, scopes: inspection.scopes, expiresAt: inspection.expiresAt };
  }

  async getPublishingLimit(accessToken) {
    requireInputString(accessToken, "accessToken");
    return parsePublishingLimit(await this.transport.get(this.transport.graphUrl(
      "me/threads_publishing_limit",
      { fields: "quota_usage,config", access_token: accessToken },
    )));
  }

  async createImageContainer({ accessToken, imageUrl, text, altText }) {
    requireInputString(accessToken, "accessToken");
    requireInputString(text, "text");
    const result = parseId(await this.transport.post(this.transport.graphUrl("me/threads", {
      media_type: "IMAGE",
      image_url: requireHttpsUrl(imageUrl),
      text,
      alt_text: altText === undefined ? undefined : requireInputString(altText, "altText"),
      access_token: accessToken,
    })), "image container");
    return { containerId: result.id };
  }

  async publishContainer(containerId, accessToken) {
    requireInputString(containerId, "containerId");
    requireInputString(accessToken, "accessToken");
    const result = parseId(await this.transport.post(this.transport.graphUrl(
      "me/threads_publish",
      { creation_id: containerId, access_token: accessToken },
    ), { ambiguousOnFailure: true }), "publish");
    return { postId: result.id };
  }

  async getPost(postId, accessToken) {
    requireInputString(postId, "postId");
    requireInputString(accessToken, "accessToken");
    const post = parsePost(await this.transport.get(this.transport.graphUrl(postId, {
      fields: "id,permalink,media_type,timestamp",
      access_token: accessToken,
    })));
    if (post.id !== postId) throw malformed("post identity");
    return post;
  }

  async getPostInsights(postId, accessToken) {
    requireInputString(postId, "postId");
    requireInputString(accessToken, "accessToken");
    return parseInsights(await this.transport.get(this.transport.graphUrl(`${postId}/insights`, {
      metric: INSIGHT_METRICS.join(","),
      access_token: accessToken,
    })));
  }

  async getTopLevelRepliesPage(postId, accessToken, { cursor, limit = 50 } = {}) {
    requireInputString(postId, "postId");
    requireInputString(accessToken, "accessToken");
    if (cursor !== undefined && cursor !== null) requireInputString(cursor, "cursor");
    parsePositiveInteger(limit, "limit", 100);
    return parseReplyPage(await this.transport.get(this.transport.graphUrl(`${postId}/replies`, {
      fields: "id,text,timestamp",
      reverse: "false",
      limit,
      after: cursor,
      access_token: accessToken,
    })));
  }

  async listTopLevelReplies(postId, accessToken, options = {}) {
    const { cursor = null, limit = 50, maxPages = 5 } = options;
    parsePositiveInteger(maxPages, "maxPages", 5);
    const replies = [];
    const seenCursors = new Set();
    let nextCursor = cursor;
    let pagesRead = 0;
    do {
      const page = await this.getTopLevelRepliesPage(postId, accessToken, {
        cursor: nextCursor,
        limit,
      });
      pagesRead += 1;
      replies.push(...page.replies);
      if (page.nextCursor === null) return { replies, nextCursor: null, pagesRead };
      if (seenCursors.has(page.nextCursor)) {
        throw new ThreadsGraphError(
          "THREADS_PAGINATION_CURSOR_REPEATED",
          "Threads replies returned a repeated pagination cursor",
        );
      }
      seenCursors.add(page.nextCursor);
      nextCursor = page.nextCursor;
    } while (pagesRead < maxPages);
    return { replies, nextCursor, pagesRead };
  }
}

export function createThreadsGraphClient(env, options = {}) {
  return new ThreadsGraphClient({
    appId: env?.THREADS_APP_ID,
    appSecret: env?.THREADS_APP_SECRET,
    redirectUri: env?.THREADS_REDIRECT_URI,
    apiVersion: env?.THREADS_GRAPH_API_VERSION,
    ...options,
  });
}
