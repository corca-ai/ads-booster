(() => {
  const tabs = Array.from(document.querySelectorAll("[role='tab'][data-tab]"));
  const panels = Array.from(document.querySelectorAll("[role='tabpanel'][data-panel]"));
  const navItems = Array.from(document.querySelectorAll("[data-nav-target]"));
  const notice = document.querySelector("[data-notice]");
  const chatForm = document.querySelector("[data-chat-form]");
  const chatThread = document.querySelector("[data-chat-thread]");
  const memberAccess = document.querySelector("[data-member-access]");
  const memberCode = document.querySelector("#member-code");
  const memberFeedback = document.querySelector("[data-member-feedback]");
  const memberConnected = document.querySelector("[data-member-connected]");
  const memberAccessForm = document.querySelector("[data-member-access-form]");
  const memberStateLabel = document.querySelector("[data-member-state-label]");
  const memberName = document.querySelector("[data-member-name]");
  let activeChatSessionId = null;

  const appendChatMessage = (role, text) => {
    if (!chatThread) return;
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
    meta.textContent = role === "assistant" ? "Trace agent · now" : "You · now";
    const body = document.createElement("p");
    body.className = "thread-body";
    body.textContent = text;
    content.append(meta, body);
    message.append(avatar, content);
    chatThread.append(message);
  };

  const renderPrivateHistory = (history) => {
    if (!chatThread) return;
    chatThread.replaceChildren();
    history.forEach((entry) => {
      if ((entry.role === "user" || entry.role === "assistant") && typeof entry.content === "string") {
        appendChatMessage(entry.role, entry.content);
      }
    });
  };

  const loadLatestPrivateHistory = async () => {
    const auth = await fetch("/api/auth/session", { credentials: "same-origin" });
    if (!auth.ok) return;
    const sessions = await fetch("/api/sessions", { credentials: "same-origin" });
    if (!sessions.ok) return;
    const summaries = await sessions.json();
    if (!Array.isArray(summaries) || summaries.length === 0) {
      chatThread?.replaceChildren();
      return;
    }
    activeChatSessionId = summaries[0].session_id;
    const session = await fetch(`/api/sessions/${encodeURIComponent(activeChatSessionId)}`, {
      credentials: "same-origin",
    });
    if (!session.ok) return;
    const payload = await session.json();
    if (Array.isArray(payload.history)) renderPrivateHistory(payload.history);
  };

  const selectTab = (name) => {
    tabs.forEach((tab) => {
      const active = tab.dataset.tab === name;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    panels.forEach((panel) => {
      const active = panel.dataset.panel === name;
      panel.hidden = !active;
    });
  };

  const initialTab = tabs.find((tab) => tab.getAttribute("aria-selected") === "true") ?? tabs[0];
  if (initialTab) selectTab(initialTab.dataset.tab);

  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => selectTab(tab.dataset.tab));
    tab.addEventListener("keydown", (event) => {
      const key = event.key;
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(key)) return;
      event.preventDefault();
      const nextIndex = key === 'Home' ? 0 : key === 'End' ? tabs.length - 1 : (index + (key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      const nextTab = tabs[nextIndex];
      nextTab.focus();
      selectTab(nextTab.dataset.tab);
    });
  });

  navItems.forEach((item) => {
    item.addEventListener("click", () => {
      const target = document.getElementById(item.dataset.navTarget);
      if (!target) return;
      navItems.forEach((nav) => nav.removeAttribute("aria-current"));
      item.setAttribute("aria-current", "page");
      if (target.dataset.panel) selectTab(target.dataset.panel);
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      target.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
      if (target.dataset.panel) target.focus({ preventScroll: true });
    });
  });

  document.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => {
      if (notice) notice.textContent = `${button.textContent.trim()} is a local static-shell action.`;
    });
  });

  chatForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const field = chatForm.querySelector("textarea");
    const value = field?.value.trim();
    if (!value || !chatThread) return;
    const submit = chatForm.querySelector('button[type="submit"]');
    if (submit) submit.disabled = true;
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: value, session_id: activeChatSessionId }),
      });
      const payload = await response.json();
      if (!response.ok) {
        if (notice) notice.textContent = payload.detail?.message ?? "Private chat request failed.";
        return;
      }
      activeChatSessionId = payload.session_id;
      field.value = "";
      if (Array.isArray(payload.history)) renderPrivateHistory(payload.history);
      if (notice) notice.textContent = "Private conversation saved.";
    } catch {
      if (notice) notice.textContent = "Private chat service is unavailable.";
    } finally {
      if (submit) submit.disabled = false;
    }
  });

  memberAccess?.addEventListener("submit", (event) => {
    event.preventDefault();
    const code = memberCode?.value.trim() ?? "";
    if (!code) {
      memberCode?.setAttribute("aria-invalid", "true");
      if (memberFeedback) {
        memberFeedback.hidden = false;
        memberFeedback.textContent = "Enter a member code before opening the workspace.";
      }
      memberCode?.focus();
      return;
    }
    memberCode?.removeAttribute("aria-invalid");
    memberCode.value = "";
    if (memberFeedback) memberFeedback.hidden = true;
    if (memberAccessForm) memberAccessForm.hidden = true;
    if (memberConnected) memberConnected.hidden = false;
    if (memberStateLabel) memberStateLabel.textContent = "Connected locally";
    if (memberName) memberName.textContent = "Member code accepted";
    if (notice) notice.textContent = "Member code accepted for this browser session.";
  });

  document.querySelector("[data-member-reset]")?.addEventListener("click", () => {
    if (memberAccessForm) memberAccessForm.hidden = false;
    if (memberConnected) memberConnected.hidden = true;
    if (memberStateLabel) memberStateLabel.textContent = "Signed out";
    if (memberName) memberName.textContent = "Enter a workspace code";
    memberCode?.focus();
  });

  void loadLatestPrivateHistory().catch(() => {
    if (notice) notice.textContent = "Private chat service is unavailable.";
  });
})();
