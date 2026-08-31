import {
  configError,
  httpError,
  malformed,
  requestTimeout,
  ThreadsGraphError,
} from "./errors.js";
import { parseJson } from "./responses.js";

const GRAPH_ORIGIN = "https://graph.threads.net";
const API_VERSION_PATTERN = /^v[1-9][0-9]*\.[0-9]+$/u;
const DEFAULT_GET_ATTEMPTS = 3;
const DEFAULT_MAX_RETRY_DELAY_MS = 10_000;
const DEFAULT_REQUEST_TIMEOUT_MS = 10_000;

const retryAfterMilliseconds = (response, now, maximum) => {
  const value = response.headers.get("retry-after");
  if (value === null) return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) {
    return Math.min(Math.round(seconds * 1000), maximum);
  }
  const timestamp = Date.parse(value);
  if (!Number.isNaN(timestamp)) return Math.min(Math.max(timestamp - now(), 0), maximum);
  return null;
};

const requireResponse = (response) => {
  if (
    !response || typeof response !== "object"
    || typeof response.ok !== "boolean"
    || !Number.isSafeInteger(response.status)
    || typeof response.headers?.get !== "function"
    || typeof response.json !== "function"
  ) {
    throw malformed("HTTP");
  }
  return response;
};

const appendParams = (url, params) => {
  for (const [name, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) url.searchParams.set(name, String(value));
  }
  return url;
};

export class ThreadsGraphTransport {
  constructor({
    apiVersion,
    fetchImpl = globalThis.fetch,
    sleeper = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
    now = Date.now,
    maxGetAttempts = DEFAULT_GET_ATTEMPTS,
    maxRetryDelayMs = DEFAULT_MAX_RETRY_DELAY_MS,
    requestTimeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
  }) {
    if (typeof apiVersion !== "string" || !API_VERSION_PATTERN.test(apiVersion)) {
      throw configError("THREADS_GRAPH_API_VERSION");
    }
    if (typeof fetchImpl !== "function") throw configError("fetch");
    if (typeof sleeper !== "function") throw configError("sleeper");
    if (!Number.isSafeInteger(maxGetAttempts) || maxGetAttempts < 1 || maxGetAttempts > 5) {
      throw configError("maxGetAttempts");
    }
    if (!Number.isSafeInteger(maxRetryDelayMs) || maxRetryDelayMs < 0 || maxRetryDelayMs > 60_000) {
      throw configError("maxRetryDelayMs");
    }
    if (!Number.isSafeInteger(requestTimeoutMs) || requestTimeoutMs < 1 || requestTimeoutMs > 60_000) {
      throw configError("requestTimeoutMs");
    }
    this.apiVersion = apiVersion;
    this.fetchImpl = fetchImpl;
    this.sleeper = sleeper;
    this.now = now;
    this.maxGetAttempts = maxGetAttempts;
    this.maxRetryDelayMs = maxRetryDelayMs;
    this.requestTimeoutMs = requestTimeoutMs;
  }

  authUrl(pathname, params = {}) {
    return appendParams(new URL(`/${pathname.replace(/^\/+/, "")}`, GRAPH_ORIGIN), params);
  }

  graphUrl(pathname, params = {}) {
    return appendParams(
      new URL(`/${this.apiVersion}/${pathname.replace(/^\/+/, "")}`, GRAPH_ORIGIN),
      params,
    );
  }

  async #fetch(url, method) {
    const controller = new AbortController();
    let timeoutId;
    const timeout = new Promise((_resolve, reject) => {
      timeoutId = setTimeout(() => {
        const error = requestTimeout();
        reject(error);
        controller.abort(error);
      }, this.requestTimeoutMs);
    });
    try {
      const pending = Promise.resolve().then(() => this.fetchImpl(url.href, {
        method,
        signal: controller.signal,
      }));
      return requireResponse(await Promise.race([pending, timeout]));
    } finally {
      clearTimeout(timeoutId);
    }
  }

  async get(url) {
    for (let attempt = 1; attempt <= this.maxGetAttempts; attempt += 1) {
      let response;
      try {
        response = await this.#fetch(url, "GET");
      } catch (error) {
        if (error instanceof ThreadsGraphError && error.code !== "THREADS_REQUEST_TIMEOUT") {
          throw error;
        }
        if (attempt === this.maxGetAttempts) {
          if (error instanceof ThreadsGraphError) throw error;
          throw new ThreadsGraphError("THREADS_NETWORK_ERROR", "Threads Graph could not be reached");
        }
        await this.sleeper(Math.min(250 * (2 ** (attempt - 1)), this.maxRetryDelayMs));
        continue;
      }
      if (response.ok) return parseJson(response);
      const retryAfterMs = retryAfterMilliseconds(response, this.now, this.maxRetryDelayMs);
      const retryable = response.status === 429 || response.status >= 500;
      if (retryable && attempt < this.maxGetAttempts) {
        const backoff = Math.min(250 * (2 ** (attempt - 1)), this.maxRetryDelayMs);
        await this.sleeper(retryAfterMs ?? backoff);
        continue;
      }
      throw httpError(response.status, retryAfterMs);
    }
    throw new ThreadsGraphError("THREADS_NETWORK_ERROR", "Threads Graph could not be reached");
  }

  async post(url, { ambiguousOnFailure = false } = {}) {
    let response;
    try {
      response = await this.#fetch(url, "POST");
    } catch (error) {
      if (ambiguousOnFailure) {
        throw new ThreadsGraphError(
          "THREADS_PUBLISH_AMBIGUOUS",
          "Threads publish outcome is unknown and must not be retried automatically",
        );
      }
      if (error instanceof ThreadsGraphError) throw error;
      throw new ThreadsGraphError("THREADS_NETWORK_ERROR", "Threads Graph could not be reached");
    }
    const retryAfterMs = retryAfterMilliseconds(response, this.now, this.maxRetryDelayMs);
    if (!response.ok) {
      if (ambiguousOnFailure && response.status >= 500) {
        throw new ThreadsGraphError(
          "THREADS_PUBLISH_AMBIGUOUS",
          "Threads publish outcome is unknown and must not be retried automatically",
          { status: response.status },
        );
      }
      throw httpError(response.status, retryAfterMs);
    }
    try {
      return await parseJson(response);
    } catch (error) {
      if (ambiguousOnFailure && error instanceof ThreadsGraphError) {
        throw new ThreadsGraphError(
          "THREADS_PUBLISH_AMBIGUOUS",
          "Threads publish outcome is unknown and must not be retried automatically",
          { status: response.status },
        );
      }
      throw error;
    }
  }
}
