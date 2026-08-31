import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = (path) => readFile(new URL(`../${path}`, import.meta.url), "utf8");

test("generated config keeps optional public Threads values separate from secrets", async () => {
  const template = await source("wrangler.template.jsonc");
  const renderer = await source("scripts/render-config.mjs");
  assert.match(template, /__THREADS_VARIABLES__/u);
  assert.match(renderer, /threadsPublicVariables\(env\)/u);
  assert.doesNotMatch(template, /THREADS_APP_SECRET|THREADS_TOKEN_ENCRYPTION_KEY|THREADS_MEDIA_SIGNING_KEY/u);
});

test("deployment keeps absent Threads configuration disabled", async () => {
  const [workflow, index] = await Promise.all([
    source("../.github/workflows/deploy-cloudflare.yml"),
    source("src/index.js"),
  ]);
  assert.doesNotMatch(workflow, /missing THREADS_[A-Z_]+ repository variable/u);
  assert.match(workflow, /\(\.threads_ready \| type\) == "boolean"/u);
  assert.match(index, /threadsConfigurationState\(env\) === "ready"/u);
});

test("browser assets never serialize Graph credentials or signed media capability", async () => {
  const [markup, browser] = await Promise.all([
    source("dist/index.html"),
    source("dist/static/workspace-live.js"),
  ]);
  const assets = `${markup}\n${browser}`;
  assert.doesNotMatch(
    assets,
    /access_token|client_secret|THREADS_APP_SECRET|THREADS_TOKEN_ENCRYPTION_KEY|THREADS_MEDIA_SIGNING_KEY|[?&]signature=/u,
  );
  assert.doesNotMatch(browser, /localStorage\.setItem\([^\n]*(?:token|oauth|secret|code)/iu);
});

test("Mac worker and generic task payloads have no Threads publishing capability", async () => {
  const [worker, hosted, index] = await Promise.all([
    source("src/mac-workers.js"),
    source("src/hosted-workspace.js"),
    source("src/index.js"),
  ]);
  assert.doesNotMatch(worker, /threads|publishContainer|THREADS_/iu);
  const capturePayload = hosted.slice(
    hosted.indexOf("const body = {\n    schema_version"),
    hosted.indexOf("const taskStatement", hosted.indexOf("const body = {\n    schema_version")),
  );
  assert.doesNotMatch(capturePayload, /threads|token|signed|publish/iu);
  assert.match(index, /runHostedThreadsPublications\(env, dispatchHostedThreadsPublication\)/u);
  assert.doesNotMatch(index, /url\.pathname.*\/v1\/[\s\S]{0,200}dispatchHostedThreadsPublication/u);
});

test("OAuth callback contains only safe completion fields", async () => {
  const callback = await source("src/threads/oauth-callback.js");
  assert.match(callback, /threads-oauth-complete/u);
  assert.doesNotMatch(callback, /access_token|client_secret|authorization_code|oauth_state/u);
});
