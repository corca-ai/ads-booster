(() => {
  const one = (selector, root = document) => root.querySelector(selector); const all = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const notice = one("[data-notice]"); const memberForm = one("[data-member-access]");
  const memberFields = one("[data-member-access-form]"); const memberConnected = one("[data-member-connected]");
  const memberFeedback = one("[data-member-feedback]"); const memberLabel = one("[data-member-state-label]");
  const memberName = one("[data-member-name]"); const chatThread = one("[data-chat-thread]");
  const chatEmpty = one("[data-chat-empty]");
  const chatForm = one("[data-chat-form]");
  const chatInput = one("[data-chat-input]", chatForm) ?? one("textarea", chatForm);
  const chatCommandOutput = one("[data-chat-command-output]");
  const chatCommandEvents = one("[data-chat-command-events]");
  const chatCommandOptions = one("[data-chat-command-options]");
  const chatCommandSuggestions = one("[data-chat-command-suggestions]");
  const chatApproval = one("[data-chat-approval]");
  const chatApprovalAction = one("[data-chat-approval-action]");
  const chatApprovalDetail = one("[data-chat-approval-detail]");
  const chatModel = one("[data-chat-model]");
  const chatPermission = one("[data-chat-permission]");
  const workspaceNames = all("[data-workspace-name]");
  const workspaceChatName = one("[data-workspace-chat-name]");
  const captureDialog = one("[data-capture-dialog]"); const workspaceLive = one("[data-workspace-live]");
  const inviteDialog = one("[data-invite-dialog]"); const inviteButton = one("[data-action='open-invite']");
  const inviteForm = one("[data-invite-form]"); const inviteName = one("[data-invite-name]");
  const inviteFeedback = one("[data-invite-feedback]"); const inviteResult = one("[data-invite-result]");
  const inviteToken = one("[data-invite-token]"); const inviteCopy = one("[data-invite-copy]");
  const entryScreen = one("[data-entry-screen]");
  const accessTokenField = one("#workspace-access-id");
  const skipLink = one(".skip-link");
  const queueEmpty = one("[data-queue-empty]");
  const queueSummary = one("[data-queue-summary]");
  const captureFeedback = one("[data-capture-feedback]");
  const captureSubmit = one("[data-capture-submit]");
  let activeChatSessionId = null;
  let contextRecords = [];
  let assetRecords = [];
  let chatCommandCatalog = [];
  let approvalPollTimer = null;
  let approvalPollBusy = false;

  const ERROR_MESSAGES = Object.freeze({
    "authentication required": "로그인이 필요합니다.",
    "invalid credentials": "워크스페이스 또는 멤버 정보가 올바르지 않습니다.",
    "model provider authentication is required": "에이전트 인증이 필요합니다.",
    "model provider authentication is unavailable": "에이전트 인증을 확인할 수 없습니다.",
    "private session not found": "개인 세션을 찾을 수 없습니다.",
    "private session changed during this request": "개인 세션이 변경되었습니다. 다시 시도해 주세요.",
    "generation request already exists with different input": "같은 요청 ID에 다른 입력이 있습니다.",
    "idempotency key conflict": "이미 다른 내용으로 등록된 요청 키입니다.",
    "queue record not found": "생성 요청을 찾을 수 없습니다.",
    "queue revision conflict": "다른 사람이 먼저 처리했습니다. 화면을 새로고침해 주세요.",
    "campaign context JSON is invalid": "선택한 페르소나 또는 홍보 소재의 JSON 형식을 확인해 주세요.",
    "campaign input not found": "선택한 캠페인 자료를 찾을 수 없습니다.",
    "reference image is unavailable": "선택한 레퍼런스 이미지 파일을 찾을 수 없습니다.",
    "reference image provenance does not match": "레퍼런스 이미지가 등록 이후 변경되었습니다.",
    "campaign revision conflict": "캠페인 상태가 변경되었습니다. 새로고침해 주세요.",
  });
  const CONTEXT_KIND_LABELS = Object.freeze({
    persona: "페르소나",
    promotion: "프로모션",
    reference: "레퍼런스",
    rule: "규칙",
  });
  const QUEUE_STATE_LABELS = Object.freeze({
    submitted: "제출됨",
    claimed: "할당됨",
    running: "실행 중",
    review: "검수 대기",
    accepted: "승인됨",
    rejected: "거절됨",
    failed: "실패",
  });
  const ACCESS_ID_SEPARATOR = "%";
  const ACCESS_ID_PREFIX = "Workspace access ID (shown once; not written to logs):";

  const localizeError = (message) => ERROR_MESSAGES[message] ?? "요청에 실패했습니다. 잠시 후 다시 시도해 주세요.";
  const contextKindLabel = (kind) => CONTEXT_KIND_LABELS[kind] ?? kind;
  const queueStateLabel = (state) => QUEUE_STATE_LABELS[state] ?? state;
  const parseAccessId = (value) => {
    const normalized = value.trim();
    const token = normalized.startsWith(ACCESS_ID_PREFIX)
      ? normalized.slice(ACCESS_ID_PREFIX.length).trim()
      : normalized;
    const parts = token.split(ACCESS_ID_SEPARATOR).map((part) => part.trim());
    if (parts.some((part) => !part)) return null;
    if (parts.length === 4) {
      const [workspaceId, memberId, workspaceCode, memberCode] = parts;
      return {
        path: "/api/auth/login",
        credentials: {
          workspace_id: workspaceId,
          member_id: memberId,
          workspace_code: workspaceCode,
          member_code: memberCode,
        },
      };
    }
    if (parts.length === 3) {
      const [workspaceId, memberId, memberCode] = parts;
      return {
        path: "/api/auth/member-login",
        credentials: {
          workspace_id: workspaceId,
          member_id: memberId,
          member_code: memberCode,
        },
      };
    }
    return null;
  };

  const setNotice = (message) => {
    if (notice) notice.textContent = message;
  };

  const setCaptureFeedback = (message) => {
    if (!captureFeedback) return;
    captureFeedback.hidden = !message;
    captureFeedback.textContent = message;
  };

  const setInviteFeedback = (message) => {
    if (!inviteFeedback) return;
    inviteFeedback.hidden = !message;
    inviteFeedback.textContent = message;
  };

  const clearInviteResult = () => {
    if (inviteResult) inviteResult.hidden = true;
    if (inviteToken) inviteToken.textContent = "";
    setInviteFeedback("");
  };

  const clearCaptureValidation = () => {
    captureSubmit?.removeAttribute("disabled");
    one("[data-bundle-json]")?.removeAttribute("aria-invalid");
    one("[data-persona-select]")?.removeAttribute("aria-invalid");
    one("[data-promotion-select]")?.removeAttribute("aria-invalid");
    setCaptureFeedback("");
  };

  const setBusy = (element, busy, message = null) => {
    if (!element) return;
    element.setAttribute("aria-busy", String(busy));
    if (busy && message) setNotice(message);
  };

  const request = async (path, options = {}) => {
    const response = await fetch(path, { credentials: "same-origin", ...options });
    const payload = response.status === 204 ? null : await response.json();
    if (!response.ok) {
      const validationError = Array.isArray(payload?.detail);
      const detail = validationError
        ? "워크스페이스 접속 ID 형식을 확인해 주세요."
        : typeof payload?.detail === "string"
          ? payload.detail
          : payload?.detail?.message;
      const message = validationError
        ? detail
        : localizeError(detail || `요청에 실패했습니다 (${response.status})`);
      throw new Error(message);
    }
    return payload;
  };

  const markAuthenticated = (member) => {
    if (entryScreen) entryScreen.hidden = true;
    if (workspaceLive) workspaceLive.hidden = false;
    if (skipLink) skipLink.setAttribute("href", "#workspace-content");
    if (memberFields) memberFields.hidden = true;
    if (memberConnected) memberConnected.hidden = false;
    if (memberLabel) memberLabel.textContent = "로컬 연결됨";
    if (memberName) memberName.textContent = member.display_name;
    workspaceNames.forEach((element) => { element.textContent = member.workspace_name; });
    if (workspaceChatName) workspaceChatName.textContent = `${member.workspace_name} 팀`;
    if (inviteButton) inviteButton.hidden = member.is_admin !== true;
  };

  const markSignedOut = () => {
    if (entryScreen) entryScreen.hidden = false;
    if (workspaceLive) workspaceLive.hidden = true;
    if (skipLink) skipLink.setAttribute("href", "#entry-title");
    if (memberFields) memberFields.hidden = false;
    if (memberConnected) memberConnected.hidden = true;
    if (memberLabel) memberLabel.textContent = "입장 전";
    if (memberName) memberName.textContent = "워크스페이스에 입장";
    workspaceNames.forEach((element) => { element.textContent = "워크스페이스"; });
    if (workspaceChatName) workspaceChatName.textContent = "개인 채팅";
    if (inviteButton) inviteButton.hidden = true;
    inviteDialog?.close();
    clearInviteResult();
    activeChatSessionId = null;
    chatCommandCatalog = [];
    clearCommandOutput();
    stopApprovalPolling();
  };

  const contextNode = (record) => {
    const item = document.createElement("article"); item.className = "context-source";
    const mark = document.createElement("span"); mark.className = "signal-mark success";
    mark.setAttribute("aria-hidden", "true");
    const content = document.createElement("span"); content.className = "context-source__content";
    const title = document.createElement("strong"); title.textContent = record.title;
    const meta = document.createElement("span"); meta.className = "context-source__meta";
    meta.textContent = `${contextKindLabel(record.kind)} · 리비전 ${record.revision}`;
    content.append(title, meta);
    item.append(mark, content);
    return item;
  };

  const loadContexts = async () => {
    const records = await request("/api/contexts");
    contextRecords = records;
    const main = one("[data-context-list]");
    const rail = one("[data-context-rail-list]");
    main?.replaceChildren(...records.map(contextNode));
    rail?.replaceChildren(...records.map(contextNode));
    const count = one("[data-context-count]");
    if (count) count.textContent = `공유 자료 ${records.length}개`;
    populateContextSelect("[data-persona-select]", "persona", "페르소나 선택");
    populateContextSelect("[data-promotion-select]", "promotion", "홍보 소재 선택");
  };

  const populateContextSelect = (selector, kind, placeholder) => {
    const select = one(selector);
    if (!select) return;
    const options = contextRecords.filter((record) => record.kind === kind).map((record) => {
      const option = document.createElement("option");
      option.value = record.context_id;
      option.textContent = record.title;
      return option;
    });
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = options.length ? placeholder : `${contextKindLabel(kind)} 자료가 없습니다`;
    select.replaceChildren(empty, ...options);
  };

  const loadAssets = async () => {
    assetRecords = await request("/api/assets");
    const select = one("[data-reference-select]");
    if (!select) return;
    const options = assetRecords.map((record) => {
      const option = document.createElement("option");
      option.value = record.asset_id;
      option.textContent = record.filename;
      return option;
    });
    select.replaceChildren(...options);
  };

  const campaignStateLabel = (state) => ({
    active: "계속 생성 중",
    stopped: "중지됨",
    completed: "완료됨",
  })[state] ?? state;

  const campaignNode = (record) => {
    const row = document.createElement("article"); row.className = "campaign-row";
    const mark = document.createElement("span");
    mark.className = `signal-mark ${record.state === "active" ? "success" : ""}`;
    mark.setAttribute("aria-hidden", "true");
    const content = document.createElement("span"); content.className = "queue-row__content";
    const title = document.createElement("strong"); title.textContent = record.name;
    const mode = record.variation_count === null ? "계속" : `${record.variation_count}개`;
    const meta = document.createElement("span"); meta.className = "queue-row__meta";
    meta.textContent = `${campaignStateLabel(record.state)} · ${mode} · 다음 변형 ${record.next_variation + 1}`;
    content.append(title, meta);
    const action = document.createElement("span");
    if (record.state === "active") {
      const button = document.createElement("button");
      button.className = "button button-secondary";
      button.type = "button";
      button.textContent = "생성 중지";
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await request(`/api/campaigns/${encodeURIComponent(record.campaign_id)}/stop`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ expected_revision: record.revision }),
          });
          await loadCampaigns();
          setNotice("캠페인의 다음 생성을 중지했습니다.");
        } catch (error) { setNotice(error.message); }
        finally { button.disabled = false; }
      });
      action.append(button);
    }
    row.append(mark, content, action);
    return row;
  };

  const loadCampaigns = async () => {
    const records = await request("/api/campaigns");
    one("[data-campaign-list]")?.replaceChildren(...records.map(campaignNode));
  };

  const stateClass = (state) => {
    if (["accepted", "review", "submitted"].includes(state)) return "success";
    if (["claimed", "running"].includes(state)) return "warning";
    return "danger";
  };

  const queueNode = (record) => {
    const row = document.createElement("article"); row.className = "queue-row";
    const mark = document.createElement("span"); mark.className = `signal-mark ${stateClass(record.state)}`;
    mark.setAttribute("aria-hidden", "true");
    const content = document.createElement("span"); content.className = "queue-row__content";
    const title = document.createElement("strong"); title.textContent = record.bundle.promotion_material.concept;
    const meta = document.createElement("span"); meta.className = "queue-row__meta";
    meta.textContent = `${record.bundle.persona.persona_id} · 시도 ${record.attempts}/${record.max_attempts}`;
    const trailing = document.createElement("span"); trailing.className = "queue-row__trailing";
    const state = document.createElement("span"); state.className = `queue-state ${record.state === "review" ? "review" : "ready"}`;
    state.textContent = queueStateLabel(record.state);
    const revision = document.createElement("span"); revision.className = "mono";
    revision.textContent = `r${record.revision}`;
    content.append(title, meta);
    trailing.append(state, revision);
    row.append(mark, content, trailing);
    return row;
  };

  const reviewNode = (record) => {
    const tile = document.createElement("article"); tile.className = "review-tile";
    const body = document.createElement("span"); body.className = "review-tile__body";
    const title = document.createElement("h3"); title.textContent = record.bundle.promotion_material.concept;
    const meta = document.createElement("span"); meta.className = "review-tile__meta";
    const artifact = document.createElement("span"); artifact.textContent = record.artifact_path || "결과 파일 준비 중";
    const actions = document.createElement("span");
    [true, false].forEach((accepted) => {
      const button = document.createElement("button");
      button.className = `button ${accepted ? "button-primary" : "button-secondary"}`;
      button.type = "button";
      button.textContent = accepted ? "승인" : "거절";
      button.addEventListener("click", async () => {
        setBusy(button, true, "검수 결과를 저장하는 중…");
        button.disabled = true;
        try {
          await request(`/api/queue/${encodeURIComponent(record.queue_id)}/review`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ accepted, expected_revision: record.revision }),
          });
          setNotice(`검수가 ${accepted ? "승인" : "거절"}되었습니다.`);
          await loadQueue();
        } catch (error) {
          setNotice(error.message);
        } finally {
          button.disabled = false;
          setBusy(button, false);
        }
      });
      actions.append(button);
    });
    meta.append(artifact, actions);
    body.append(title, meta);
    tile.append(body);
    return tile;
  };

  const loadQueue = async () => {
    const feedback = one("[data-queue-feedback]");
    try {
      const records = await request("/api/queue");
      const queueList = one("[data-queue-list]");
      if (records.length) {
        queueList?.replaceChildren(...records.map(queueNode));
        if (queueEmpty) queueEmpty.hidden = true;
        if (queueSummary) queueSummary.hidden = true;
      } else {
        queueList?.replaceChildren();
        if (queueEmpty) queueEmpty.hidden = false;
        if (queueSummary) queueSummary.hidden = false;
      }
      const reviews = records.filter((record) => record.state === "review");
      one("[data-review-gallery]")?.replaceChildren(...reviews.map(reviewNode));
      const empty = one("[data-review-empty]");
      if (empty) empty.hidden = reviews.length > 0;
      const queueCount = one("[data-queue-count]");
      const reviewCount = one("[data-review-count]");
      if (queueCount) queueCount.textContent = String(records.length);
      if (reviewCount) reviewCount.textContent = String(reviews.length);
      if (feedback) feedback.hidden = true;
    } catch (error) {
      const message = one("[data-queue-feedback-text]");
      if (message) message.textContent = error.message;
      if (feedback) feedback.hidden = false;
      throw error;
    }
  };

  const appendChat = (role, text) => {
    if (!chatThread) return;
    if (chatEmpty?.parentElement === chatThread) chatEmpty.remove();
    const message = document.createElement("article");
    message.className = `thread-message${role === "assistant" ? " agent" : ""}`;
    const avatar = document.createElement("span");
    avatar.className = "thread-avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = role === "assistant" ? "TA" : "YO";
    const content = document.createElement("div");
    content.className = "thread-content";
    const meta = document.createElement("div");
    meta.className = "thread-meta";
    meta.textContent = role === "assistant" ? "Trace 에이전트 · 지금" : "나 · 지금";
    const body = document.createElement("p");
    body.className = "thread-body";
    body.textContent = text;
    content.append(meta, body);
    message.append(avatar, content);
    chatThread.append(message);
  };

  const renderHistory = (history) => {
    if (!chatThread) return;
    const messages = history.filter(
      (item) => ["user", "assistant"].includes(item.role) && typeof item.content === "string",
    );
    chatThread.replaceChildren();
    if (!messages.length && chatEmpty) chatThread.append(chatEmpty);
    messages.forEach((item) => appendChat(item.role, item.content));
  };

  const permissionLabel = (mode) => mode === "ask" ? "매번 확인" : "자동 허용";

  const renderChatSettings = (settings) => {
    if (!settings) return;
    if (chatModel) chatModel.textContent = `모델: ${settings.model}`;
    if (chatPermission) chatPermission.textContent = `승인: ${permissionLabel(settings.permission_mode)}`;
  };

  const clearCommandOutput = () => {
    if (!chatCommandOutput) return;
    chatCommandOutput.hidden = true;
    chatCommandEvents?.replaceChildren();
    chatCommandOptions?.replaceChildren();
  };

  const commandOption = (label, meta, prompt) => {
    const button = document.createElement("button");
    button.className = "chat-command-option";
    button.type = "button";
    const title = document.createElement("strong");
    title.textContent = label;
    const detail = document.createElement("span");
    detail.className = "chat-command-option__meta mono";
    detail.textContent = meta;
    button.append(title, detail);
    button.addEventListener("click", () => {
      if (chatInput) chatInput.value = prompt;
      void submitChatPrompt(prompt);
    });
    return button;
  };

  const renderCommandResult = (result) => {
    if (result.replace_history) {
      activeChatSessionId = result.session_id;
      renderHistory(result.history);
    }
    renderChatSettings(result.settings);
    const hasOutput = result.events.length || result.sessions.length || result.models.length;
    if (!hasOutput) {
      clearCommandOutput();
      return;
    }
    if (chatCommandOutput) chatCommandOutput.hidden = false;
    chatCommandEvents?.replaceChildren();
    result.events.forEach((event) => {
      const line = document.createElement("p");
      line.dataset.commandRole = event.role;
      line.textContent = event.content;
      chatCommandEvents?.append(line);
    });
    chatCommandOptions?.replaceChildren();
    result.sessions.forEach((session) => {
      chatCommandOptions?.append(
        commandOption(session.title, `${session.session_id} · ${session.revision}`, `/session ${session.session_id}`),
      );
    });
    result.models.forEach((model) => {
      chatCommandOptions?.append(commandOption(model.display_name, model.slug, `/model ${model.slug}`));
    });
  };

  const renderApproval = (pending) => {
    if (!chatApproval) return;
    chatApproval.hidden = pending === null;
    if (pending === null) return;
    chatApproval.dataset.requestId = pending.request_id;
    if (chatApprovalAction) chatApprovalAction.textContent = pending.action;
    if (chatApprovalDetail) chatApprovalDetail.textContent = pending.detail;
  };

  const pollApproval = async () => {
    if (approvalPollBusy) return;
    approvalPollBusy = true;
    try {
      renderApproval(await request("/api/chat/approval"));
    } catch (error) {
      if (error instanceof Error) setNotice(error.message);
      else throw error;
    } finally {
      approvalPollBusy = false;
    }
  };

  const startApprovalPolling = () => {
    if (approvalPollTimer !== null) return;
    approvalPollTimer = setInterval(() => { void pollApproval(); }, 250);
  };

  const stopApprovalPolling = () => {
    if (approvalPollTimer === null) return;
    clearInterval(approvalPollTimer);
    approvalPollTimer = null;
    renderApproval(null);
  };

  const updateCommandSuggestions = () => {
    if (!chatCommandSuggestions || !chatInput) return;
    const value = chatInput.value.trimStart();
    if (!value.startsWith("/")) {
      chatCommandSuggestions.hidden = true;
      chatCommandSuggestions.replaceChildren();
      return;
    }
    const matches = chatCommandCatalog.filter((item) => item.command.startsWith(value));
    chatCommandSuggestions.hidden = matches.length === 0;
    chatCommandSuggestions.replaceChildren(...matches.map((item) => {
      const button = document.createElement("button");
      button.className = "chat-command-suggestion";
      button.type = "button";
      button.setAttribute("role", "option");
      const command = document.createElement("strong");
      command.textContent = item.command;
      const description = document.createElement("span");
      description.className = "chat-command-suggestion__meta";
      description.textContent = item.description;
      button.append(command, description);
      button.addEventListener("click", () => {
        chatInput.value = item.command === "/model" ? "/model " : item.command;
        chatInput.focus();
        updateCommandSuggestions();
      });
      return button;
    }));
  };

  const loadChatCommands = async () => {
    chatCommandCatalog = await request("/api/chat/commands");
  };

  const loadChat = async () => {
    const sessions = await request("/api/sessions");
    if (!sessions.length) {
      renderHistory([]);
      return;
    }
    activeChatSessionId = sessions[0].session_id;
    const session = await request(`/api/sessions/${encodeURIComponent(activeChatSessionId)}`);
    renderHistory(session.history);
  };

  const refreshWorkspace = async () => {
    setBusy(workspaceLive, true, "워크스페이스를 새로고침하는 중…");
    try {
      const results = await Promise.allSettled([
        loadContexts(),
        loadAssets(),
        loadCampaigns(),
        loadQueue(),
        loadChat(),
        loadChatCommands(),
      ]);
      const failure = results.find((result) => result.status === "rejected");
      if (failure) throw failure.reason;
    } finally {
      setBusy(workspaceLive, false);
    }
  };

  memberForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const target = event.currentTarget;
    const form = new FormData(target);
    setBusy(target, true, "워크스페이스에 연결하는 중…");
    accessTokenField?.removeAttribute("aria-invalid");
    const accessId = String(form.get("access-token") ?? "").trim();
    if (!accessId) {
      accessTokenField?.setAttribute("aria-invalid", "true");
      if (memberFeedback) {
        memberFeedback.hidden = false;
        memberFeedback.textContent = "워크스페이스 접속 ID 값을 입력해 주세요.";
      }
      accessTokenField?.focus();
      setBusy(target, false);
      return;
    }
    const parsedAccessId = parseAccessId(accessId);
    if (!parsedAccessId) {
      accessTokenField?.setAttribute("aria-invalid", "true");
      if (memberFeedback) {
        memberFeedback.hidden = false;
        memberFeedback.textContent = "워크스페이스 접속 ID 형식을 확인해 주세요.";
      }
      accessTokenField?.focus();
      setBusy(target, false);
      return;
    }
    target.reset();
    try {
      const member = await request(parsedAccessId.path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(parsedAccessId.credentials),
      });
      markAuthenticated(member);
      await refreshWorkspace();
      setNotice("워크스페이스에 연결되었습니다.");
    } catch (error) {
      if (memberFeedback) { memberFeedback.hidden = false; memberFeedback.textContent = error.message; }
    } finally {
      setBusy(target, false);
    }
  });

  one("[data-member-reset]")?.addEventListener("click", async () => {
    setBusy(memberForm, true, "워크스페이스 연결을 끊는 중…");
    try {
      await request("/api/auth/logout", { method: "POST" });
      markSignedOut();
      setNotice("로그아웃했습니다.");
    } catch (error) { setNotice(error.message); }
    finally { setBusy(memberForm, false); }
  });

  const openInviteDialog = () => {
    clearInviteResult();
    inviteForm?.reset();
    inviteDialog?.showModal();
    inviteName?.focus();
  };

  inviteButton?.addEventListener("click", openInviteDialog);

  inviteForm?.addEventListener("submit", async (event) => {
    if (event.submitter?.value === "cancel") return;
    event.preventDefault();
    const target = event.currentTarget;
    const form = new FormData(target);
    const displayName = String(form.get("display-name") ?? "").trim();
    setBusy(target, true, "팀원 초대 ID를 만드는 중…");
    inviteName?.removeAttribute("aria-invalid");
    setInviteFeedback("");
    if (!displayName) {
      inviteName?.setAttribute("aria-invalid", "true");
      setInviteFeedback("팀원 이름을 입력해 주세요.");
      inviteName?.focus();
      setBusy(target, false);
      return;
    }
    try {
      const result = await request("/api/members/invite", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: displayName }),
      });
      if (inviteToken) inviteToken.textContent = result.member_access_id;
      if (inviteResult) inviteResult.hidden = false;
      target.reset();
      setNotice("팀원 초대 ID를 만들었습니다. 생성 직후 한 번 표시됩니다.");
    } catch (error) {
      setInviteFeedback(error.message);
      setNotice(error.message);
    } finally { setBusy(target, false); }
  });

  inviteCopy?.addEventListener("click", async () => {
    const token = inviteToken?.textContent?.trim();
    if (!token) return;
    try {
      if (typeof navigator === "undefined" || !navigator.clipboard) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(token);
      setNotice("팀원 접속 ID를 복사했습니다.");
    } catch (error) {
      if (error instanceof Error) setNotice("접속 ID를 직접 선택해 복사해 주세요.");
      else throw error;
    }
  });

  inviteDialog?.querySelector("[value='cancel']")?.addEventListener("click", () => inviteDialog?.close());

  one("[data-context-form]")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const target = event.currentTarget;
    const form = new FormData(target);
    setBusy(target, true, "공유 정보를 저장하는 중…");
    try {
      await request("/api/contexts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(form)) });
      target.reset();
      await loadContexts();
      setNotice("자료를 추가했습니다.");
    } catch (error) { setNotice(error.message); }
    finally { setBusy(target, false); }
  });

  one("[data-asset-upload]")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const target = event.currentTarget;
    const input = one("input[type='file']", target);
    const file = input?.files?.[0];
    if (!file) { setNotice("레퍼런스 이미지 파일을 선택해 주세요."); return; }
    setBusy(target, true, "레퍼런스 이미지를 저장하는 중…");
    try {
      const contentBase64 = await fileToBase64(file);
      await request("/api/assets/upload", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: file.name,
          media_type: file.type,
          content_base64: contentBase64,
          context_id: null,
        }),
      });
      target.reset();
      await loadAssets();
      setNotice("레퍼런스 이미지를 저장했습니다.");
    } catch (error) { setNotice(error.message); }
    finally { setBusy(target, false); }
  });

  const fileToBase64 = (file) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      const result = String(reader.result ?? "");
      resolve(result.includes(",") ? result.split(",", 2)[1] : "");
    });
    reader.addEventListener("error", () => reject(new Error("레퍼런스 이미지를 읽지 못했습니다.")));
    reader.readAsDataURL(file);
  });

  one("[data-continuous]")?.addEventListener("change", (event) => {
    const continuous = event.currentTarget.checked;
    const count = one("[data-variation-count]");
    const label = one("[data-variation-count-label]");
    if (count) count.disabled = continuous;
    if (label) label.hidden = continuous;
  });

  one("[data-capture-form]")?.addEventListener("submit", async (event) => {
    if (event.submitter?.value === "cancel") return;
    event.preventDefault();
    const target = event.currentTarget;
    const referenceDateField = one("[data-reference-date]");
    if (referenceDateField) referenceDateField.value = new Date().toISOString();
    const form = new FormData(target);
    setBusy(target, true, "생성 큐에 등록하는 중…");
    setCaptureFeedback("");
    try {
      let bundle;
      const details = one("[data-capture-details]", target);
      const bundleInput = one("[data-bundle-json]", target);
      const rawBundle = String(form.get("bundle-json") ?? "").trim();
      if (rawBundle) {
        try {
          bundle = JSON.parse(rawBundle);
          bundleInput?.removeAttribute("aria-invalid");
        } catch {
          if (details) details.open = true;
          bundleInput?.setAttribute("aria-invalid", "true");
          bundleInput?.focus();
          if (captureSubmit) captureSubmit.disabled = true;
          throw new Error("컨텍스트 JSON 형식을 확인해 주세요.");
        }
        await request("/api/generation", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bundle }) });
      } else {
        const personaId = String(form.get("persona-context-id") ?? "");
        const promotionId = String(form.get("promotion-context-id") ?? "");
        if (!personaId || !promotionId) {
          one("[data-persona-select]")?.setAttribute("aria-invalid", String(!personaId));
          one("[data-promotion-select]")?.setAttribute("aria-invalid", String(!promotionId));
          throw new Error("페르소나와 홍보 소재를 선택해 주세요.");
        }
        const continuous = form.get("continuous") === "true";
        const referenceDate = new Date(String(form.get("reference-date") ?? ""));
        if (Number.isNaN(referenceDate.getTime())) throw new Error("기준 시각을 생성하지 못했습니다.");
        await request("/api/campaigns", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: form.get("campaign-name"),
            persona_context_id: personaId,
            promotion_context_id: promotionId,
            reference_asset_ids: form.getAll("reference-asset-ids"),
            reference_date: referenceDate.toISOString(),
            device: {
              kind: "simulator",
              udid: form.get("device-udid"),
              platform_version: form.get("platform-version"),
              device_name: form.get("device-name"),
            },
            variation_count: continuous ? null : Number(form.get("variation-count")),
          }),
        });
      }
      target.reset();
      captureDialog?.close();
      await Promise.all([loadCampaigns(), loadQueue()]);
      setNotice(rawBundle ? "단건 생성을 등록했습니다." : "반복 캠페인을 시작했습니다.");
    } catch (error) {
      setCaptureFeedback(error.message);
      setNotice(error.message);
    }
    finally { setBusy(target, false); }
  });
  one("[data-bundle-json]")?.addEventListener("input", clearCaptureValidation);
  one("[data-capture-dialog] [value='cancel']")?.addEventListener("click", () => captureDialog?.close());

  one("[data-chat-form]")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const field = one("textarea", event.currentTarget);
    const prompt = field?.value.trim();
    if (prompt) await submitChatPrompt(prompt);
  });

  const submitChatPrompt = async (prompt) => {
    if (!chatForm || !chatInput) return;
    setBusy(chatForm, true, "메시지를 보내는 중…");
    startApprovalPolling();
    try {
      const result = await request("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt, session_id: activeChatSessionId }) });
      const isCommandResult = result.replace_history
        || result.events?.length > 0
        || result.sessions?.length > 0
        || result.models?.length > 0;
      if (isCommandResult) {
        activeChatSessionId = result.session_id ?? activeChatSessionId;
        chatInput.value = "";
        renderCommandResult(result);
        setNotice("명령을 실행했습니다.");
      } else {
        activeChatSessionId = result.session_id;
        chatInput.value = "";
        renderChatSettings(result.settings);
        renderHistory(result.history);
        clearCommandOutput();
        setNotice("개인 대화가 저장되었습니다.");
      }
      updateCommandSuggestions();
    } catch (error) {
      if (error instanceof Error) setNotice(error.message);
      else throw error;
    } finally {
      stopApprovalPolling();
      setBusy(chatForm, false);
    }
  };

  chatInput?.addEventListener("input", updateCommandSuggestions);
  all("[data-chat-approval-decision]").forEach((button) => button.addEventListener("click", async () => {
    const requestId = chatApproval?.dataset.requestId;
    const decision = button.dataset.chatApprovalDecision;
    if (!requestId || !decision) return;
    setBusy(chatApproval, true, "승인 결정을 전송하는 중…");
    try {
      await request("/api/chat/approval", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request_id: requestId, decision }),
      });
      renderApproval(null);
    } catch (error) {
      if (error instanceof Error) setNotice(error.message);
      else throw error;
    } finally { setBusy(chatApproval, false); }
  }));

  all("[data-action='new-capture']").forEach((button) => button.addEventListener("click", () => captureDialog?.showModal())); document.addEventListener("trace-new-capture", () => captureDialog?.showModal());
  one("[data-action='retry']")?.addEventListener("click", async (event) => {
    const target = event.currentTarget;
    setBusy(target, true, "큐를 새로고침하는 중…");
    try { await loadQueue(); }
    catch (error) { setNotice(error.message); }
    finally { setBusy(target, false); }
  });
  one("[data-action='attach-context']")?.addEventListener("click", () => {
    document.dispatchEvent(new CustomEvent("trace-select-tab", { detail: "library" }));
  });

  void request("/api/auth/session").then(async (member) => {
    markAuthenticated(member);
    try {
      await refreshWorkspace();
      setNotice("워크스페이스에 연결되었습니다.");
    } catch (error) { setNotice(error.message); }
  }, markSignedOut);
})();
