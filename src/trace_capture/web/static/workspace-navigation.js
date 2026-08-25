(() => {
  const tabs = Array.from(document.querySelectorAll("[role='tab'][data-tab]"));
  const panels = Array.from(document.querySelectorAll("[role='tabpanel'][data-panel]"));
  const commandDialog = document.querySelector("[data-command-dialog]");
  const closeCommandMenu = () => commandDialog?.close();
  const panelId = (name) => name === "review" ? "reviews" : name;
  const scrollToTarget = (target) => {
    if (!target) return;
    const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const scroller = document.querySelector(".workspace-main");
    if (scroller?.contains(target)) {
      scroller.scrollTo({
        behavior: reduced ? "auto" : "smooth",
        top: Math.max(0, target.offsetTop - scroller.offsetTop),
      });
      return;
    }
    target.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
  };
  const selectTab = (name) => {
    tabs.forEach((tab) => {
      const active = tab.dataset.tab === name;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    panels.forEach((panel) => { panel.hidden = panel.dataset.panel !== name; });
  };
  const initialTab = tabs.find((tab) => tab.getAttribute("aria-selected") === "true") ?? tabs[0];
  if (initialTab) selectTab(initialTab.dataset.tab);
  const revealTab = (name) => {
    selectTab(name);
    const panel = document.getElementById(panelId(name));
    panel?.focus({ preventScroll: true });
    scrollToTarget(panel);
  };
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => selectTab(tab.dataset.tab));
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const delta = event.key === "ArrowRight" ? 1 : -1;
      const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + delta + tabs.length) % tabs.length;
      tabs[next].focus();
      selectTab(tabs[next].dataset.tab);
    });
  });
  document.querySelectorAll("[data-action='open-command']").forEach((button) => {
    button.addEventListener("click", () => {
      commandDialog?.showModal();
      commandDialog?.querySelector("[data-command-action='open-review']")?.focus({ preventScroll: true });
    });
  });
  commandDialog?.querySelector("[data-command-action='close']")?.addEventListener("click", closeCommandMenu);
  commandDialog?.querySelector("[data-command-action='open-review']")?.addEventListener("click", () => {
    closeCommandMenu();
    revealTab("review");
  });
  document.querySelectorAll("[data-action='open-review']").forEach((button) => {
    button.addEventListener("click", () => revealTab("review"));
  });
})();
