const COMMIT_SHA = /^[0-9a-f]{40}$/u;
const GRAPH_VERSION = /^v[1-9][0-9]*\.[0-9]+$/u;
const REQUIRED_THREADS_BINDINGS = Object.freeze([
  "THREADS_APP_ID",
  "THREADS_APP_SECRET",
  "THREADS_GRAPH_API_VERSION",
  "THREADS_MEDIA_SIGNING_KEY",
  "THREADS_PUBLIC_ORIGIN",
  "THREADS_REDIRECT_URI",
  "THREADS_TOKEN_ENCRYPTION_KEY",
]);

export const deploymentHealth = (env) => {
  const commitSha = env.TRACE_DEPLOY_SHA ?? "local";
  if (commitSha !== "local" && !COMMIT_SHA.test(commitSha)) {
    throw new Error("TRACE_DEPLOY_SHA must be a full lowercase commit SHA");
  }
  for (const name of REQUIRED_THREADS_BINDINGS) {
    if (typeof env[name] !== "string" || env[name].length === 0) {
      throw new Error(`missing required binding: ${name}`);
    }
  }
  if (!GRAPH_VERSION.test(env.THREADS_GRAPH_API_VERSION)) {
    throw new Error("THREADS_GRAPH_API_VERSION must be pinned to vN.N");
  }
  for (const name of ["THREADS_PUBLIC_ORIGIN", "THREADS_REDIRECT_URI"]) {
    if (new URL(env[name]).protocol !== "https:") throw new Error(`${name} must use HTTPS`);
  }
  if (!/^v[1-9][0-9]*:[A-Za-z0-9+/]+={0,2}$/u.test(env.THREADS_TOKEN_ENCRYPTION_KEY)) {
    throw new Error("THREADS_TOKEN_ENCRYPTION_KEY must be a versioned key");
  }
  if (new TextEncoder().encode(env.THREADS_MEDIA_SIGNING_KEY).byteLength < 32) {
    throw new Error("THREADS_MEDIA_SIGNING_KEY must be at least 32 bytes");
  }
  return { ok: true, commit_sha: commitSha, threads_ready: true };
};
