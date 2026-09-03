import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const built = async (path) => readFile(new URL(`../dist/${path}`, import.meta.url), "utf8");

test("built workspace retains only the requested intake controls and candidate bulk deletion", async () => {
  const [markup, source] = await Promise.all([
    built("index.html"),
    built("static/workspace-live.js"),
  ]);
  assert.doesNotMatch(markup, /data-account-settings/u);
  assert.doesNotMatch(markup, /data-account-form/u);
  assert.doesNotMatch(markup, /data-context-select/u);
  assert.doesNotMatch(markup, /data-manual-entry/u);
  assert.match(markup, /data-account-propose/u);
  assert.match(markup, /data-autogen/u);
  assert.match(markup, /data-candidate-delete-all/u);
  assert.match(source, /const createProposalAccount/u);
  assert.match(source, /createProposalAccount\(proposal, use\)/u);
  assert.match(source, /const deleteAllCandidates/u);
  assert.match(source, /request\("\/api\/candidates", \{[\s\S]*method: "DELETE"/u);
});

test("built workspace exposes separate Mac and Threads operations controls", async () => {
  const markup = await built("index.html");
  assert.match(markup, /data-worker-manager-open/u);
  assert.match(markup, /data-threads-manager-open/u);
  assert.match(markup, /data-threads-unlock-form/u);
  assert.match(markup, /data-threads-profile-list/u);
  assert.match(markup, /data-threads-toggle/u);
  assert.match(markup, /OFF로 바꾸면 아직 발행 장벽을 넘지 않은 예약만 취소/u);
});

test("built workspace exposes an account-scoped worker execution timeline", async () => {
  const [markup, source, styles] = await Promise.all([
    built("index.html"),
    built("static/workspace-live.js"),
    built("static/workspace.css"),
  ]);
  assert.match(markup, /data-worker-events/u);
  assert.match(markup, /data-worker-event-list/u);
  assert.match(markup, /실행 기록/u);
  assert.match(source, /request\("\/api\/worker-events"\)/u);
  assert.match(source, /preparation_started/u);
  assert.match(source, /execution_started/u);
  assert.match(source, /callback_applied/u);
  assert.match(source, /const eventAccountId = selectedAccountId/u);
  assert.match(source, /eventAccountId !== selectedAccountId/u);
  assert.match(source, /sameWorkerEvents/u);
  assert.match(markup, /data-worker-event-count[^>]+role="status"[^>]+aria-live="polite"/u);
  assert.doesNotMatch(markup, /data-worker-event-list[^>]+aria-live/u);
  assert.match(styles, /\.worker-events > summary::after/u);
  assert.match(styles, /\.worker-events\[open\] > summary::after/u);
  assert.match(styles, /\.worker-events > summary:focus-visible/u);
});

test("control token remains memory-only and popup completion is origin and source checked", async () => {
  const source = await built("static/workspace-live.js");
  assert.match(source, /let controlPlaneToken = ""/u);
  assert.match(source, /headers\.set\("Authorization", `Bearer \$\{controlPlaneToken\}`\)/u);
  assert.match(source, /event\.origin !== window\.location\.origin/u);
  assert.match(source, /event\.source !== threadsPopup/u);
  assert.match(source, /event\.data\?\.type !== "threads-oauth-complete"/u);
  assert.match(source, /window\.addEventListener\("beforeunload"[\s\S]*controlPlaneToken = ""/u);
  assert.doesNotMatch(source, /localStorage\.setItem\([^\n]*(?:token|oauth|code|secret)/iu);
});

test("profile default, toggle, reconnect, disconnect, and candidate target use privileged requests", async () => {
  const source = await built("static/workspace-live.js");
  assert.match(source, /controlPlaneRequest\(`\/api\/threads\/profiles\/\$\{[^}]+\}\/default`/u);
  assert.match(source, /controlPlaneRequest\("\/api\/threads\/settings"/u);
  assert.match(source, /\/reconnect`\)/u);
  assert.match(source, /\/disconnect`/u);
  assert.match(source, /\/threads-profile`/u);
  assert.match(source, /select\.disabled = !controlPlaneToken/u);
});

test("publication UI distinguishes every durable state and has no unknown publish retry", async () => {
  const source = await built("static/workspace-live.js");
  for (const state of [
    "scheduled",
    "creating_container",
    "container_ready",
    "publishing",
    "published",
    "canceled",
    "failed",
    "unknown_side_effect",
    "rate_limited",
    "auth_required",
    "unavailable",
  ]) assert.match(source, new RegExp(`${state}:`, "u"));
  assert.match(source, /최상위 답글 보기/u);
  assert.match(source, /ID로 readback 확인/u);
  assert.doesNotMatch(source, /publish 재시도|게시 재시도/iu);
  assert.match(source, /조회.*좋아요.*답글.*리포스트.*인용.*공유/su);
});

test("the caption approval card shows the background search query", async () => {
  const source = await built("static/workspace-live.js");
  const card = source.slice(source.indexOf("const approvalNode ="));
  const caption = card.slice(0, card.indexOf("const approvalField"));

  // The query is written during generation and decides which wallpaper the search returns,
  // so a reviewer has to see it while approving the caption. Shown only on the image card,
  // a bad query costs a capture before anyone can reject it.
  assert.match(caption, /text\.append\(topicLabel, topic, caption, backgroundQueryNode\(record\)\)/u);
});

test("the account workspace carries the background asset review tab", async () => {
  const [markup, source] = await Promise.all([
    built("index.html"),
    built("static/workspace-live.js"),
  ]);

  // The tab is hosted-only: the pool hangs off a hosted persona, and the local surface has
  // no personas to hang one on.
  assert.match(markup, /data-tab="assets" data-hosted-only hidden>배경 자산</u);
  assert.match(markup, /data-panel="assets" data-hosted-only hidden/u);
  assert.match(markup, /data-asset-pending\b/u);
  assert.match(markup, /data-asset-approved\b/u);

  // Review actions hit the API this panel exists for, and entering an account loads the
  // pool alongside the candidates.
  assert.match(source, /\/api\/background-assets\/\$\{encodeURIComponent\(asset\.asset_id\)\}\/review/u);
  assert.match(source, /background-assets`,\n\s+\);/u);
  assert.match(source, /void loadBackgroundAssets\(\);/u);
});
