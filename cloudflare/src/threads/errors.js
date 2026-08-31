export class ThreadsGraphError extends Error {
  constructor(code, message, options = {}) {
    super(message);
    this.name = "ThreadsGraphError";
    this.code = code;
    this.status = options.status ?? null;
    this.details = Object.freeze({ ...(options.details ?? {}) });
  }
}

export const configError = (field) => new ThreadsGraphError(
  "THREADS_CONFIG_INVALID",
  `Threads Graph client configuration is missing or invalid: ${field}`,
);

export const malformed = (context) => new ThreadsGraphError(
  "THREADS_RESPONSE_MALFORMED",
  `Threads Graph returned a malformed ${context} response`,
);

export const requireRecord = (value, context) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw malformed(context);
  return value;
};

export const requireString = (value, context) => {
  if (typeof value !== "string" || value.length === 0) throw malformed(context);
  return value;
};

export const requireNonNegativeInteger = (value, context) => {
  if (!Number.isSafeInteger(value) || value < 0) throw malformed(context);
  return value;
};

export const requireInputString = (value, field) => {
  if (typeof value !== "string" || value.length === 0) {
    throw new ThreadsGraphError(
      "THREADS_INPUT_INVALID",
      `Threads Graph input is invalid: ${field}`,
    );
  }
  return value;
};

export const parsePositiveInteger = (value, field, maximum) => {
  if (!Number.isSafeInteger(value) || value < 1 || value > maximum) {
    throw new ThreadsGraphError(
      "THREADS_INPUT_INVALID",
      `Threads Graph input is invalid: ${field}`,
    );
  }
  return value;
};

export const requestTimeout = () => new ThreadsGraphError(
  "THREADS_REQUEST_TIMEOUT",
  "Threads Graph request exceeded its time limit",
);

export const httpError = (status, retryAfterMs) => {
  const options = { status };
  if (retryAfterMs !== null) options.details = { retryAfterMs };
  if (status === 401 || status === 403) {
    return new ThreadsGraphError(
      "THREADS_REAUTH_REQUIRED",
      "Threads profile authorization must be renewed",
      options,
    );
  }
  if (status === 404) {
    return new ThreadsGraphError(
      "THREADS_RESOURCE_NOT_FOUND",
      "Threads Graph resource was not found",
      options,
    );
  }
  if (status === 429) {
    return new ThreadsGraphError(
      "THREADS_RATE_LIMITED",
      "Threads Graph rate limit was reached",
      options,
    );
  }
  if (status >= 500) {
    return new ThreadsGraphError(
      "THREADS_UPSTREAM_UNAVAILABLE",
      "Threads Graph is temporarily unavailable",
      options,
    );
  }
  return new ThreadsGraphError(
    "THREADS_REQUEST_REJECTED",
    "Threads Graph rejected the request",
    options,
  );
};
