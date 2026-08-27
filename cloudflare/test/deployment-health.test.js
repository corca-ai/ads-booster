import assert from "node:assert/strict";
import test from "node:test";

import { deploymentHealth } from "../src/deployment-health.js";

test("deployment health exposes the exact deployed commit", () => {
  const commitSha = "0123456789abcdef0123456789abcdef01234567";
  assert.deepEqual(deploymentHealth({ TRACE_DEPLOY_SHA: commitSha }), {
    ok: true,
    commit_sha: commitSha,
  });
});

test("deployment health rejects ambiguous production provenance", () => {
  assert.throws(
    () => deploymentHealth({ TRACE_DEPLOY_SHA: "main" }),
    /full lowercase commit SHA/u,
  );
  assert.deepEqual(deploymentHealth({}), { ok: true, commit_sha: "local" });
});
