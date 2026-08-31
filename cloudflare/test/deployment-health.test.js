import assert from "node:assert/strict";
import test from "node:test";

import { deploymentHealth } from "../src/deployment-health.js";

const threadsEnvironment = (overrides = {}) => ({
  THREADS_APP_ID: "configured",
  THREADS_APP_SECRET: "configured",
  THREADS_GRAPH_API_VERSION: "v1.0",
  THREADS_MEDIA_SIGNING_KEY: "m".repeat(32),
  THREADS_PUBLIC_ORIGIN: "https://workspace.example",
  THREADS_REDIRECT_URI: "https://workspace.example/api/threads/oauth/callback",
  THREADS_TOKEN_ENCRYPTION_KEY: "v1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
  ...overrides,
});

test("deployment health exposes the exact deployed commit", () => {
  const commitSha = "0123456789abcdef0123456789abcdef01234567";
  assert.deepEqual(deploymentHealth(threadsEnvironment({ TRACE_DEPLOY_SHA: commitSha })), {
    ok: true,
    commit_sha: commitSha,
    threads_ready: true,
  });
});

test("deployment health rejects ambiguous production provenance", () => {
  assert.throws(
    () => deploymentHealth(threadsEnvironment({ TRACE_DEPLOY_SHA: "main" })),
    /full lowercase commit SHA/u,
  );
  assert.deepEqual(deploymentHealth(threadsEnvironment()), {
    ok: true,
    commit_sha: "local",
    threads_ready: true,
  });
});

test("deployment health fails closed when Threads runtime bindings are missing", () => {
  assert.throws(
    () => deploymentHealth(threadsEnvironment({ THREADS_APP_SECRET: "" })),
    /missing required binding: THREADS_APP_SECRET/u,
  );
  assert.throws(
    () => deploymentHealth(threadsEnvironment({ THREADS_MEDIA_SIGNING_KEY: "short" })),
    /at least 32 bytes/u,
  );
});
