import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = dirname(dirname(dirname(fileURLToPath(import.meta.url))));
const staticRoot = join(root, "src", "trace_capture", "web", "static");
const liveSource = readFileSync(join(staticRoot, "workspace-live.js"), "utf8");

class FakeElement {
  constructor(id, dataset = {}) {
    this.id = id;
    this.dataset = dataset;
    this.hidden = false;
    this.disabled = false;
    this.tabIndex = 0;
    this.attributes = new Map();
    this.listeners = new Map();
    this.events = [];
    this.open = false;
    this.children = [];
    this.role = dataset.role ?? null;
    this.formValues = new Map();
    this.resetCount = 0;
  }

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  async click() {
    const listener = this.listeners.get("click");
    if (listener) await listener({ currentTarget: this, preventDefault() {} });
  }

  async submit(submitter = null) {
    const listener = this.listeners.get("submit");
    if (!listener) return;
    const event = { currentTarget: this, submitter, preventDefault() {} };
    const pending = listener(event);
    event.currentTarget = null;
    await pending;
  }

  reset() {
    this.resetCount += 1;
    this.events.push("reset");
  }

  focus() {
    this.events.push("focus");
    this.ownerDocument.activeElement = this;
  }

  scrollIntoView() {
    this.events.push("scroll");
  }

  showModal() {
    this.events.push("show-modal");
    this.open = true;
  }

  close() {
    this.events.push("close");
    this.open = false;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = children;
  }

  querySelector(selector) {
    return this.children.find((child) => child.matches(selector)) ?? null;
  }

  matches(selector) {
    if (selector.includes("[role='tab']")) return this.role === "tab";
    if (selector.includes("[role='tabpanel']")) return this.role === "tabpanel";
    if (selector.includes("[data-nav-target]")) return this.dataset.navTarget !== undefined;
    if (selector.includes("[data-command-dialog]")) return this.dataset.commandDialog === true;
    if (selector.includes("[data-action='open-command']")) return this.dataset.action === "open-command";
    if (selector.includes("[data-action='open-review']")) return this.dataset.action === "open-review";
    if (selector === "[data-command-action]") return this.dataset.commandAction !== undefined;
    const actionMatch = selector.match(/\[data-command-action=['"]([^'"]+)['"]\]/);
    if (actionMatch) return this.dataset.commandAction === actionMatch[1];
    return false;
  }
}

class FakeDocument {
  constructor(elements) {
    this.elements = elements;
    this.activeElement = null;
    this.listeners = new Map();
    for (const element of elements) element.ownerDocument = this;
  }

  createElement(id) {
    return new FakeElement(id);
  }

  querySelector(selector) {
    return this.elements.find((element) => element.matches(selector)) ?? null;
  }

  querySelectorAll(selector) {
    return this.elements.filter((element) => element.matches(selector));
  }

  getElementById(id) {
    return this.elements.find((element) => element.id === id) ?? null;
  }

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  dispatchEvent(event) {
    const listener = this.listeners.get(event.type);
    if (listener) listener(event);
  }
}

const makeNavigationDocument = () => {
  const tabs = ["queue", "review", "library", "chat"].map((name) => new FakeElement(`tab-${name}`, { tab: name, role: "tab" }));
  const panels = [
    new FakeElement("queue", { panel: "queue", role: "tabpanel" }),
    new FakeElement("reviews", { panel: "review", role: "tabpanel" }),
    new FakeElement("library", { panel: "library", role: "tabpanel" }),
    new FakeElement("chat", { panel: "chat", role: "tabpanel" }),
  ];
  const navItems = ["overview", "queue", "reviews", "chat", "library"].map(
    (name) => new FakeElement(`nav-${name}`, { navTarget: name }),
  );
  const commandButton = new FakeElement("command", { action: "open-command" });
  const reviewButton = new FakeElement("open-review", { action: "open-review" });
  const commandDialog = new FakeElement("command-dialog", { commandDialog: true });
  const firstCommand = new FakeElement("command-new-capture", { commandAction: "new-capture" });
  commandDialog.children = [firstCommand];
  return {
    document: new FakeDocument([
      ...tabs,
      ...panels,
      ...navItems,
      commandButton,
      reviewButton,
      commandDialog,
      firstCommand,
    ]),
    tabs,
    panels,
    commandButton,
    reviewButton,
    commandDialog,
    firstCommand,
  };
};

const loadNavigation = async (fixture) => {
  const source = await readFile(join(staticRoot, "workspace-navigation.js"), "utf8");
  runInNewContext(source, {
    document: fixture.document,
    matchMedia: () => ({ matches: true }),
    console,
  });
};

const testAttachContextFocusesBeforeScroll = async () => {
  const fixture = makeNavigationDocument();
  await loadNavigation(fixture);
  fixture.document.dispatchEvent({ type: "trace-select-tab", detail: "library" });
  const library = fixture.panels.find((panel) => panel.id === "library");
  assert.equal(library.hidden, false);
  assert.deepEqual(library.events, ["focus", "scroll"]);
  assert.equal(fixture.tabs.find((tab) => tab.dataset.tab === "library").getAttribute("aria-selected"), "true");
};

const testCommandAndReviewHaveDistinctDestinations = async () => {
  const commandFixture = makeNavigationDocument();
  await loadNavigation(commandFixture);
  await commandFixture.commandButton.click();
  assert.equal(commandFixture.commandDialog.open, true);
  assert.equal(commandFixture.document.activeElement, commandFixture.firstCommand);

  const reviewFixture = makeNavigationDocument();
  await loadNavigation(reviewFixture);
  await reviewFixture.reviewButton.click();
  assert.equal(reviewFixture.commandDialog.open, false);
  const reviews = reviewFixture.panels.find((panel) => panel.id === "reviews");
  assert.equal(reviews.hidden, false);
  assert.deepEqual(reviews.events, ["focus", "scroll"]);
};

const makeLiveDocument = () => {
  const workspaceLive = new FakeElement("workspace-live");
  const entryScreen = new FakeElement("entry-screen");
  const skipLink = new FakeElement("skip-link");
  const liveStatus = new FakeElement("live-status");
  const memberForm = new FakeElement("member-form");
  const memberFields = new FakeElement("member-fields");
  const memberConnected = new FakeElement("member-connected");
  const memberFeedback = new FakeElement("member-feedback");
  const memberLabel = new FakeElement("member-label");
  const memberName = new FakeElement("member-name");
  const notice = new FakeElement("notice");
  const queueList = new FakeElement("queue-list");
  const queueEmpty = new FakeElement("queue-empty");
  const queueSummary = new FakeElement("queue-summary");
  memberForm.formValues = new Map([
    ["workspace-id", "workspace-1"],
    ["member-id", "member-1"],
    ["workspace-code", "workspace-code"],
    ["member-code", "member-code"],
  ]);
  const captureDialog = new FakeElement("capture-dialog");
  const captureCancel = new FakeElement("capture-cancel");
  const captureDetails = new FakeElement("capture-details");
  captureDetails.matches = (selector) => selector === "[data-capture-details]";
  const bundleInput = new FakeElement("bundle-input");
  bundleInput.matches = (selector) => selector === "[data-bundle-json]";
  const captureSubmit = new FakeElement("capture-submit");
  captureSubmit.matches = (selector) => selector === "[data-capture-submit]";
  const personaSelect = new FakeElement("persona-select");
  personaSelect.matches = (selector) => selector === "[data-persona-select]";
  const promotionSelect = new FakeElement("promotion-select");
  promotionSelect.matches = (selector) => selector === "[data-promotion-select]";
  const contextForm = new FakeElement("context-form");
  contextForm.formValues = new Map([
    ["kind", "brief"],
    ["title", "Trace"],
    ["content", "Local context"],
  ]);
  const captureForm = new FakeElement("capture-form");
  captureForm.formValues = new Map([
    ["bundle-json", JSON.stringify({ persona: {}, promotion_material: {} })],
    ["campaign-name", "Campaign"],
    ["persona-context-id", "persona-1"],
    ["promotion-context-id", "promotion-1"],
    ["reference-date", "2026-08-25T09:00"],
    ["device-udid", "E1FB798D-79E6-4B25-A987-D298A4FD122A"],
    ["platform-version", "26.5"],
    ["device-name", "iPhone 17 Pro"],
    ["continuous", "true"],
  ]);
  captureForm.children = [
    captureDetails,
    bundleInput,
    captureSubmit,
    personaSelect,
    promotionSelect,
  ];
  const chatForm = new FakeElement("chat-form");
  const chatField = new FakeElement("chat-field");
  chatField.value = "Hello Trace";
  chatField.matches = (selector) => selector === "textarea";
  chatForm.children = [chatField];
  const chatCommandOutput = new FakeElement("chat-command-output");
  const chatCommandEvents = new FakeElement("chat-command-events");
  const chatCommandOptions = new FakeElement("chat-command-options");
  const chatCommandSuggestions = new FakeElement("chat-command-suggestions");
  const chatApproval = new FakeElement("chat-approval");
  const chatApprovalAction = new FakeElement("chat-approval-action");
  const chatApprovalDetail = new FakeElement("chat-approval-detail");
  const chatModel = new FakeElement("chat-model");
  const chatPermission = new FakeElement("chat-permission");
  chatCommandOutput.hidden = true;
  chatApproval.hidden = true;
  const selectors = new Map([
    ["[data-workspace-live]", workspaceLive],
    ["[data-entry-screen]", entryScreen],
    [".skip-link", skipLink],
    ["[data-live-status]", liveStatus],
    ["[data-member-access]", memberForm],
    ["[data-member-access-form]", memberFields],
    ["[data-member-connected]", memberConnected],
    ["[data-member-feedback]", memberFeedback],
    ["[data-member-state-label]", memberLabel],
    ["[data-member-name]", memberName],
    ["[data-notice]", notice],
    ["[data-queue-list]", queueList],
    ["[data-queue-empty]", queueEmpty],
    ["[data-queue-summary]", queueSummary],
    ["[data-context-form]", contextForm],
    ["[data-capture-form]", captureForm],
    ["[data-capture-dialog]", captureDialog],
    ["[data-capture-dialog] [value='cancel']", captureCancel],
    ["[data-capture-submit]", captureSubmit],
    ["[data-persona-select]", personaSelect],
    ["[data-promotion-select]", promotionSelect],
    ["[data-chat-form]", chatForm],
    ["[data-chat-command-output]", chatCommandOutput],
    ["[data-chat-command-events]", chatCommandEvents],
    ["[data-chat-command-options]", chatCommandOptions],
    ["[data-chat-command-suggestions]", chatCommandSuggestions],
    ["[data-chat-approval]", chatApproval],
    ["[data-chat-approval-action]", chatApprovalAction],
    ["[data-chat-approval-detail]", chatApprovalDetail],
    ["[data-chat-model]", chatModel],
    ["[data-chat-permission]", chatPermission],
  ]);
  const document = new FakeDocument([
    workspaceLive,
    entryScreen,
    skipLink,
    liveStatus,
    memberForm,
    memberFields,
    memberConnected,
    memberFeedback,
    memberLabel,
    memberName,
    notice,
    queueList,
    queueEmpty,
    queueSummary,
    contextForm,
    captureForm,
    captureDialog,
    captureCancel,
    captureDetails,
    bundleInput,
    captureSubmit,
    personaSelect,
    promotionSelect,
    chatForm,
    chatField,
  ]);
  document.querySelector = (selector) => selectors.get(selector) ?? null;
  document.querySelectorAll = () => [];
  return {
    document,
    workspaceLive,
    entryScreen,
    skipLink,
    liveStatus,
    notice,
    memberForm,
    memberFields,
    memberConnected,
    memberFeedback,
    memberLabel,
    queueEmpty,
    queueSummary,
    contextForm,
    captureForm,
    captureDialog,
    captureCancel,
    captureDetails,
    bundleInput,
    captureSubmit,
    personaSelect,
    promotionSelect,
    chatForm,
    chatField,
    chatCommandOutput,
    chatCommandEvents,
    chatCommandOptions,
    chatCommandSuggestions,
    chatApproval,
    chatApprovalAction,
    chatApprovalDetail,
    chatModel,
    chatPermission,
  };
};

const loadLive = async (fixture, fetchImplementation) => {
  runInNewContext(liveSource, {
    document: fixture.document,
    fetch: fetchImplementation,
    FormData: class FakeFormData {
      constructor(element) {
        this.values = element?.formValues ?? new Map();
      }

      get(name) {
        return this.values.get(name) ?? null;
      }

      getAll(name) {
        const value = this.values.get(name);
        if (value === undefined || value === null) return [];
        return Array.isArray(value) ? value : [value];
      }

      [Symbol.iterator]() {
        return this.values[Symbol.iterator]();
      }
    },
    CustomEvent: class CustomEvent {
      constructor(type, init) {
        this.type = type;
        this.detail = init?.detail;
      }
    },
    setInterval,
    clearInterval,
    console,
  });
  await new Promise((resolve) => setTimeout(resolve, 0));
};

const response = (status, payload) => ({ status, ok: status >= 200 && status < 300, json: async () => payload });

const deferred = () => {
  let resolve;
  const promise = new Promise((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
};

const nextTurn = () => new Promise((resolve) => setTimeout(resolve, 0));

const testRefreshShowsAndClearsBusyState = async () => {
  const fixture = makeLiveDocument();
  const contexts = deferred();
  const fetchImplementation = async (path) => {
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (path === "/api/contexts") return contexts.promise;
    return response(200, []);
  };
  const loading = loadLive(fixture, fetchImplementation);
  await nextTurn();
  assert.equal(fixture.workspaceLive.getAttribute("aria-busy"), "true");
  assert.equal(fixture.notice.textContent, "워크스페이스를 새로고침하는 중…");
  contexts.resolve(response(200, []));
  await loading;
  await nextTurn();
  assert.equal(fixture.workspaceLive.getAttribute("aria-busy"), "false");
  assert.equal(fixture.notice.textContent, "워크스페이스에 연결되었습니다.");
  assert.equal(fixture.entryScreen.hidden, true);
  assert.equal(fixture.workspaceLive.hidden, false);
  assert.equal(fixture.skipLink.getAttribute("href"), "#workspace-content");
};

const testLoginShowsAndClearsActionBusyState = async () => {
  const fixture = makeLiveDocument();
  const login = deferred();
  const fetchImplementation = async (path) => {
    if (path === "/api/auth/session") return response(401, { detail: "authentication required" });
    if (path === "/api/auth/login") return login.promise;
    if (["/api/contexts", "/api/assets", "/api/campaigns", "/api/queue", "/api/sessions", "/api/chat/commands"].includes(path)) {
      return response(200, []);
    }
    throw new Error(`unexpected path: ${path}`);
  };
  await loadLive(fixture, fetchImplementation);
  const submitting = fixture.memberForm.submit();
  await nextTurn();
  assert.equal(fixture.memberForm.getAttribute("aria-busy"), "true");
  assert.equal(fixture.memberForm.disabled, false);
  assert.equal(fixture.notice.textContent, "워크스페이스에 연결하는 중…");
  login.resolve(response(200, { display_name: "Ada" }));
  await submitting;
  await nextTurn();
  assert.equal(fixture.memberForm.getAttribute("aria-busy"), "false");
  assert.equal(fixture.notice.textContent, "워크스페이스에 연결되었습니다.");
};

const testRefreshFailureDoesNotSignOut = async () => {
  const fixture = makeLiveDocument();
  const fetchImplementation = async (path) => {
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    return response(503, { detail: "queue unavailable" });
  };
  await loadLive(fixture, fetchImplementation);
  assert.equal(fixture.memberFields.hidden, true);
  assert.equal(fixture.memberConnected.hidden, false);
  assert.equal(fixture.memberLabel.textContent, "로컬 연결됨");
};

const testAuthFailureSignsOut = async () => {
  const fixture = makeLiveDocument();
  await loadLive(fixture, async () => response(401, { detail: "authentication required" }));
  assert.equal(fixture.memberFields.hidden, false);
  assert.equal(fixture.memberConnected.hidden, true);
  assert.equal(fixture.memberLabel.textContent, "입장 전");
  assert.equal(fixture.entryScreen.hidden, false);
  assert.equal(fixture.workspaceLive.hidden, true);
  assert.equal(fixture.skipLink.getAttribute("href"), "#entry-title");
};

const testLoginValidationExplainsTheFirstMissingValue = async () => {
  const fixture = makeLiveDocument();
  fixture.memberForm.formValues.set("workspace-id", "");
  await loadLive(fixture, async (path) => {
    if (path === "/api/auth/session") return response(401, { detail: "authentication required" });
    throw new Error(`unexpected path: ${path}`);
  });
  await fixture.memberForm.submit();
  assert.equal(fixture.memberFeedback.textContent, "워크스페이스 ID 값을 입력해 주세요.");
};

const testLoginValidationReportsEveryMissingValue = async () => {
  const fixture = makeLiveDocument();
  fixture.memberForm.formValues.forEach((_value, name) => fixture.memberForm.formValues.set(name, ""));
  await loadLive(fixture, async (path) => {
    if (path === "/api/auth/session") return response(401, { detail: "authentication required" });
    throw new Error(`unexpected path: ${path}`);
  });
  await fixture.memberForm.submit();
  assert.equal(
    fixture.memberFeedback.textContent,
    "워크스페이스 ID, 멤버 ID, 워크스페이스 코드, 멤버 코드 값을 입력해 주세요.",
  );
};

const testQueueEmptySurfaceExplainsTheNextAction = async () => {
  const fixture = makeLiveDocument();
  await loadLive(fixture, async (path) => {
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (["/api/contexts", "/api/assets", "/api/campaigns", "/api/queue", "/api/sessions", "/api/chat/commands"].includes(path)) {
      return response(200, []);
    }
    throw new Error(`unexpected path: ${path}`);
  });
  assert.equal(fixture.queueEmpty.hidden, false);
  assert.equal(fixture.queueSummary.hidden, false);
};

const testAsyncSubmitHandlersRetainTheirFormTargets = async () => {
  const fixture = makeLiveDocument();
  const calls = [];
  const fetchImplementation = async (path) => {
    calls.push(path);
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (["/api/contexts", "/api/assets", "/api/campaigns", "/api/queue", "/api/sessions", "/api/generation", "/api/chat/commands"].includes(path)) {
      return response(200, []);
    }
    if (path === "/api/chat") {
      return response(200, { session_id: "session-1", history: [] });
    }
    throw new Error(`unexpected path: ${path}`);
  };
  await loadLive(fixture, fetchImplementation);
  await fixture.contextForm.submit();
  await fixture.captureForm.submit();
  await fixture.chatForm.submit();
  assert.equal(fixture.contextForm.resetCount, 1);
  assert.equal(fixture.captureForm.resetCount, 1);
  assert.equal(fixture.chatField.value, "");
  assert.ok(calls.includes("/api/generation"));
};

const testChatCommandOutputRendersTuiSessionOptions = async () => {
  const fixture = makeLiveDocument();
  const fetchImplementation = async (path) => {
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (["/api/contexts", "/api/assets", "/api/campaigns", "/api/queue", "/api/sessions", "/api/chat/commands"].includes(path)) {
      return response(200, []);
    }
    if (path === "/api/chat") {
      return response(200, {
        session_id: "session-1",
        history: [],
        replace_history: false,
        events: [{ role: "system", content: "이전 세션을 선택하세요" }],
        sessions: [{ session_id: "session-2", title: "이전 대화", revision: 1, created_at: 1, updated_at: 2 }],
        models: [],
        settings: { model: "gpt-5.5", reasoning: null, permission_mode: "yolo" },
      });
    }
    if (path === "/api/chat/approval") return response(200, null);
    throw new Error(`unexpected path: ${path}`);
  };
  await loadLive(fixture, fetchImplementation);
  await fixture.chatForm.submit();
  assert.equal(fixture.chatField.value, "");
  assert.equal(fixture.chatCommandOutput.hidden, false);
  assert.equal(fixture.chatCommandEvents.children[0].textContent, "이전 세션을 선택하세요");
  assert.equal(fixture.chatCommandOptions.children.length, 1);
};

const testCaptureDialogCancelClosesTheDialog = async () => {
  const fixture = makeLiveDocument();
  fixture.captureDialog.open = true;
  await loadLive(fixture, async () => response(401, { detail: "authentication required" }));
  await fixture.captureCancel.click();
  assert.equal(fixture.captureDialog.open, false);
};

const testCampaignRequiresPersonaAndPromotion = async () => {
  const fixture = makeLiveDocument();
  fixture.captureForm.formValues.set("bundle-json", "");
  fixture.captureForm.formValues.set("persona-context-id", "");
  fixture.captureForm.formValues.set("promotion-context-id", "");
  await loadLive(fixture, async () => response(401, { detail: "authentication required" }));
  await fixture.captureForm.submit();
  assert.equal(fixture.personaSelect.getAttribute("aria-invalid"), "true");
  assert.equal(fixture.promotionSelect.getAttribute("aria-invalid"), "true");
  assert.equal(fixture.notice.textContent, "페르소나와 홍보 소재를 선택해 주세요.");
};

const testMalformedCaptureFocusesTheInvalidInput = async () => {
  const fixture = makeLiveDocument();
  fixture.captureForm.formValues.set("bundle-json", "{bad");
  await loadLive(fixture, async () => response(401, { detail: "authentication required" }));
  await fixture.captureForm.submit();
  assert.equal(fixture.captureDetails.open, true);
  assert.equal(fixture.bundleInput.getAttribute("aria-invalid"), "true");
  assert.ok(fixture.bundleInput.events.includes("focus"));
  assert.equal(fixture.captureSubmit.disabled, true);
  assert.equal(fixture.notice.textContent, "컨텍스트 JSON 형식을 확인해 주세요.");
};

const testSavedContextCampaignStartsContinuousProduction = async () => {
  const fixture = makeLiveDocument();
  fixture.captureForm.formValues.set("bundle-json", "");
  fixture.captureForm.formValues.set("reference-asset-ids", ["asset-1", "asset-2"]);
  const calls = [];
  await loadLive(fixture, async (path, options = {}) => {
    calls.push([path, options]);
    if (path === "/api/auth/session") return response(401, { detail: "authentication required" });
    if (path === "/api/campaigns" && options.method === "POST") {
      return response(201, { campaign_id: "campaign-1" });
    }
    if (path === "/api/campaigns" || path === "/api/queue") return response(200, []);
    throw new Error(`unexpected path: ${path}`);
  });
  await fixture.captureForm.submit();
  const submitted = calls.find(([path, options]) => path === "/api/campaigns" && options.method === "POST");
  assert.ok(submitted);
  const payload = JSON.parse(submitted[1].body);
  assert.equal(payload.persona_context_id, "persona-1");
  assert.equal(payload.promotion_context_id, "promotion-1");
  assert.deepEqual(payload.reference_asset_ids, ["asset-1", "asset-2"]);
  assert.match(payload.reference_date, /Z$/);
  assert.equal(payload.device.udid, "E1FB798D-79E6-4B25-A987-D298A4FD122A");
  assert.equal(payload.device.platform_version, "26.5");
  assert.equal(payload.device.device_name, "iPhone 17 Pro");
  assert.equal(payload.variation_count, null);
  assert.equal(fixture.captureDialog.open, false);
  assert.equal(fixture.notice.textContent, "반복 캠페인을 시작했습니다.");
};

const testOverviewNavigationResetsTheSelectedTab = async () => {
  const fixture = makeNavigationDocument();
  await loadNavigation(fixture);
  fixture.tabs[0].setAttribute("aria-selected", "false");
  fixture.tabs[1].setAttribute("aria-selected", "true");
  await fixture.document.getElementById("nav-overview").click();
  assert.equal(fixture.tabs[0].getAttribute("aria-selected"), "false");
  assert.equal(fixture.tabs[1].getAttribute("aria-selected"), "false");
  assert.equal(fixture.tabs[2].getAttribute("aria-selected"), "true");
  assert.equal(fixture.panels[2].hidden, false);
  assert.equal(fixture.document.getElementById("nav-overview").getAttribute("aria-current"), "page");
};

const testChatNavigationSelectsTheChatPanel = async () => {
  const fixture = makeNavigationDocument();
  await loadNavigation(fixture);
  await fixture.document.getElementById("nav-chat").click();
  assert.equal(fixture.tabs[3].getAttribute("aria-selected"), "true");
  assert.equal(fixture.panels[3].hidden, false);
  assert.equal(fixture.panels[0].hidden, true);
  assert.equal(fixture.document.getElementById("nav-chat").getAttribute("aria-current"), "page");
};

await testAttachContextFocusesBeforeScroll();
await testCommandAndReviewHaveDistinctDestinations();
await testRefreshFailureDoesNotSignOut();
await testAuthFailureSignsOut();
await testLoginValidationExplainsTheFirstMissingValue();
await testLoginValidationReportsEveryMissingValue();
await testQueueEmptySurfaceExplainsTheNextAction();
await testAsyncSubmitHandlersRetainTheirFormTargets();
await testChatCommandOutputRendersTuiSessionOptions();
await testCaptureDialogCancelClosesTheDialog();
await testCampaignRequiresPersonaAndPromotion();
await testMalformedCaptureFocusesTheInvalidInput();
await testSavedContextCampaignStartsContinuousProduction();
await testOverviewNavigationResetsTheSelectedTab();
await testChatNavigationSelectsTheChatPanel();
await testRefreshShowsAndClearsBusyState();
await testLoginShowsAndClearsActionBusyState();
console.log("workspace static behavior: 17 passed");
