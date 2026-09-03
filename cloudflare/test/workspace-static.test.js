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

test("built workspace exposes a separate governed marketing-agent review surface", async () => {
  const [markup, source, styles] = await Promise.all([
    built("index.html"),
    built("static/workspace-agent.js"),
    built("static/workspace.css"),
  ]);
  assert.match(markup, /data-marketing-agent/u);
  assert.match(markup, /data-agent-unlock-form/u);
  assert.match(markup, /data-agent-campaign-list/u);
  assert.match(markup, /data-agent-run-form/u);
  assert.match(markup, /data-agent-run-list/u);
  assert.match(markup, /검증된 context snapshot ID로 한 번 재개/u);
  assert.match(markup, /data-agent-review-list/u);
  assert.match(markup, /data-agent-review-detail/u);
  assert.match(markup, /최근 캠페인/u);
  assert.match(markup, /trace-marketing agent launch/u);
  assert.match(markup, /data-agent-product-source/u);
  assert.match(markup, /data-agent-product-ref/u);
  assert.match(source, /let agentControlToken = ""/u);
  assert.match(source, /headers\.set\("Authorization", `Bearer \$\{agentControlToken\}`\)/u);
  assert.match(source, /headers\.set\("X-Trace-Account-ID", expectedAccount\)/u);
  assert.match(source, /"\/api\/marketing-agent\/review-queue"/u);
  assert.match(source, /"\/api\/marketing-agent\/campaigns"/u);
  assert.match(source, /"\/api\/marketing-agent\/runs"/u);
  assert.match(source, /run\.loop\?\.state === "needs_input"/u);
  assert.match(source, /run\.next_intent\?\.requested_scope === "customer_intelligence"/u);
  assert.match(source, /const resumeDrafts = new Map\(\)/u);
  assert.match(source, /draft\.resumeId \|\|= crypto\.randomUUID\(\)/u);
  assert.match(source, /`\/api\/marketing-agent\/runs\/\$\{encodeURIComponent\(run\.run_id\)\}\/resume`/u);
  assert.match(source, /`\/api\/marketing-agent\/runs\/\$\{encodeURIComponent\(run\.run_id\)\}\/journey`/u);
  assert.match(source, /성과 루프 보기/u);
  assert.match(source, /node\.mode === "shadow"[\s\S]*그림자 전략 · 외부 효과 없음/u);
  assert.match(source, /node\.state === "published" \|\| node\.state === "observing"/u);
  assert.match(source, /성과 관찰 중/u);
  assert.match(source, /실행·검수 준비 중/u);
  assert.match(source, /계보 무결성 확인 필요/u);
  assert.match(source, /journey\.integrity_state !== "verified"/u);
  assert.match(source, /journey\.integrity_state === "launch_pending"/u);
  assert.match(source, /제품 근거 조사·전략 준비 중/u);
  assert.match(source, /outcome\.reassessment_state/u);
  assert.match(source, /outcome\.learning_state/u);
  assert.match(source, /성과 재평가 완료/u);
  assert.match(source, /학습 후보 검수 대기/u);
  assert.doesNotMatch(source, /성과 관찰 대기/u);
  assert.doesNotMatch(source, /reassessment_ready/u);
  assert.match(source, /renderedAccount !== accountId\(\)/u);
  assert.match(source, /schema_version: "trace\.marketing-agent-resume-request\.v1"/u);
  assert.match(source, /resume_id: draft\.resumeId/u);
  assert.match(source, /expected_head_step_sha256: run\.loop\.head_step_sha256/u);
  assert.match(source, /marketing_context_snapshot_id: snapshotId/u);
  assert.match(source, /renderedAccount !== accountId\(\)/u);
  assert.match(source, /renderedAccount,/u);
  assert.match(source, /resumeDrafts\.clear\(\)/u);
  assert.doesNotMatch(source, /raw[_ -]?evidence/iu);
  assert.match(styles, /\.agent-resume/u);
  assert.match(styles, /\.agent-run-journey/u);
  assert.match(source, /method: "POST", body: JSON\.stringify\(requestBody\)/u);
  assert.match(source, /requestBody\?\.research\?\.account_id !== submittedAccount/u);
  assert.match(source, /실행은 하지 말고 JSON만 반환해/u);
  assert.match(source, /item\.review_packet_path/u);
  assert.match(source, /approval\.action\.body/u);
  assert.match(source, /SHA-256 \$\{item\.target\.sha256\}/u);
  assert.match(source, /"검증된 원본", packet\.source/u);
  assert.match(source, /"관찰 결과", packet\.outcomes/u);
  assert.match(source, /제품 소스와 정확한 ref를 읽고 digest로 고정할 수 없으면/u);
  assert.match(source, /action\.method !== "POST"/u);
  assert.match(source, /action\.path\.startsWith\("\/api\/marketing-agent\/"\)/u);
  assert.match(source, /window\.addEventListener\("beforeunload"[\s\S]*agentControlToken = ""/u);
  assert.match(source, /pre\.textContent = compactValue\(value\)/u);
  assert.match(source, /selectedReview\.accountId !== accountId\(\)/u);
  assert.match(source, /generation !== reviewGeneration/u);
  assert.match(source, /agentControlToken = new FormData[\s\S]*unlockForm\.reset\(\)/u);
  assert.doesNotMatch(source, /innerHTML|insertAdjacentHTML|document\.write/u);
  assert.doesNotMatch(source, /localStorage\.(?:setItem|getItem)\([^\n]*(?:token|secret)/iu);
  assert.doesNotMatch(source, /\/materializations|\/assignments|\/api\/threads/iu);
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
