(() => {
  const one = (selector, root = document) => root.querySelector(selector); const all = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const notice = one("[data-notice]"); const memberForm = one("[data-member-access]");
  const memberFields = one("[data-member-access-form]"); const memberConnected = one("[data-member-connected]");
  const memberFeedback = one("[data-member-feedback]"); const memberLabel = one("[data-member-state-label]");
  const memberName = one("[data-member-name]");
  const workspaceLive = one("[data-workspace-live]");
  const entryScreen = one("[data-entry-screen]");
  const accessTokenField = one("#workspace-access-id");
  const inviteDialog = one("[data-invite-dialog]"); const inviteButton = one("[data-action='open-invite']");
  const inviteForm = one("[data-invite-form]"); const inviteName = one("[data-invite-name]");
  const inviteFeedback = one("[data-invite-feedback]"); const inviteResult = one("[data-invite-result]");
  const inviteToken = one("[data-invite-token]"); const inviteCopy = one("[data-invite-copy]");
  const skipLink = one(".skip-link");
  const candidateEmpty = one("[data-candidate-empty]");
  const candidateFeedback = one("[data-candidate-feedback]");
  const autogenFeedback = one("[data-autogen-feedback]");

  const HANGUL = /[가-힣]/;
  const ERROR_MESSAGES = Object.freeze({
    "authentication required": "로그인이 필요합니다.",
    "invalid credentials": "워크스페이스 또는 멤버 정보가 올바르지 않습니다.",
    "candidate not found": "후보를 찾을 수 없습니다. 새로고침 후 다시 시도해 주세요.",
    "candidate revision conflict": "다른 사람이 먼저 처리했습니다. 새로고침 후 다시 시도해 주세요.",
    "candidate already reviewed": "이미 승인 또는 반려된 후보입니다. 새로고침 후 다시 시도해 주세요.",
  });
  const CANDIDATE_SOURCE_LABELS = Object.freeze({
    auto: "🤖 자동",
    manual: "✍️ 수동",
  });
  const CANDIDATE_STATUS_LABELS = Object.freeze({
    awaiting_review: "캡션·주제 검수 대기",
    caption_approved: "캡션·주제 승인됨 · 이미지 대기",
    rejected: "반려됨",
    image_awaiting_review: "이미지 검수 대기",
    submitted: "제출됨 · 게시 준비 완료",
  });
  const JOURNEY_STEPS = Object.freeze(["① 캡션·주제 승인", "② 이미지 승인", "③ 제출"]);
  const JOURNEY_POSITION = Object.freeze({
    awaiting_review: 0,
    rejected: 0,
    caption_approved: 1,
    image_awaiting_review: 1,
    submitted: JOURNEY_STEPS.length,
  });

  const ACCESS_ID_SEPARATOR = "%";
  const ACCESS_ID_PREFIX = "Workspace access ID (shown once; not written to logs):";

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

  const COUNTRY_LANGUAGES = Object.freeze({
    KR: "ko",
    JP: "ja",
    TW: "zh",
    US: "en",
  });
  const DEFAULT_LANGUAGE = "en";
  const MAX_TRACE_ITEMS = 8;

  const countryLanguage = (country) => COUNTRY_LANGUAGES[country] ?? DEFAULT_LANGUAGE;

  const localizeError = (message) => ERROR_MESSAGES[message] ?? "요청에 실패했습니다. 잠시 후 다시 시도해 주세요.";
  const candidateSourceLabel = (source) => CANDIDATE_SOURCE_LABELS[source] ?? source;
  const candidateStatusLabel = (status) => CANDIDATE_STATUS_LABELS[status] ?? status;
  const candidateDate = (seconds) => new Date(seconds * 1000).toLocaleDateString("ko-KR");

  const setNotice = (message) => {
    if (notice) notice.textContent = message;
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
      const message = validationError || (detail && HANGUL.test(detail))
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
    if (inviteButton) inviteButton.hidden = true;
    inviteDialog?.close();
    clearInviteResult();
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

  const setCandidateFeedback = (message) => {
    if (!candidateFeedback) return;
    candidateFeedback.hidden = !message;
    candidateFeedback.textContent = message;
  };

  const setAutogenFeedback = (message) => {
    if (!autogenFeedback) return;
    autogenFeedback.hidden = !message;
    autogenFeedback.textContent = message;
  };

  const badge = (className, text) => {
    const element = document.createElement("span");
    element.className = className;
    element.textContent = text;
    return element;
  };

  const journeyNode = (record) => {
    const list = document.createElement("ol");
    list.className = "journey";
    list.setAttribute("aria-label", "후보 진행 단계");
    const rejected = record.status === "rejected";
    const position = JOURNEY_POSITION[record.status] ?? 0;
    JOURNEY_STEPS.forEach((label, index) => {
      const item = document.createElement("li");
      const state = rejected
        ? (index === 0 ? "is-rejected" : "")
        : index < position ? "is-done" : index === position ? "is-current" : "";
      item.className = `journey__step${state ? ` ${state}` : ""}`;
      item.textContent = label;
      if (state === "is-current") item.setAttribute("aria-current", "step");
      list.append(item);
    });
    return list;
  };

  const candidateNode = (record) => {
    const row = document.createElement("article"); row.className = "candidate-row";
    const source = badge("candidate-source", candidateSourceLabel(record.source));
    const content = document.createElement("span"); content.className = "candidate-row__content";
    const title = document.createElement("strong");
    title.textContent = record.topic || "(주제 없음)";
    const caption = document.createElement("span"); caption.className = "candidate-row__caption";
    caption.textContent = record.caption.split("\n", 1)[0] || "(캡션 없음)";
    const meta = document.createElement("span"); meta.className = "candidate-row__meta";
    meta.textContent = `${record.country} · ${candidateDate(record.created_at)}`;
    content.append(title, caption, meta, journeyNode(record));
    const trailing = document.createElement("span"); trailing.className = "candidate-row__trailing";
    trailing.append(badge(`candidate-status ${record.status}`, candidateStatusLabel(record.status)));
    row.append(source, content, trailing);
    return row;
  };

  const approvalField = (label, value) => {
    const field = document.createElement("div"); field.className = "approval-field";
    const name = document.createElement("span"); name.className = "eyebrow";
    name.textContent = label;
    const body = document.createElement("span"); body.className = "approval-field__value";
    body.textContent = value;
    field.append(name, body);
    return field;
  };

  const approvalPrinciples = (record) => {
    const field = document.createElement("div"); field.className = "approval-field";
    const name = document.createElement("span"); name.className = "eyebrow";
    name.textContent = "적용 원리";
    const list = document.createElement("span"); list.className = "approval-badges";
    if (record.principles_applied.length) {
      record.principles_applied.forEach((principle) => list.append(badge("approval-badge", `원리 ${principle}`)));
    } else {
      list.append(badge("approval-badge quiet", "없음"));
    }
    field.append(name, list);
    return field;
  };

  const approvalVisual = (record) => {
    const visual = document.createElement("div"); visual.className = "approval-visual";
    if (record.image_path) {
      const image = document.createElement("img");
      image.src = `/api/candidates/${encodeURIComponent(record.candidate_id)}/image`;
      image.alt = "합성된 후보 이미지";
      image.loading = "lazy";
      image.addEventListener("error", () => {
        visual.replaceChildren(badge("approval-visual__placeholder", "이미지를 불러올 수 없습니다"));
      });
      visual.append(image);
      return visual;
    }
    visual.append(badge("approval-visual__placeholder", "이미지 생성 전"));
    return visual;
  };

  const approvalNode = (record) => {
    const card = document.createElement("article"); card.className = "approval-card";
    const header = document.createElement("div"); header.className = "approval-card__header";
    header.append(
      badge("candidate-source", candidateSourceLabel(record.source)),
      badge("mono", `${record.country} · ${candidateDate(record.created_at)}`),
    );
    const journey = journeyNode(record);
    const body = document.createElement("div"); body.className = "approval-card__body";
    const text = document.createElement("div"); text.className = "approval-card__text";
    const topicLabel = document.createElement("span"); topicLabel.className = "eyebrow";
    topicLabel.textContent = "주제/컨셉";
    const topic = document.createElement("h3"); topic.className = "approval-card__topic";
    topic.textContent = record.topic || "(주제 없음)";
    const caption = document.createElement("p"); caption.className = "approval-card__caption";
    caption.textContent = record.caption;
    text.append(topicLabel, topic, caption);
    body.append(text, approvalVisual(record));
    const facts = document.createElement("div"); facts.className = "approval-card__facts";
    facts.append(
      approvalField("가설", record.hypothesis),
      approvalPrinciples(record),
      approvalField("참조", record.refs_used.length ? record.refs_used.join(", ") : "—"),
      approvalField("AI 검수", record.ai_verdict || "—"),
    );
    const order = document.createElement("details"); order.className = "advanced-input";
    const summary = document.createElement("summary"); summary.textContent = "Appium 프롬프트";
    const orderBody = document.createElement("pre"); orderBody.className = "approval-card__order";
    orderBody.textContent = record.shooting_order || "Appium 프롬프트가 비어 있습니다.";
    order.append(summary, orderBody);
    const actions = document.createElement("div"); actions.className = "approval-card__actions";
    const reason = document.createElement("input");
    reason.className = "approval-card__reason";
    reason.type = "text";
    reason.maxLength = 2000;
    reason.placeholder = "반려 사유 (다음 후보 생성에 반영됩니다)";
    reason.setAttribute("aria-label", "반려 사유");
    const buttons = [true, false].map((accepted) => {
      const button = document.createElement("button");
      button.className = `button ${accepted ? "button-primary" : "button-secondary"}`;
      button.type = "button";
      button.textContent = accepted ? "✅ 캡션·주제 승인" : "❌ 반려";
      button.addEventListener("click", () => reviewCandidate(record, accepted, reason, button, () => buttons));
      return button;
    });
    actions.append(reason, ...buttons);
    card.append(header, journey, body, facts, order, actions);
    return card;
  };

  const imageSummary = (record) => {
    const text = document.createElement("div"); text.className = "approval-card__text";
    const label = document.createElement("span"); label.className = "eyebrow";
    label.textContent = "주제/컨셉";
    const topic = document.createElement("h3"); topic.className = "approval-card__topic";
    topic.textContent = record.topic || "(주제 없음)";
    const caption = document.createElement("p"); caption.className = "approval-card__caption";
    caption.textContent = record.caption;
    text.append(label, topic, caption);
    return text;
  };

  const imageNode = (record) => {
    const card = document.createElement("article"); card.className = "approval-card";
    const header = document.createElement("div"); header.className = "approval-card__header";
    header.append(
      badge("candidate-source", candidateSourceLabel(record.source)),
      badge(`candidate-status ${record.status}`, candidateStatusLabel(record.status)),
    );
    const body = document.createElement("div"); body.className = "approval-card__body";
    body.append(imageSummary(record), approvalVisual(record));
    const actions = document.createElement("div"); actions.className = "approval-card__actions";
    const feedback = document.createElement("p");
    feedback.className = "candidate-feedback";
    feedback.setAttribute("role", "alert");
    feedback.hidden = true;
    if (record.status === "caption_approved") {
      const button = document.createElement("button");
      button.className = "button button-primary";
      button.type = "button";
      button.textContent = "🎨 이미지 생성";
      button.addEventListener("click", () => generateCandidateImage(record, button, feedback));
      actions.append(button);
      if (record.review_note) {
        const note = approvalField("직전 반려 사유", record.review_note);
        card.append(header, body, note, actions, feedback);
        return card;
      }
    } else {
      const reason = document.createElement("input");
      reason.className = "approval-card__reason";
      reason.type = "text";
      reason.maxLength = 2000;
      reason.placeholder = "반려 사유 (다음 이미지 생성에 반영됩니다)";
      reason.setAttribute("aria-label", "이미지 반려 사유");
      const buttons = [true, false].map((accepted) => {
        const button = document.createElement("button");
        button.className = `button ${accepted ? "button-primary" : "button-secondary"}`;
        button.type = "button";
        button.textContent = accepted ? "✅ 승인" : "❌ 반려";
        button.addEventListener("click", () =>
          reviewCandidateImage(record, accepted, reason, button, feedback, () => buttons));
        return button;
      });
      actions.append(reason, ...buttons);
    }
    card.append(header, body, actions, feedback);
    return card;
  };

  const setCardFeedback = (element, message) => {
    if (!element) return;
    element.hidden = !message;
    element.textContent = message;
  };

  const generateCandidateImage = async (record, button, feedback) => {
    const label = button.textContent;
    button.disabled = true;
    button.textContent = "이미지 생성 중… (1~3분)";
    setCardFeedback(feedback, "");
    setBusy(button, true, "잠금화면 이미지를 만드는 중… (1~3분 소요)");
    try {
      await request(`/api/candidates/${encodeURIComponent(record.candidate_id)}/generate-image`, {
        method: "POST",
      });
      await loadCandidates();
      setNotice("이미지를 만들었습니다. 이미지 검수를 진행해 주세요.");
    } catch (error) {
      setCardFeedback(feedback, error.message);
      setNotice(error.message);
    } finally {
      button.disabled = false;
      button.textContent = label;
      setBusy(button, false);
    }
  };

  const reviewCandidateImage = async (record, accepted, reason, target, feedback, siblings) => {
    const note = String(reason?.value ?? "").trim();
    const disabled = siblings();
    disabled.forEach((button) => { button.disabled = true; });
    setCardFeedback(feedback, "");
    setBusy(target, true, "이미지 검수 결과를 저장하는 중…");
    try {
      await request(`/api/candidates/${encodeURIComponent(record.candidate_id)}/review-image`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accepted, note: note || null, expected_revision: record.revision }),
      });
      await loadCandidates();
      setNotice(accepted ? "제출 준비가 끝났습니다." : "이미지를 반려했습니다. 다시 생성할 수 있습니다.");
    } catch (error) {
      setCardFeedback(feedback, error.message);
      setNotice(error.message);
      disabled.forEach((button) => { button.disabled = false; });
    } finally {
      setBusy(target, false);
    }
  };

  const reviewCandidate = async (record, accepted, reason, target, siblings) => {
    const note = String(reason?.value ?? "").trim();
    const disabled = siblings();
    disabled.forEach((button) => { button.disabled = true; });
    setBusy(target, true, "검수 결과를 저장하는 중…");
    try {
      await request(`/api/candidates/${encodeURIComponent(record.candidate_id)}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accepted, note: note || null, expected_revision: record.revision }),
      });
      await loadCandidates();
      setNotice(accepted ? "주제와 캡션을 승인했습니다. 이미지 승인 단계로 넘어갑니다." : "후보를 반려했습니다.");
    } catch (error) {
      setNotice(error.message);
      disabled.forEach((button) => { button.disabled = false; });
    } finally {
      setBusy(target, false);
    }
  };

  const loadCandidates = async () => {
    const records = await request("/api/candidates");
    one("[data-candidate-list]")?.replaceChildren(...records.map(candidateNode));
    const count = one("[data-candidate-count]");
    if (count) count.textContent = `후보 ${records.length}개`;
    if (candidateEmpty) candidateEmpty.hidden = records.length > 0;
    const pending = records.filter((record) => record.status === "awaiting_review");
    one("[data-approval-list]")?.replaceChildren(...pending.map(approvalNode));
    const approvalEmpty = one("[data-approval-empty]");
    if (approvalEmpty) approvalEmpty.hidden = pending.length > 0;
    const approvalCount = one("[data-approval-count]");
    if (approvalCount) approvalCount.textContent = `캡션·주제 검수 대기 ${pending.length}건`;
    const imageStage = records.filter(
      (record) => record.status === "caption_approved" || record.status === "image_awaiting_review",
    );
    one("[data-image-list]")?.replaceChildren(...imageStage.map(imageNode));
    const imageEmpty = one("[data-image-empty]");
    if (imageEmpty) imageEmpty.hidden = imageStage.length > 0;
    const imageCount = one("[data-image-count]");
    if (imageCount) {
      const waiting = imageStage.filter((record) => record.status === "image_awaiting_review");
      imageCount.textContent = `이미지 대기 ${imageStage.length}건 · 검수 대기 ${waiting.length}건`;
    }
  };

  const commaList = (value) => String(value ?? "").split(",").map((item) => item.trim()).filter(Boolean);
  const lineList = (value) => String(value ?? "").split("\n").map((item) => item.trim()).filter(Boolean);

  const refreshWorkspace = async () => {
    setBusy(workspaceLive, true, "워크스페이스를 새로고침하는 중…");
    try {
      await loadCandidates();
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

  const generateCandidates = async (button) => {
    const label = button.textContent;
    button.disabled = true;
    button.textContent = "생성 중… (1~3분 소요)";
    setAutogenFeedback("");
    setBusy(workspaceLive, true, "AI가 후보를 만드는 중… (1~3분 소요)");
    try {
      const created = await request("/api/candidates/generate", { method: "POST" });
      await loadCandidates();
      setNotice(`후보 ${created.length}개가 등록되었습니다.`);
    } catch (error) {
      setAutogenFeedback(error.message);
      setNotice(error.message);
    } finally {
      button.disabled = false;
      button.textContent = label;
      setBusy(workspaceLive, false);
    }
  };

  all("[data-autogen]").forEach((button) =>
    button.addEventListener("click", () => generateCandidates(button)));

  const candidateProblem = (draft) => {
    if (!draft.topic) return ["candidate-topic", "주제/컨셉을 입력해 주세요."];
    if (!/^[A-Z]{2}$/.test(draft.country)) return ["candidate-country", "국가는 두 자리 국가 코드로 입력해 주세요. 예: JP"];
    if (!draft.caption) return ["candidate-caption", "캡션을 입력해 주세요."];
    if (!draft.hypothesis) return ["candidate-hypothesis", "가설을 입력해 주세요."];
    if (draft.principles_applied.some((value) => !Number.isInteger(value) || value < 1)) {
      return ["candidate-principles", "적용 원리는 1 이상의 숫자를 쉼표로 구분해 입력해 주세요."];
    }
    const items = draft.image_inputs.trace_items;
    if (!items.length) return ["candidate-schedule", "잠금화면 일정을 한 줄에 하나씩 입력해 주세요."];
    if (items.length > MAX_TRACE_ITEMS) {
      return ["candidate-schedule", `잠금화면 일정은 최대 ${MAX_TRACE_ITEMS}줄까지 입력할 수 있습니다.`];
    }
    if (!/^\d{2}:\d{2}$/.test(draft.image_inputs.device_time)) {
      return ["candidate-device-time", "기기 시각은 HH:MM 형식으로 입력해 주세요. 예: 07:20"];
    }
    if (!draft.image_inputs.background_mood) {
      return ["candidate-background-mood", "배경 분위기를 입력해 주세요."];
    }
    return null;
  };

  one("[data-candidate-form]")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const target = event.currentTarget;
    const form = new FormData(target);
    const draft = {
      topic: String(form.get("topic") ?? "").trim(),
      country: String(form.get("country") ?? "").trim().toUpperCase(),
      caption: String(form.get("caption") ?? "").trim(),
      hypothesis: String(form.get("hypothesis") ?? "").trim(),
      refs_used: commaList(form.get("refs-used")),
      principles_applied: commaList(form.get("principles-applied")).map(Number),
      shooting_order: String(form.get("shooting-order") ?? ""),
      image_inputs: {
        trace_items: lineList(form.get("trace-items")),
        device_time: String(form.get("device-time") ?? "").trim(),
        background_subject: String(form.get("background-subject") ?? "").trim(),
        background_mood: String(form.get("background-mood") ?? "").trim(),
        language: countryLanguage(String(form.get("country") ?? "").trim().toUpperCase()),
      },
    };
    ["candidate-topic", "candidate-country", "candidate-caption", "candidate-hypothesis", "candidate-principles",
      "candidate-schedule", "candidate-device-time", "candidate-background-mood"]
      .forEach((id) => document.getElementById(id)?.removeAttribute("aria-invalid"));
    const problem = candidateProblem(draft);
    if (problem) {
      const [id, message] = problem;
      const field = document.getElementById(id);
      field?.setAttribute("aria-invalid", "true");
      field?.focus();
      setCandidateFeedback(message);
      return;
    }
    setBusy(target, true, "후보를 등록하는 중…");
    setCandidateFeedback("");
    try {
      await request("/api/candidates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draft),
      });
      target.reset();
      await loadCandidates();
      setNotice("후보를 등록했습니다.");
    } catch (error) {
      setCandidateFeedback(error.message);
      setNotice(error.message);
    }
    finally { setBusy(target, false); }
  });

  void request("/api/auth/session").then(async (member) => {
    markAuthenticated(member);
    try {
      await refreshWorkspace();
      setNotice("워크스페이스에 연결되었습니다.");
    } catch (error) { setNotice(error.message); }
  }, markSignedOut);
})();
