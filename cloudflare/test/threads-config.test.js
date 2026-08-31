import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { renderConfig } from "../scripts/render-config.mjs";
import {
  threadsConfigurationState,
  threadsPublicVariables,
} from "../src/threads/config.js";

const PUBLIC_THREADS = Object.freeze({
  THREADS_APP_ID: "app-id",
  THREADS_GRAPH_API_VERSION: "v1.0",
  THREADS_PUBLIC_ORIGIN: "https://workspace.example",
  THREADS_REDIRECT_URI: "https://workspace.example/api/threads/oauth/callback",
});
const COMPLETE_THREADS = Object.freeze({
  ...PUBLIC_THREADS,
  THREADS_APP_SECRET: "app-secret",
  THREADS_MEDIA_SIGNING_KEY: "m".repeat(32),
  THREADS_TOKEN_ENCRYPTION_KEY: "v1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
});
const BASE_CONFIG = Object.freeze({
  CF_D1_DATABASE_ID: "database-id",
  TRACE_DEPLOY_SHA: "0123456789abcdef0123456789abcdef01234567",
});

test("Threads configuration is disabled when every binding is absent", () => {
  assert.equal(threadsConfigurationState({}), "disabled");
  assert.deepEqual(threadsPublicVariables({}), {});
});

test("Threads configuration is ready only when every binding is complete", () => {
  assert.equal(threadsConfigurationState(COMPLETE_THREADS), "ready");
  assert.deepEqual(threadsPublicVariables(PUBLIC_THREADS), PUBLIC_THREADS);
});

test("Threads configuration rejects partial public and runtime bindings", () => {
  assert.throws(
    () => threadsPublicVariables({ THREADS_APP_ID: "app-id" }),
    /Threads bindings must be configured together/u,
  );
  assert.throws(
    () => threadsConfigurationState(PUBLIC_THREADS),
    /missing required binding: THREADS_APP_SECRET/u,
  );
});

test("production config omits Threads variables while the feature is disabled", async () => {
  const template = await readFile(new URL("../wrangler.template.jsonc", import.meta.url), "utf8");
  const rendered = renderConfig(template, BASE_CONFIG);
  assert.doesNotMatch(rendered, /THREADS_/u);
  assert.match(rendered, /"database_id": "database-id"/u);
  assert.match(rendered, new RegExp(BASE_CONFIG.TRACE_DEPLOY_SHA, "u"));
});

test("production config includes a complete public Threads configuration", async () => {
  const template = await readFile(new URL("../wrangler.template.jsonc", import.meta.url), "utf8");
  const rendered = renderConfig(template, { ...BASE_CONFIG, ...PUBLIC_THREADS });
  for (const [name, value] of Object.entries(PUBLIC_THREADS)) {
    assert.ok(rendered.includes(`${JSON.stringify(name)}: ${JSON.stringify(value)}`));
  }
});
