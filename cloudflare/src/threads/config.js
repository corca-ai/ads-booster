const GRAPH_VERSION = /^v[1-9][0-9]*\.[0-9]+$/u;
const TOKEN_KEY = /^v[1-9][0-9]*:[A-Za-z0-9+/]+={0,2}$/u;

export const THREADS_PUBLIC_BINDINGS = Object.freeze([
  "THREADS_APP_ID",
  "THREADS_GRAPH_API_VERSION",
  "THREADS_PUBLIC_ORIGIN",
  "THREADS_REDIRECT_URI",
]);

export const THREADS_SECRET_BINDINGS = Object.freeze([
  "THREADS_APP_SECRET",
  "THREADS_MEDIA_SIGNING_KEY",
  "THREADS_TOKEN_ENCRYPTION_KEY",
]);

const THREADS_RUNTIME_BINDINGS = Object.freeze([
  ...THREADS_PUBLIC_BINDINGS,
  ...THREADS_SECRET_BINDINGS,
]);

const configured = (value) => typeof value === "string" && value.length > 0;

const configurationState = (env, bindings) => {
  const configuredBindings = bindings.filter((name) => configured(env?.[name]));
  if (configuredBindings.length === 0) return "disabled";
  const missing = bindings.find((name) => !configured(env?.[name]));
  if (missing) {
    throw new Error(`Threads bindings must be configured together; missing required binding: ${missing}`);
  }
  return "ready";
};

const requireHttps = (env, name) => {
  let url;
  try {
    url = new URL(env[name]);
  } catch (error) {
    throw new Error(`${name} must use HTTPS`, { cause: error });
  }
  if (url.protocol !== "https:") throw new Error(`${name} must use HTTPS`);
};

const validatePublicConfiguration = (env) => {
  if (!GRAPH_VERSION.test(env.THREADS_GRAPH_API_VERSION)) {
    throw new Error("THREADS_GRAPH_API_VERSION must be pinned to vN.N");
  }
  requireHttps(env, "THREADS_PUBLIC_ORIGIN");
  requireHttps(env, "THREADS_REDIRECT_URI");
};

export const threadsPublicVariables = (env) => {
  if (configurationState(env, THREADS_PUBLIC_BINDINGS) === "disabled") return {};
  validatePublicConfiguration(env);
  return Object.fromEntries(THREADS_PUBLIC_BINDINGS.map((name) => [name, env[name]]));
};

export const threadsConfigurationState = (env) => {
  const state = configurationState(env, THREADS_RUNTIME_BINDINGS);
  if (state === "disabled") return state;
  validatePublicConfiguration(env);
  if (!TOKEN_KEY.test(env.THREADS_TOKEN_ENCRYPTION_KEY)) {
    throw new Error("THREADS_TOKEN_ENCRYPTION_KEY must be a versioned key");
  }
  if (new TextEncoder().encode(env.THREADS_MEDIA_SIGNING_KEY).byteLength < 32) {
    throw new Error("THREADS_MEDIA_SIGNING_KEY must be at least 32 bytes");
  }
  return state;
};
