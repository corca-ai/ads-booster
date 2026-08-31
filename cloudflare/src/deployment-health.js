import { threadsConfigurationState } from "./threads/config.js";

const COMMIT_SHA = /^[0-9a-f]{40}$/u;

export const deploymentHealth = (env) => {
  const commitSha = env.TRACE_DEPLOY_SHA ?? "local";
  if (commitSha !== "local" && !COMMIT_SHA.test(commitSha)) {
    throw new Error("TRACE_DEPLOY_SHA must be a full lowercase commit SHA");
  }
  const threadsReady = threadsConfigurationState(env) === "ready";
  return { ok: true, commit_sha: commitSha, threads_ready: threadsReady };
};
