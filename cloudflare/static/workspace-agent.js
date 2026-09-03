(() => {
  "use strict";

  const root = document.querySelector("[data-marketing-agent]");
  const accountSelect = document.querySelector("[data-account-select]");
  if (!root || !accountSelect) return;

  const locked = root.querySelector("[data-agent-locked]");
  const panel = root.querySelector("[data-agent-panel]");
  const unlockForm = root.querySelector("[data-agent-unlock-form]");
  const feedback = root.querySelector("[data-agent-feedback]");
  const state = root.querySelector("[data-agent-state]");
  const accountNote = root.querySelector("[data-agent-account-note]");
  const campaignList = root.querySelector("[data-agent-campaign-list]");
  const campaignEmpty = root.querySelector("[data-agent-campaign-empty]");
  const reviewList = root.querySelector("[data-agent-review-list]");
  const reviewEmpty = root.querySelector("[data-agent-review-empty]");
  const reviewDetail = root.querySelector("[data-agent-review-detail]");
  const reviewContent = root.querySelector("[data-agent-review-content]");
  const reviewerId = root.querySelector("[data-agent-reviewer-id]");
  const approveButton = root.querySelector("[data-agent-approve]");
  const rejectButton = root.querySelector("[data-agent-reject]");
  const reviewFeedback = root.querySelector("[data-agent-review-feedback]");
  const startPrompt = root.querySelector("[data-agent-start-prompt]");
  const launchCommand = root.querySelector("[data-agent-launch-command]");
  const featureInput = root.querySelector("[data-agent-feature]");
  const outcomeInput = root.querySelector("[data-agent-outcome]");
  const controlInput = root.querySelector("[data-agent-control]");
  const productSourceInput = root.querySelector("[data-agent-product-source]");
  const productRefInput = root.querySelector("[data-agent-product-ref]");
  const runForm = root.querySelector("[data-agent-run-form]");
  const runInput = root.querySelector("[data-agent-run-input]");
  const runFeedback = root.querySelector("[data-agent-run-feedback]");
  const runList = root.querySelector("[data-agent-run-list]");
  const runEmpty = root.querySelector("[data-agent-run-empty]");

  let agentControlToken = "";
  let selectedReview = null;
  let pollTimer = null;
  let loadGeneration = 0;
  let reviewGeneration = 0;
  const resumeDrafts = new Map();

  const accountId = () => accountSelect.value.trim();

  const showFeedback = (element, message) => {
    element.textContent = message;
    element.hidden = !message;
  };

  const agentRequest = async (path, options = {}, expectedAccount = accountId()) => {
    if (!agentControlToken) throw new Error("먼저 운영 권한을 열어주세요.");
    if (!expectedAccount) throw new Error("운영 계정을 먼저 선택해주세요.");
    const url = new URL(path, window.location.origin);
    if (url.origin !== window.location.origin || !url.pathname.startsWith("/api/marketing-agent/")) {
      throw new Error("허용되지 않은 마케팅 에이전트 경로입니다.");
    }
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${agentControlToken}`);
    headers.set("X-Trace-Account-ID", expectedAccount);
    if (options.body) headers.set("Content-Type", "application/json");
    const response = await fetch(url, { ...options, headers, credentials: "same-origin" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(body.detail || `요청을 처리하지 못했습니다. (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return body;
  };

  const addText = (parent, tag, value, className = "") => {
    if (value == null || value === "") return;
    const node = document.createElement(tag);
    node.textContent = String(value);
    if (className) node.className = className;
    parent.append(node);
  };

  const compactValue = (value) => {
    if (typeof value === "string") return value;
    if (value == null) return "";
    return JSON.stringify(value, null, 2);
  };

  const appendPacketSection = (parent, title, value) => {
    if (value == null || value === "" || (Array.isArray(value) && value.length === 0)) return;
    const section = document.createElement("section");
    addText(section, "h4", title);
    const pre = document.createElement("pre");
    pre.textContent = compactValue(value);
    section.append(pre);
    parent.append(section);
  };

  const campaignState = (campaign) => campaign.state || campaign.campaign?.state || "상태 미상";

  const renderCampaigns = (campaigns) => {
    campaignList.replaceChildren();
    campaignEmpty.hidden = campaigns.length > 0;
    for (const campaign of campaigns) {
      const card = document.createElement("article");
      card.className = "agent-card";
      addText(card, "strong", campaign.business_outcome || campaign.campaign_id || "캠페인");
      addText(card, "span", campaignState(campaign), "mono");
      addText(card, "small", campaign.campaign_id);
      card.title = "상세 상태는 검수 항목에서 확인할 수 있습니다.";
      campaignList.append(card);
    }
  };

  const resumeDraftKey = (account, run) =>
    `${account}:${run.run_id}:${run.loop?.head_step_sha256 || "missing-head"}`;

  const canResumeCustomerContext = (run) =>
    run.state === "blocked"
    && run.loop?.state === "needs_input"
    && /^[a-f0-9]{64}$/.test(run.loop?.head_step_sha256 || "")
    && run.next_intent?.intent_id === "request_more_evidence"
    && run.next_intent?.requested_scope === "customer_intelligence";

  const appendResumeControl = (card, run, renderedAccount) => {
    if (!canResumeCustomerContext(run)) return;
    const draftKey = resumeDraftKey(renderedAccount, run);
    const draft = resumeDrafts.get(draftKey) || { resumeId: "", snapshotId: "" };
    resumeDrafts.set(draftKey, draft);
    const form = document.createElement("form");
    form.className = "agent-resume";
    form.dataset.agentResumeForm = "";
    const label = document.createElement("label");
    label.textContent = "검증된 marketing context snapshot ID";
    const input = document.createElement("input");
    input.name = "marketing-context-snapshot-id";
    input.autocomplete = "off";
    input.maxLength = 120;
    input.pattern = "[A-Za-z0-9][A-Za-z0-9._-]{0,119}";
    input.placeholder = "예: context-2026-09-03";
    input.required = true;
    input.value = draft.snapshotId;
    input.addEventListener("input", () => { draft.snapshotId = input.value; });
    label.append(input);
    const button = document.createElement("button");
    button.type = "submit";
    button.className = "button button-secondary";
    button.textContent = "이 snapshot으로 재개";
    const resumeFeedback = document.createElement("p");
    resumeFeedback.className = "candidate-feedback";
    resumeFeedback.dataset.agentResumeFeedback = "";
    resumeFeedback.role = "alert";
    resumeFeedback.hidden = true;
    form.append(label, button, resumeFeedback);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (renderedAccount !== accountId()) {
        showFeedback(resumeFeedback, "계정이 바뀌었습니다. 실행 목록을 다시 불러와주세요.");
        return;
      }
      const snapshotId = input.value.trim();
      if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$/.test(snapshotId)) {
        showFeedback(resumeFeedback, "검증된 marketing context snapshot ID를 입력해주세요.");
        input.focus();
        return;
      }
      draft.snapshotId = snapshotId;
      draft.resumeId ||= crypto.randomUUID();
      const requestBody = {
        schema_version: "trace.marketing-agent-resume-request.v1",
        resume_id: draft.resumeId,
        expected_head_step_sha256: run.loop.head_step_sha256,
        marketing_context_snapshot_id: snapshotId,
      };
      button.disabled = true;
      showFeedback(resumeFeedback, "검증된 customer context로 재개하는 중입니다.");
      try {
        const resumed = await agentRequest(
          `/api/marketing-agent/runs/${encodeURIComponent(run.run_id)}/resume`,
          { method: "POST", body: JSON.stringify(requestBody) },
          renderedAccount,
        );
        if (renderedAccount !== accountId()) return;
        resumeDrafts.delete(draftKey);
        showFeedback(resumeFeedback, `재개 접수됨: ${resumed.run_id} · ${resumed.state}`);
        await loadAgent();
      } catch (error) {
        if (renderedAccount === accountId()) showFeedback(resumeFeedback, error.message);
      } finally {
        button.disabled = false;
      }
    });
    card.append(form);
  };

  const journeyStateLabel = (node) => {
    const outcome = node.outcome || {};
    const labels = {
      activated: "후속 그림자 실험 생성됨",
      approved: "학습 승인됨",
      blocked: "후속 실험 생성 중단",
      candidate: "학습 후보 검수 대기",
      completed: "다음 실험안 준비됨",
      evaluated: "성과 평가 완료",
      failed: "다음 실험안 생성 실패",
      inconclusive: "성과 판단 유보",
      pending: "후속 실험 실행 준비",
      proposed: "성과 재평가 완료",
      queued: "다음 실험안 생성 중",
      rejected: "학습 후보 기각됨",
      stopped: "실험 중단됨",
      superseded: "새 판단으로 대체됨",
      unknown_side_effect: "외부 효과 확인 필요",
    };
    if (outcome.successor_activation_state) {
      return labels[outcome.successor_activation_state] || "후속 실험 상태 확인 필요";
    }
    if (outcome.next_experiment_state) {
      return labels[outcome.next_experiment_state] || "다음 실험 상태 확인 필요";
    }
    if (outcome.learning_state) {
      return labels[outcome.learning_state] || "학습 상태 확인 필요";
    }
    if (outcome.reassessment_state) {
      return labels[outcome.reassessment_state] || "성과 재평가 상태 확인 필요";
    }
    if (outcome.evaluation_state) {
      return labels[outcome.evaluation_state] || "성과 평가 상태 확인 필요";
    }
    if (node.state === "evaluated") return "계보 무결성 확인 필요";
    if (node.mode === "shadow") return "그림자 전략 · 외부 효과 없음";
    if (node.state === "published" || node.state === "observing") return "성과 관찰 중";
    return "실행·검수 준비 중";
  };

  const appendJourneyControl = (card, run, renderedAccount) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "button button-secondary";
    button.textContent = "성과 루프 보기";
    const output = document.createElement("section");
    output.className = "agent-run-journey";
    output.hidden = true;
    button.addEventListener("click", async () => {
      if (renderedAccount !== accountId()) return;
      button.disabled = true;
      output.hidden = false;
      output.replaceChildren();
      addText(output, "small", "성과 계보를 불러오는 중입니다.");
      try {
        const journey = await agentRequest(
          `/api/marketing-agent/runs/${encodeURIComponent(run.run_id)}/journey`,
          {},
          renderedAccount,
        );
        if (renderedAccount !== accountId()) return;
        output.replaceChildren();
        if (journey.integrity_state === "launch_pending") {
          addText(output, "strong", "제품 근거 조사·전략 준비 중");
          return;
        }
        if (journey.integrity_state !== "verified") {
          addText(output, "strong", "실행 계보 무결성 확인 필요");
          addText(output, "small", "루트 캠페인 또는 생성 기록을 확인해주세요.");
          return;
        }
        addText(
          output,
          "strong",
          `성과 루프 ${journey.nodes?.length || 0}단계${journey.truncated ? " · 일부만 표시" : ""}`,
        );
        for (const node of journey.nodes || []) {
          const row = document.createElement("div");
          row.className = "agent-journey-node";
          addText(row, "span", `${node.relation} · ${node.state}`, "mono");
          addText(row, "small", node.campaign_id);
          addText(row, "small", journeyStateLabel(node));
          output.append(row);
        }
      } catch (error) {
        if (renderedAccount !== accountId()) return;
        output.replaceChildren();
        addText(output, "small", error.message);
      } finally {
        button.disabled = false;
      }
    });
    card.append(button, output);
  };

  const renderRuns = (runs, renderedAccount) => {
    runList.replaceChildren();
    runEmpty.hidden = runs.length > 0;
    for (const run of runs) {
      const card = document.createElement("article");
      card.className = "agent-card";
      addText(card, "strong", run.run_id || "에이전트 실행");
      addText(card, "span", run.state || "상태 미상", "mono");
      addText(card, "small", run.failure_code || run.campaign_id || run.request_sha256);
      appendJourneyControl(card, run, renderedAccount);
      appendResumeControl(card, run, renderedAccount);
      runList.append(card);
    }
  };

  const safeApproval = (item, packet) => {
    const approval = packet?.approval;
    const action = approval?.action;
    if (!approval || !action || action.method !== "POST") throw new Error("승인 계약이 올바르지 않습니다.");
    if (typeof action.path !== "string" || !action.path.startsWith("/api/marketing-agent/")) {
      throw new Error("승인 경로가 올바르지 않습니다.");
    }
    if (!action.body || Array.isArray(action.body) || typeof action.body !== "object") {
      throw new Error("승인 본문이 올바르지 않습니다.");
    }
    if (
      !Array.isArray(action.allowed_decisions)
      || !action.allowed_decisions.every((decision) => decision === "approved" || decision === "rejected")
    ) throw new Error("승인 선택지가 올바르지 않습니다.");
    if (
      approval.target_kind !== item.target?.kind
      || approval.target_id !== item.target?.id
      || approval.target_sha256 !== item.target?.sha256
    ) throw new Error("검수 대상이 최신 대기열과 일치하지 않습니다.");
    return approval;
  };

  const openReview = async (item) => {
    const openedAccount = accountId();
    const generation = ++reviewGeneration;
    approveButton.disabled = true;
    rejectButton.disabled = true;
    showFeedback(reviewFeedback, "검수 근거를 불러오는 중입니다.");
    reviewDetail.hidden = false;
    try {
      const packet = await agentRequest(item.review_packet_path, {}, openedAccount);
      if (generation !== reviewGeneration || openedAccount !== accountId()) return;
      const approval = safeApproval(item, packet);
      selectedReview = { item, packet, approval, accountId: openedAccount };
      reviewContent.replaceChildren();
      addText(reviewContent, "p", `${item.review_kind} · ${item.campaign?.campaign_id}`, "eyebrow");
      addText(reviewContent, "h3", item.campaign?.business_outcome || "검수할 판단");
      addText(reviewContent, "p", `대상 ${item.target.kind} / ${item.target.id}`);
      addText(reviewContent, "p", `SHA-256 ${item.target.sha256}`, "mono");
      appendPacketSection(reviewContent, "근거와 주장 경계", packet.evidence);
      appendPacketSection(reviewContent, "검증된 원본", packet.source);
      appendPacketSection(reviewContent, "전략", packet.strategy);
      appendPacketSection(reviewContent, "크리에이티브", packet.creative);
      appendPacketSection(reviewContent, "관찰 결과", packet.outcomes);
      appendPacketSection(reviewContent, "다음 실험", packet.draft);
      appendPacketSection(reviewContent, "학습", packet.learning);
      appendPacketSection(reviewContent, "승인 시 영향", packet.effect);
      appendPacketSection(reviewContent, "제약", packet.limitations);
      approveButton.disabled = !approval.action.allowed_decisions?.includes("approved");
      rejectButton.disabled = !approval.action.allowed_decisions?.includes("rejected");
      showFeedback(reviewFeedback, "");
      reviewDetail.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (error) {
      if (generation !== reviewGeneration || openedAccount !== accountId()) return;
      selectedReview = null;
      reviewContent.replaceChildren();
      showFeedback(reviewFeedback, error.message);
    }
  };

  const renderReviews = (items) => {
    reviewList.replaceChildren();
    reviewEmpty.hidden = items.length > 0;
    for (const item of items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "agent-card agent-card--button";
      addText(button, "strong", item.campaign?.business_outcome || item.campaign?.campaign_id);
      addText(button, "span", `${item.review_kind} 검수`, "mono");
      addText(button, "small", item.target?.id);
      button.addEventListener("click", () => openReview(item));
      reviewList.append(button);
    }
  };

  const loadAgent = async () => {
    const requestedAccount = accountId();
    const generation = ++loadGeneration;
    state.textContent = "불러오는 중";
    try {
      const [runBody, campaignBody, reviewBody] = await Promise.all([
        agentRequest("/api/marketing-agent/runs", {}, requestedAccount),
        agentRequest("/api/marketing-agent/campaigns", {}, requestedAccount),
        agentRequest("/api/marketing-agent/review-queue", {}, requestedAccount),
      ]);
      if (generation !== loadGeneration || requestedAccount !== accountId()) return;
      renderRuns(Array.isArray(runBody.runs) ? runBody.runs : [], requestedAccount);
      renderCampaigns(Array.isArray(campaignBody.campaigns) ? campaignBody.campaigns : []);
      renderReviews(Array.isArray(reviewBody.items) ? reviewBody.items : []);
      state.textContent = `검수 ${reviewBody.items?.length || 0}건`;
      showFeedback(feedback, "");
    } catch (error) {
      if (generation !== loadGeneration) return;
      state.textContent = "확인 필요";
      showFeedback(feedback, error.message);
      if (error.status === 401) lockAgent("운영 토큰을 다시 확인해주세요.");
    }
  };

  const lockAgent = (message = "") => {
    agentControlToken = "";
    selectedReview = null;
    resumeDrafts.clear();
    loadGeneration += 1;
    reviewGeneration += 1;
    window.clearInterval(pollTimer);
    pollTimer = null;
    locked.hidden = false;
    panel.hidden = true;
    reviewDetail.hidden = true;
    approveButton.disabled = true;
    rejectButton.disabled = true;
    state.textContent = "잠김";
    unlockForm.reset();
    showFeedback(feedback, message);
  };

  const schedulePolling = () => {
    window.clearInterval(pollTimer);
    pollTimer = window.setInterval(() => {
      if (root.open && agentControlToken) loadAgent();
    }, 20_000);
  };

  const updateStartHandoff = () => {
    const selected = accountSelect.selectedOptions[0];
    const selectedAccount = accountId() || "<account-id>";
    const accountName = selected?.textContent?.trim() || selectedAccount;
    const feature = featureInput.value.trim() || "<홍보할 새 기능>";
    const outcome = outcomeInput.value.trim() || "<검증할 사업 결과>";
    const control = controlInput.value.trim() || "<현재 성과 기준 포맷>";
    const productSource = productSourceInput.value.trim() || "<제품 저장소 URL 또는 로컬 절대 경로>";
    const productRef = productRefInput.value.trim() || "<제품 branch, PR 또는 commit SHA>";
    accountNote.textContent = `현재 hosted 계정: ${accountName} (${selectedAccount})`;
    startPrompt.textContent = [
      "ads-booster의 Trace 마케팅 에이전트로 다음 제품 기능의 shadow 마케팅 실험을 시작해줘.",
      `- hosted workspace: ${window.location.origin}`,
      `- hosted account_id: ${selectedAccount}`,
      `- 제품 소스: ${productSource}`,
      `- 제품 ref: ${productRef}`,
      `- 기능: ${feature}`,
      `- 사업 결과: ${outcome}`,
      `- 현재 비교 기준: ${control}`,
      "fresh-installed trace-marketing 환경을 기준으로 제품 근거를 확인하고, 최신 시장 근거를 조사할 immutable trace.feature-launch-run-request.v1 JSON을 준비해줘.",
      "실행은 하지 말고 JSON만 반환해. 그 JSON은 hosted 마케팅 에이전트 실행 접수 화면에 붙여 넣을 거야.",
      "제품 소스와 정확한 ref를 읽고 digest로 고정할 수 없으면 제품 근거를 추정하거나 hash를 만들지 말고 중단해.",
      "사람의 승인 전에는 candidate 생성, Appium 실행, Threads 게시 또는 외부 side effect를 하지 마.",
    ].join("\n");
    launchCommand.textContent = `TRACE_MARKETING_CONTROL_TOKEN=… trace-marketing agent launch --input launch.json --url ${window.location.origin} --home /private/path/to/state --model <model>`;
  };

  const submitRun = async (event) => {
    event.preventDefault();
    const submittedAccount = accountId();
    let requestBody;
    try {
      requestBody = JSON.parse(runInput.value);
    } catch {
      showFeedback(runFeedback, "실행 요청이 올바른 JSON이 아닙니다.");
      return;
    }
    if (requestBody?.research?.account_id !== submittedAccount) {
      showFeedback(runFeedback, "실행 요청의 account_id가 현재 선택 계정과 다릅니다.");
      return;
    }
    const submitButton = runForm.querySelector("button[type='submit']");
    submitButton.disabled = true;
    showFeedback(runFeedback, "에이전트 실행을 접수하는 중입니다.");
    try {
      const result = await agentRequest(
        "/api/marketing-agent/runs",
        { method: "POST", body: JSON.stringify(requestBody) },
        submittedAccount,
      );
      if (submittedAccount !== accountId()) return;
      showFeedback(runFeedback, `접수됨: ${result.run_id} · ${result.state}`);
      await loadAgent();
    } catch (error) {
      if (submittedAccount === accountId()) showFeedback(runFeedback, error.message);
    } finally {
      submitButton.disabled = false;
    }
  };

  const decideReview = async (decision) => {
    const reviewer = reviewerId.value.trim();
    if (!selectedReview || selectedReview.accountId !== accountId()) {
      showFeedback(reviewFeedback, "계정 또는 검수 대상이 바뀌었습니다. 다시 열어주세요.");
      return;
    }
    if (!reviewer) {
      showFeedback(reviewFeedback, "검수자 ID를 입력해주세요.");
      reviewerId.focus();
      return;
    }
    const review = selectedReview;
    const decisionGeneration = reviewGeneration;
    const { approval } = review;
    const action = approval.action;
    if (!action.allowed_decisions.includes(decision)) return;
    const body = { ...approval.action.body, reviewer_id: reviewer, decision };
    approveButton.disabled = true;
    rejectButton.disabled = true;
    try {
      await agentRequest(action.path, { method: "POST", body: JSON.stringify(body) }, review.accountId);
      if (decisionGeneration !== reviewGeneration || review.accountId !== accountId()) {
        await loadAgent();
        return;
      }
      selectedReview = null;
      reviewDetail.hidden = true;
      await loadAgent();
    } catch (error) {
      if (decisionGeneration !== reviewGeneration || review.accountId !== accountId()) {
        await loadAgent();
        return;
      }
      showFeedback(
        reviewFeedback,
        error.status === 409 ? "다른 검수가 먼저 반영되었습니다. 최신 상태를 다시 불러옵니다." : error.message,
      );
      await loadAgent();
    }
  };

  unlockForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    agentControlToken = new FormData(unlockForm).get("control-token")?.toString().trim() || "";
    if (!agentControlToken) return;
    unlockForm.reset();
    locked.hidden = true;
    panel.hidden = false;
    await loadAgent();
    if (agentControlToken) schedulePolling();
  });
  root.querySelector("[data-agent-refresh]").addEventListener("click", loadAgent);
  root.querySelector("[data-agent-lock]").addEventListener("click", () => lockAgent());
  root.querySelector("[data-agent-copy-prompt]").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(startPrompt.textContent);
      state.textContent = "요청문 복사됨";
      showFeedback(feedback, "");
    } catch {
      showFeedback(feedback, "자동 복사에 실패했습니다. 화면의 요청문을 직접 선택해 복사해주세요.");
    }
  });
  runForm.addEventListener("submit", submitRun);
  approveButton.addEventListener("click", () => decideReview("approved"));
  rejectButton.addEventListener("click", () => decideReview("rejected"));
  accountSelect.addEventListener("change", () => {
    updateStartHandoff();
    if (agentControlToken) lockAgent("계정이 바뀌어 검수 권한을 잠갔습니다.");
  });
  for (const input of [productSourceInput, productRefInput, featureInput, outcomeInput, controlInput]) {
    input.addEventListener("input", updateStartHandoff);
  }
  root.addEventListener("toggle", () => {
    if (!root.open && agentControlToken) lockAgent();
    if (root.open) updateStartHandoff();
  });
  window.addEventListener("beforeunload", () => { agentControlToken = ""; });

  approveButton.disabled = true;
  rejectButton.disabled = true;
  updateStartHandoff();
})();
