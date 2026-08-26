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

  async keydown(key) {
    const listener = this.listeners.get("keydown");
    if (listener) await listener({ currentTarget: this, key, preventDefault() {} });
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
  const tabs = ["candidates", "review"].map((name) => new FakeElement(`tab-${name}`, { tab: name, role: "tab" }));
  tabs[0].setAttribute("aria-selected", "true");
  tabs[1].setAttribute("aria-selected", "false");
  const panels = [
    new FakeElement("candidates", { panel: "candidates", role: "tabpanel" }),
    new FakeElement("reviews", { panel: "review", role: "tabpanel" }),
  ];
  const commandButton = new FakeElement("command", { action: "open-command" });
  const reviewButton = new FakeElement("open-review", { action: "open-review" });
  const commandDialog = new FakeElement("command-dialog", { commandDialog: true });
  const openReviewCommand = new FakeElement("command-open-review", { commandAction: "open-review" });
  commandDialog.children = [openReviewCommand];
  return {
    document: new FakeDocument([
      ...tabs,
      ...panels,
      commandButton,
      reviewButton,
      commandDialog,
      openReviewCommand,
    ]),
    tabs,
    panels,
    commandButton,
    reviewButton,
    commandDialog,
    openReviewCommand,
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

const testCandidatesIsTheDefaultTab = async () => {
  const fixture = makeNavigationDocument();
  await loadNavigation(fixture);
  assert.equal(fixture.tabs.length, 2);
  assert.equal(fixture.tabs[0].getAttribute("aria-selected"), "true");
  assert.equal(fixture.tabs[0].tabIndex, 0);
  assert.equal(fixture.tabs[1].tabIndex, -1);
  assert.equal(fixture.panels[0].hidden, false);
  assert.equal(fixture.panels[1].hidden, true);
};

const testArrowKeysMoveBetweenTheTwoTabs = async () => {
  const fixture = makeNavigationDocument();
  await loadNavigation(fixture);
  await fixture.tabs[0].keydown("ArrowRight");
  assert.equal(fixture.tabs[1].getAttribute("aria-selected"), "true");
  assert.equal(fixture.panels[1].hidden, false);
  assert.equal(fixture.panels[0].hidden, true);
  await fixture.tabs[1].keydown("ArrowRight");
  assert.equal(fixture.tabs[0].getAttribute("aria-selected"), "true");
  assert.equal(fixture.panels[0].hidden, false);
};

const testCommandMenuOnlyLeadsToApproval = async () => {
  const fixture = makeNavigationDocument();
  await loadNavigation(fixture);
  await fixture.commandButton.click();
  assert.equal(fixture.commandDialog.open, true);
  assert.equal(fixture.document.activeElement, fixture.openReviewCommand);
  await fixture.openReviewCommand.click();
  assert.equal(fixture.commandDialog.open, false);
  assert.equal(fixture.panels[1].hidden, false);
  assert.deepEqual(fixture.panels[1].events, ["focus", "scroll"]);
};

const testOpenReviewButtonRevealsTheApprovalTab = async () => {
  const fixture = makeNavigationDocument();
  await loadNavigation(fixture);
  await fixture.reviewButton.click();
  assert.equal(fixture.commandDialog.open, false);
  assert.equal(fixture.panels[1].hidden, false);
  assert.deepEqual(fixture.panels[1].events, ["focus", "scroll"]);
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
  memberForm.formValues = new Map([
    ["access-token", "workspace-1%member-1%workspace-code%member-code"],
  ]);
  const accessTokenField = new FakeElement("workspace-access-id");
  const inviteButton = new FakeElement("invite-button");
  const inviteDialog = new FakeElement("invite-dialog");
  const inviteForm = new FakeElement("invite-form");
  const inviteName = new FakeElement("invite-name");
  const inviteFeedback = new FakeElement("invite-feedback");
  const inviteResult = new FakeElement("invite-result");
  const inviteToken = new FakeElement("invite-token");
  const inviteCopy = new FakeElement("invite-copy");
  const inviteCancel = new FakeElement("invite-cancel");
  inviteCancel.matches = (selector) => selector === "[value='cancel']";
  inviteDialog.children = [inviteCancel];
  inviteForm.formValues = new Map([["display-name", "Grace"]]);
  const candidateForm = new FakeElement("candidate-form");
  candidateForm.formValues = new Map([
    ["topic", "  시험기간 일정 관리 — 잠금화면 데모  "],
    ["country", " jp "],
    ["caption", "시험 기간엔 잠금화면부터 바꾼다"],
    ["hypothesis", "1인칭 감탄이 저장률을 올린다"],
    ["refs-used", "ref-a, ref-b"],
    ["principles-applied", "1, 4"],
    ["shooting-order", "- 책상 위 아이폰"],
    ["trace-items", "  09:00 통계학 2교시  \n\n13:00 스터디\n"],
    ["device-time", "07:20"],
    ["background-subject", "scenery"],
    ["background-mood", "늦은 밤 책상 위 스탠드 불빛"],
  ]);
  const scheduleField = new FakeElement("candidate-schedule");
  const deviceTimeField = new FakeElement("candidate-device-time");
  const backgroundMoodField = new FakeElement("candidate-background-mood");
  const candidateFeedback = new FakeElement("candidate-feedback");
  candidateFeedback.hidden = true;
  const candidateList = new FakeElement("candidate-list");
  const candidateEmpty = new FakeElement("candidate-empty");
  const candidateCount = new FakeElement("candidate-count");
  const approvalList = new FakeElement("approval-list");
  const approvalEmpty = new FakeElement("approval-empty");
  const approvalCount = new FakeElement("approval-count");
  const imageList = new FakeElement("image-list");
  const imageEmpty = new FakeElement("image-empty");
  const imageCount = new FakeElement("image-count");
  const countryField = new FakeElement("candidate-country");
  const topicField = new FakeElement("candidate-topic");
  const autogenButton = new FakeElement("autogen-button", { autogen: "" });
  autogenButton.textContent = "🤖 후보 자동 생성";
  const autogenFeedback = new FakeElement("autogen-feedback");
  autogenFeedback.hidden = true;
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
    ["[data-candidate-form]", candidateForm],
    ["[data-candidate-feedback]", candidateFeedback],
    ["[data-candidate-list]", candidateList],
    ["[data-candidate-empty]", candidateEmpty],
    ["[data-candidate-count]", candidateCount],
    ["[data-approval-list]", approvalList],
    ["[data-approval-empty]", approvalEmpty],
    ["[data-approval-count]", approvalCount],
    ["[data-image-list]", imageList],
    ["[data-image-empty]", imageEmpty],
    ["[data-image-count]", imageCount],
    ["[data-autogen-feedback]", autogenFeedback],
    ["#workspace-access-id", accessTokenField],
    ["[data-action='open-invite']", inviteButton],
    ["[data-invite-dialog]", inviteDialog],
    ["[data-invite-form]", inviteForm],
    ["[data-invite-name]", inviteName],
    ["[data-invite-feedback]", inviteFeedback],
    ["[data-invite-result]", inviteResult],
    ["[data-invite-token]", inviteToken],
    ["[data-invite-copy]", inviteCopy],
  ]);
  const selectorGroups = new Map([["[data-autogen]", [autogenButton]]]);
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
    candidateForm,
    candidateFeedback,
    candidateList,
    candidateEmpty,
    candidateCount,
    approvalList,
    approvalEmpty,
    approvalCount,
    imageList,
    imageEmpty,
    imageCount,
    countryField,
    topicField,
    autogenButton,
    autogenFeedback,
    accessTokenField,
    inviteButton,
    inviteDialog,
    inviteForm,
    inviteName,
    inviteFeedback,
    inviteResult,
    inviteToken,
    inviteCopy,
    inviteCancel,
    scheduleField,
    deviceTimeField,
    backgroundMoodField,
  ]);
  document.querySelector = (selector) => selectors.get(selector) ?? null;
  document.querySelectorAll = (selector) => selectorGroups.get(selector) ?? [];
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
    candidateForm,
    candidateFeedback,
    candidateList,
    candidateEmpty,
    candidateCount,
    approvalList,
    approvalEmpty,
    approvalCount,
    imageList,
    imageEmpty,
    imageCount,
    countryField,
    topicField,
    autogenButton,
    autogenFeedback,
    accessTokenField,
    inviteButton,
    inviteDialog,
    inviteForm,
    inviteName,
    inviteFeedback,
    inviteResult,
    inviteToken,
    inviteCopy,
    inviteCancel,
    scheduleField,
    deviceTimeField,
    backgroundMoodField,
  };
};

const loadLive = async (fixture, fetchImplementation) => {
  runInNewContext(liveSource, {
    document: fixture.document,
    fetch: fetchImplementation,
    Headers,
    window: {
      clearTimeout,
      // The capture poll reschedules itself forever; an unreferenced timer still fires
      // while a test is awaiting, but never holds the process open after the last one.
      setTimeout: (handler, delay) => {
        const timer = setTimeout(handler, delay);
        timer.unref?.();
        return timer;
      },
      confirm: () => true,
      localStorage: { getItem: () => null, setItem: () => {} },
    },
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

const findByText = (node, text) => {
  if (node?.textContent === text) return node;
  for (const child of node?.children ?? []) {
    const found = findByText(child, text);
    if (found) return found;
  }
  return null;
};

const findJourney = (node) => {
  if (node?.className === "journey") return node;
  for (const child of node?.children ?? []) {
    const found = findJourney(child);
    if (found) return found;
  }
  return null;
};

const findByClassName = (node, className) => {
  if (node?.className === className) return node;
  for (const child of node?.children ?? []) {
    const found = findByClassName(child, className);
    if (found) return found;
  }
  return null;
};

const findProvenance = (node) => findByClassName(node, "advanced-input provenance");

const provenanceTexts = (node, className) => {
  const found = [];
  const walk = (current) => {
    if (current?.className === className) found.push(current.textContent);
    for (const child of current?.children ?? []) walk(child);
  };
  walk(node);
  return found;
};

const provenance = (overrides = {}) => ({
  documents: [
    { relative_path: "core/PRINCIPLES-KR.md", size_bytes: 8_806 },
    { relative_path: "references/KR/INDEX.md", size_bytes: 1_240 },
  ],
  model: "gpt-5.5",
  instruction_chars: 41_238,
  generated_at: 1_770_000_000,
  ...overrides,
});

const candidate = (overrides) => ({
  candidate_id: "candidate-1",
  source: "manual",
  country: "JP",
  topic: "시험기간 일정 관리 — 잠금화면 데모",
  caption: "첫 줄\n둘째 줄",
  hypothesis: "가설",
  refs_used: ["ref-a"],
  principles_applied: [2],
  shooting_order: "- 아이폰 잠금화면, 기기 시각 07:20",
  image_inputs: {
    trace_items: ["09:00 통계학 2교시", "13:00 스터디"],
    device_time: "07:20",
    background_subject: "scenery",
    background_mood: "늦은 밤 책상 위 스탠드 불빛",
    language: "ko",
  },
  image_sha256: null,
  ai_verdict: null,
  image_path: null,
  generation_provenance: null,
  status: "awaiting_review",
  review_note: null,
  revision: 1,
  created_at: 1_770_000_000,
  updated_at: 1_770_000_000,
  ...overrides,
});

const loadCandidates = async (fixture, records) => {
  await loadLive(fixture, async (path) => {
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (path === "/api/candidates") return response(200, records);
    throw new Error(`unexpected path: ${path}`);
  });
};

const signedOut = async (path) => {
  if (path === "/api/auth/session") return response(401, { detail: "authentication required" });
  throw new Error(`unexpected path: ${path}`);
};

const testWorkspaceLoadOnlyReadsCandidates = async () => {
  const fixture = makeLiveDocument();
  const calls = [];
  await loadLive(fixture, async (path) => {
    calls.push(path);
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (path === "/api/candidates") return response(200, []);
    throw new Error(`unexpected path: ${path}`);
  });
  assert.deepEqual(calls, ["/api/auth/session", "/api/candidates"]);
  assert.equal(fixture.notice.textContent, "워크스페이스에 연결되었습니다.");
  assert.equal(fixture.candidateEmpty.hidden, false);
  assert.equal(fixture.approvalEmpty.hidden, false);
};

const testRefreshShowsAndClearsBusyState = async () => {
  const fixture = makeLiveDocument();
  const candidates = deferred();
  const fetchImplementation = async (path) => {
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (path === "/api/candidates") return candidates.promise;
    throw new Error(`unexpected path: ${path}`);
  };
  const loading = loadLive(fixture, fetchImplementation);
  await nextTurn();
  assert.equal(fixture.workspaceLive.getAttribute("aria-busy"), "true");
  assert.equal(fixture.notice.textContent, "워크스페이스를 새로고침하는 중…");
  candidates.resolve(response(200, []));
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
    if (path === "/api/candidates") return response(200, []);
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
    return response(503, { detail: "candidate store unavailable" });
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

const testLoginValidationExplainsTheMissingAccessId = async () => {
  const fixture = makeLiveDocument();
  fixture.memberForm.formValues.set("access-token", "");
  await loadLive(fixture, signedOut);
  await fixture.memberForm.submit();
  assert.equal(fixture.accessTokenField.getAttribute("aria-invalid"), "true");
  assert.equal(fixture.memberFeedback.textContent, "워크스페이스 접속 ID 값을 입력해 주세요.");
};

const testLoginParsesCompositeAccessId = async () => {
  const fixture = makeLiveDocument();
  fixture.memberForm.formValues.set(
    "access-token",
    "Workspace access ID (shown once; not written to logs): workspace-1%member-1%workspace-code%member-code",
  );
  const calls = [];
  await loadLive(fixture, async (path, options = {}) => {
    calls.push([path, options]);
    if (path === "/api/auth/session") return response(401, { detail: "authentication required" });
    if (path === "/api/auth/login") return response(200, { display_name: "Ada" });
    if (path === "/api/candidates") return response(200, []);
    throw new Error(`unexpected path: ${path}`);
  });
  await fixture.memberForm.submit();
  const login = calls.find(([path]) => path === "/api/auth/login");
  assert.ok(login);
  assert.deepEqual(JSON.parse(login[1].body), {
    workspace_id: "workspace-1",
    member_id: "member-1",
    workspace_code: "workspace-code",
    member_code: "member-code",
  });
};

const testLoginRejectsMalformedCompositeAccessId = async () => {
  const fixture = makeLiveDocument();
  fixture.memberForm.formValues.set("access-token", "workspace-1%member-1");
  await loadLive(fixture, signedOut);
  await fixture.memberForm.submit();
  assert.equal(fixture.accessTokenField.getAttribute("aria-invalid"), "true");
  assert.equal(fixture.memberFeedback.textContent, "워크스페이스 접속 ID 형식을 확인해 주세요.");
};

const testMemberAccessIdUsesMemberLoginRoute = async () => {
  const fixture = makeLiveDocument();
  fixture.memberForm.formValues.set("access-token", "workspace-1%member-1%member-code");
  const calls = [];
  await loadLive(fixture, async (path, options = {}) => {
    calls.push([path, options]);
    if (path === "/api/auth/session") return response(401, { detail: "authentication required" });
    if (path === "/api/auth/member-login") return response(200, { display_name: "Grace", is_admin: false });
    if (path === "/api/candidates") return response(200, []);
    throw new Error(`unexpected path: ${path}`);
  });
  await fixture.memberForm.submit();
  const login = calls.find(([path]) => path === "/api/auth/member-login");
  assert.ok(login);
  assert.deepEqual(JSON.parse(login[1].body), {
    workspace_id: "workspace-1",
    member_id: "member-1",
    member_code: "member-code",
  });
  assert.equal(fixture.inviteButton.hidden, true);
};

const testOwnerCanInviteMemberAndSeeOneTimeAccessId = async () => {
  const fixture = makeLiveDocument();
  const calls = [];
  await loadLive(fixture, async (path, options = {}) => {
    calls.push([path, options]);
    if (path === "/api/auth/session") return response(200, { display_name: "Owner", is_admin: true });
    if (path === "/api/members/invite") return response(201, { member_access_id: "workspace-1%member-2%member-code" });
    if (path === "/api/candidates") return response(200, []);
    throw new Error(`unexpected path: ${path}`);
  });
  assert.equal(fixture.inviteButton.hidden, false);
  await fixture.inviteButton.click();
  assert.equal(fixture.inviteDialog.open, true);
  await fixture.inviteForm.submit();
  assert.equal(fixture.inviteResult.hidden, false);
  assert.equal(fixture.inviteToken.textContent, "workspace-1%member-2%member-code");
  const invite = calls.find(([path]) => path === "/api/members/invite");
  assert.ok(invite);
  assert.deepEqual(JSON.parse(invite[1].body), { display_name: "Grace" });
};

const testInviteValidationAndFailureStayNearby = async () => {
  const fixture = makeLiveDocument();
  fixture.inviteForm.formValues.set("display-name", "");
  const owner = async (path) => {
    if (path === "/api/auth/session") return response(200, { display_name: "Owner", is_admin: true });
    if (path === "/api/candidates") return response(200, []);
    throw new Error(`unexpected path: ${path}`);
  };
  await loadLive(fixture, owner);
  await fixture.inviteButton.click();
  await fixture.inviteForm.submit();
  assert.equal(fixture.inviteName.getAttribute("aria-invalid"), "true");
  assert.equal(fixture.inviteFeedback.textContent, "팀원 이름을 입력해 주세요.");
  const failing = async (path) => {
    if (path === "/api/auth/session") return response(200, { display_name: "Owner", is_admin: true });
    if (path === "/api/members/invite") return response(403, { detail: "admin access required" });
    if (path === "/api/candidates") return response(200, []);
    throw new Error(`unexpected path: ${path}`);
  };
  const failureFixture = makeLiveDocument();
  await loadLive(failureFixture, failing);
  await failureFixture.inviteButton.click();
  await failureFixture.inviteForm.submit();
  assert.equal(failureFixture.inviteFeedback.hidden, false);
};

const testAutogenGeneratesAndRefreshesTheList = async () => {
  const fixture = makeLiveDocument();
  const calls = [];
  const generated = [
    candidate({}),
    candidate({ candidate_id: "candidate-2", topic: "두 번째 주제" }),
    candidate({ candidate_id: "candidate-3", topic: "세 번째 주제" }),
  ];
  let stored = [];
  await loadLive(fixture, async (path, options = {}) => {
    calls.push([path, options.method ?? "GET"]);
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (path === "/api/candidates/generate") {
      stored = generated;
      return response(201, generated);
    }
    if (path === "/api/candidates") return response(200, stored);
    throw new Error(`unexpected path: ${path}`);
  });
  await fixture.autogenButton.click();
  assert.ok(calls.some(([path, method]) => path === "/api/candidates/generate" && method === "POST"));
  assert.equal(fixture.notice.textContent, "후보 3개가 등록되었습니다.");
  assert.equal(fixture.candidateList.children.length, 3);
  assert.equal(fixture.approvalList.children.length, 3);
  assert.equal(fixture.autogenButton.disabled, false);
  assert.equal(fixture.autogenButton.textContent, "🤖 후보 자동 생성");
  assert.equal(fixture.autogenFeedback.hidden, true);
};

const testAutogenShowsTheServerMessageVerbatim = async () => {
  const fixture = makeLiveDocument();
  const detail = "context 폴더를 찾을 수 없습니다 (경로: /tmp/context) — trace 폴더에서 서버를 실행했는지 확인하세요.";
  await loadLive(fixture, async (path) => {
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (path === "/api/candidates/generate") return response(409, { detail });
    if (path === "/api/candidates") return response(200, []);
    throw new Error(`unexpected path: ${path}`);
  });
  await fixture.autogenButton.click();
  assert.equal(fixture.autogenFeedback.hidden, false);
  assert.equal(fixture.autogenFeedback.textContent, detail);
  assert.equal(fixture.notice.textContent, detail);
  assert.equal(fixture.autogenButton.disabled, false);
  assert.equal(fixture.autogenButton.textContent, "🤖 후보 자동 생성");
};

const testManualCandidateSubmitsParsedListFields = async () => {
  const fixture = makeLiveDocument();
  const calls = [];
  await loadLive(fixture, async (path, options = {}) => {
    calls.push([path, options]);
    if (path === "/api/auth/session") return response(401, { detail: "authentication required" });
    if (path === "/api/candidates" && options.method === "POST") {
      return response(201, { candidate_id: "candidate-1" });
    }
    if (path === "/api/candidates") return response(200, []);
    throw new Error(`unexpected path: ${path}`);
  });
  await fixture.candidateForm.submit();
  const submitted = calls.find(([path, options]) => path === "/api/candidates" && options.method === "POST");
  assert.ok(submitted);
  const payload = JSON.parse(submitted[1].body);
  assert.equal(payload.topic, "시험기간 일정 관리 — 잠금화면 데모");
  assert.equal(payload.country, "JP");
  assert.equal(payload.caption, "시험 기간엔 잠금화면부터 바꾼다");
  assert.deepEqual(payload.refs_used, ["ref-a", "ref-b"]);
  assert.deepEqual(payload.principles_applied, [1, 4]);
  assert.equal(payload.shooting_order, "- 책상 위 아이폰");
  assert.deepEqual(payload.image_inputs, {
    trace_items: ["09:00 통계학 2교시", "13:00 스터디"],
    device_time: "07:20",
    background_subject: "scenery",
    background_mood: "늦은 밤 책상 위 스탠드 불빛",
    language: "ja",
  });
  assert.equal(fixture.candidateForm.resetCount, 1);
  assert.equal(fixture.notice.textContent, "후보를 등록했습니다.");
};

const testManualCandidateValidationRequiresATopic = async () => {
  const fixture = makeLiveDocument();
  fixture.candidateForm.formValues.set("topic", "   ");
  await loadLive(fixture, signedOut);
  await fixture.candidateForm.submit();
  assert.equal(fixture.candidateFeedback.hidden, false);
  assert.equal(fixture.candidateFeedback.textContent, "주제/컨셉을 입력해 주세요.");
  assert.equal(fixture.topicField.getAttribute("aria-invalid"), "true");
  assert.ok(fixture.topicField.events.includes("focus"));
};

const testManualCandidateValidationRequiresASchedule = async () => {
  const fixture = makeLiveDocument();
  fixture.candidateForm.formValues.set("trace-items", "   \n  ");
  await loadLive(fixture, signedOut);
  await fixture.candidateForm.submit();
  assert.equal(fixture.scheduleField.getAttribute("aria-invalid"), "true");
  assert.equal(fixture.candidateFeedback.textContent, "잠금화면 일정을 한 줄에 하나씩 입력해 주세요.");
};

const testManualCandidateValidationCapsTheSchedule = async () => {
  const fixture = makeLiveDocument();
  fixture.candidateForm.formValues.set(
    "trace-items",
    Array.from({ length: 9 }, (_value, index) => `0${index}:00 일정`).join("\n"),
  );
  await loadLive(fixture, signedOut);
  await fixture.candidateForm.submit();
  assert.equal(fixture.scheduleField.getAttribute("aria-invalid"), "true");
  assert.equal(
    fixture.candidateFeedback.textContent,
    "잠금화면 일정은 최대 8줄까지 입력할 수 있습니다.",
  );
};

const testManualCandidateValidationExplainsTheDeviceTime = async () => {
  const fixture = makeLiveDocument();
  fixture.candidateForm.formValues.set("device-time", "7시 20분");
  await loadLive(fixture, signedOut);
  await fixture.candidateForm.submit();
  assert.equal(fixture.deviceTimeField.getAttribute("aria-invalid"), "true");
  assert.equal(
    fixture.candidateFeedback.textContent,
    "기기 시각은 HH:MM 형식으로 입력해 주세요. 예: 07:20",
  );
};

const testTopicLeadsTheRowAndTheApprovalCard = async () => {
  const fixture = makeLiveDocument();
  await loadCandidates(fixture, [candidate({})]);
  const row = fixture.candidateList.children[0];
  const rowContent = row.children.find((child) => child.className === "candidate-row__content");
  assert.equal(rowContent.children[0].textContent, "시험기간 일정 관리 — 잠금화면 데모");
  assert.equal(rowContent.children[0].id, "strong");
  assert.equal(rowContent.children[1].className, "candidate-row__caption");
  assert.equal(rowContent.children[1].textContent, "첫 줄");

  const card = fixture.approvalList.children[0];
  const body = card.children.find((child) => child.className === "approval-card__body");
  const text = body.children[0];
  assert.equal(text.className, "approval-card__text");
  assert.equal(text.children[0].textContent, "주제/컨셉");
  assert.equal(text.children[1].className, "approval-card__topic");
  assert.equal(text.children[1].textContent, "시험기간 일정 관리 — 잠금화면 데모");
  assert.equal(text.children[2].className, "approval-card__caption");
  assert.equal(text.children[2].textContent, "첫 줄\n둘째 줄");
};

const testManualCandidateValidationExplainsTheCountryCode = async () => {
  const fixture = makeLiveDocument();
  fixture.candidateForm.formValues.set("country", "japan");
  await loadLive(fixture, signedOut);
  await fixture.candidateForm.submit();
  assert.equal(fixture.candidateFeedback.hidden, false);
  assert.equal(fixture.candidateFeedback.textContent, "국가는 두 자리 국가 코드로 입력해 주세요. 예: JP");
  assert.equal(fixture.countryField.getAttribute("aria-invalid"), "true");
  assert.ok(fixture.countryField.events.includes("focus"));
};

const testOnlyAwaitingCaptionsFillTheApprovalGate = async () => {
  const fixture = makeLiveDocument();
  await loadCandidates(fixture, [
    candidate({}),
    candidate({
      candidate_id: "candidate-2",
      source: "auto",
      country: "KR",
      caption: "캡션이 승인된 후보",
      status: "caption_approved",
      revision: 2,
      created_at: 1_770_000_001,
      updated_at: 1_770_000_002,
    }),
  ]);
  assert.equal(fixture.candidateList.children.length, 2);
  assert.equal(fixture.candidateEmpty.hidden, true);
  assert.equal(fixture.candidateCount.textContent, "후보 2개");
  assert.equal(fixture.approvalList.children.length, 1);
  assert.equal(fixture.approvalEmpty.hidden, true);
  assert.equal(fixture.approvalCount.textContent, "캡션·주제 검수 대기 1건");
};

const testJourneyIsVisibleOnRowsAndCards = async () => {
  const fixture = makeLiveDocument();
  await loadCandidates(fixture, [candidate({})]);
  const rowJourney = findJourney(fixture.candidateList.children[0]);
  const cardJourney = findJourney(fixture.approvalList.children[0]);
  for (const journey of [rowJourney, cardJourney]) {
    assert.ok(journey, "journey indicator is rendered");
    assert.deepEqual(
      journey.children.map((step) => step.textContent),
      ["① 캡션·주제 승인", "② 이미지 승인", "③ 제출"],
    );
    for (const step of journey.children) {
      assert.equal(step.title, undefined, "no step is marked as not yet connected");
      assert.ok(!step.className.includes("is-planned"));
    }
  }
};

const testJourneyPositionFollowsTheStatus = async () => {
  const cases = [
    ["awaiting_review", ["is-current", "", ""]],
    ["caption_approved", ["is-done", "is-current", ""]],
    ["image_awaiting_review", ["is-done", "is-current", ""]],
    ["rejected", ["is-rejected", "", ""]],
    ["submitted", ["is-done", "is-done", "is-done"]],
  ];
  for (const [status, expected] of cases) {
    const fixture = makeLiveDocument();
    await loadCandidates(fixture, [candidate({ status })]);
    const journey = findJourney(fixture.candidateList.children[0]);
    assert.ok(journey, `journey rendered for ${status}`);
    const states = journey.children.map((step) => {
      const marks = step.className.split(" ").filter((name) => name.startsWith("is-"));
      return marks.join(" ");
    });
    assert.deepEqual(states, expected, `journey state for ${status}`);
    assert.equal(
      journey.children.filter((step) => step.getAttribute("aria-current") === "step").length,
      expected.includes("is-current") ? 1 : 0,
    );
  }
};

const testImageStageSplitsCaptionAndImageWork = async () => {
  const fixture = makeLiveDocument();
  await loadCandidates(fixture, [
    candidate({}),
    candidate({ candidate_id: "candidate-2", status: "caption_approved", revision: 2 }),
    candidate({
      candidate_id: "candidate-3",
      status: "image_awaiting_review",
      revision: 3,
      image_path: "candidates/candidate-3/r2/outputs/final.png",
      image_sha256: "a".repeat(64),
    }),
    candidate({ candidate_id: "candidate-4", status: "submitted", revision: 4 }),
  ]);
  assert.equal(fixture.approvalList.children.length, 1);
  assert.equal(fixture.imageList.children.length, 2);
  assert.equal(fixture.imageEmpty.hidden, true);
  assert.equal(
    fixture.imageCount.textContent,
    "생성 가능 1건 · Mac 대기·실행 0건 · 실패 0건 · 검수 대기 1건",
  );
  assert.equal(fixture.candidateList.children.length, 4);
};

const testImageGenerationButtonRunsTheStage = async () => {
  const fixture = makeLiveDocument();
  const calls = [];
  let stored = [candidate({ status: "caption_approved", revision: 2 })];
  await loadLive(fixture, async (path, options = {}) => {
    calls.push([path, options.method ?? "GET"]);
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (path === "/api/candidates/candidate-1/generate-image") {
      stored = [
        candidate({
          status: "caption_approved",
          revision: 3,
          capture_state: "queued",
          capture_task_id: "task-1",
        }),
      ];
      return response(201, stored[0]);
    }
    if (path === "/api/candidates") return response(200, stored);
    throw new Error(`unexpected path: ${path}`);
  });
  const button = findByText(fixture.imageList.children[0], "Mac에서 이미지 생성");
  assert.ok(button, "the caption-approved card offers image generation");
  await button.click();
  assert.ok(calls.some(([path, method]) =>
    path === "/api/candidates/candidate-1/generate-image" && method === "POST"));
  assert.equal(fixture.notice.textContent, "Mac 캡처 Queue에 등록했습니다. 완료되면 이미지 검수 카드가 자동으로 갱신됩니다.");
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, "Mac에서 이미지 생성");
  assert.ok(findByText(fixture.imageList.children[0], "등록된 Mac worker가 Queue 작업을 가져가면 Appium 캡처가 시작됩니다. 완료되면 이 카드가 자동으로 갱신됩니다."));
};

const testImageGenerationFailureShowsTheServerMessage = async () => {
  const fixture = makeLiveDocument();
  const detail = "잠금화면 부품 이미지를 찾을 수 없습니다 (경로: /x) — trace 폴더에서 서버를 실행했는지 확인하세요.";
  await loadLive(fixture, async (path) => {
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (path === "/api/candidates/candidate-1/generate-image") return response(409, { detail });
    if (path === "/api/candidates") {
      return response(200, [candidate({ status: "caption_approved", revision: 2 })]);
    }
    throw new Error(`unexpected path: ${path}`);
  });
  const card = fixture.imageList.children[0];
  const button = findByText(card, "Mac에서 이미지 생성");
  await button.click();
  const feedback = card.children.find((child) => child.className === "candidate-feedback");
  assert.equal(feedback.hidden, false);
  assert.equal(feedback.textContent, detail);
  assert.equal(fixture.notice.textContent, detail);
  assert.equal(button.disabled, false);
};

const testImageApprovalPostsTheDecision = async () => {
  const fixture = makeLiveDocument();
  const calls = [];
  const composed = candidate({
    status: "image_awaiting_review",
    revision: 3,
    image_path: "candidates/candidate-1/r2/outputs/final.png",
    image_sha256: "c".repeat(64),
  });
  let stored = [composed];
  await loadLive(fixture, async (path, options = {}) => {
    calls.push([path, options]);
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (path === "/api/candidates/candidate-1/review-image") {
      stored = [candidate({ status: "submitted", revision: 4 })];
      return response(200, stored[0]);
    }
    if (path === "/api/candidates") return response(200, stored);
    throw new Error(`unexpected path: ${path}`);
  });
  await findByText(fixture.imageList.children[0], "이미지 승인 · 5점").click();
  const submitted = calls.find(([path]) => path === "/api/candidates/candidate-1/review-image");
  assert.ok(submitted);
  const payload = JSON.parse(submitted[1].body);
  assert.equal(payload.accepted, true);
  assert.equal(payload.expected_revision, 3);
  assert.equal(fixture.notice.textContent, "제출 준비가 끝났습니다.");
  assert.equal(fixture.imageList.children.length, 0);
  assert.equal(fixture.imageEmpty.hidden, false);
};

const testGenerationProvenanceIsShownOnRowsAndCards = async () => {
  const fixture = makeLiveDocument();
  await loadCandidates(fixture, [
    candidate({ source: "auto", generation_provenance: provenance() }),
  ]);
  const panels = [
    findProvenance(fixture.candidateList.children[0]),
    findProvenance(fixture.approvalList.children[0]),
  ];
  for (const panel of panels) {
    assert.ok(panel, "the generation provenance panel is rendered");
    assert.equal(panel.id, "details", "the panel stays collapsed until it is opened");
    assert.equal(panel.children[0].textContent, "🧠 생성 근거");
    assert.deepEqual(provenanceTexts(panel, "provenance__document mono"), [
      "context/core/PRINCIPLES-KR.md · 8.6KB",
      "context/references/KR/INDEX.md · 1.2KB",
    ]);
    assert.ok(findByText(panel, "읽은 문서 2개"), "the document count labels the list");
    const model = findByClassName(panel, "provenance__model mono");
    assert.ok(model.textContent.startsWith("gpt-5.5 · 지시문 41,238자 · "));
    assert.equal(
      model.textContent,
      `gpt-5.5 · 지시문 41,238자 · ${new Date(1_770_000_000 * 1000).toLocaleString("ko-KR")}`,
    );
    assert.ok(findByText(panel, "적용 원리·참조 레퍼런스는 위 배지에 표시됩니다"));
  }
};

const testCandidatesWithoutProvenanceSayWhyThePanelIsEmpty = async () => {
  const fixture = makeLiveDocument();
  await loadCandidates(fixture, [
    candidate({}),
    candidate({ candidate_id: "candidate-2", source: "auto", topic: "이전 후보" }),
  ]);
  const [manual, legacy] = fixture.candidateList.children.map(findProvenance);
  assert.equal(
    findByClassName(manual, "provenance__missing").textContent,
    "수동 등록 — 생성 근거 없음",
  );
  assert.equal(
    findByClassName(legacy, "provenance__missing").textContent,
    "생성 근거가 기록되지 않은 후보입니다.",
  );
  assert.equal(provenanceTexts(manual, "provenance__document mono").length, 0);
};

const testAutogenNoticeReportsWhatTheRunRead = async () => {
  const fixture = makeLiveDocument();
  const generated = [
    candidate({ source: "auto", generation_provenance: provenance() }),
    candidate({ candidate_id: "candidate-2", source: "auto", generation_provenance: provenance() }),
  ];
  let stored = [];
  await loadLive(fixture, async (path) => {
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (path === "/api/candidates/generate") {
      stored = generated;
      return response(201, generated);
    }
    if (path === "/api/candidates") return response(200, stored);
    throw new Error(`unexpected path: ${path}`);
  });
  await fixture.autogenButton.click();
  assert.equal(fixture.notice.textContent, "후보 2개가 등록되었습니다 — 문서 2개(9.8KB)를 읽고 생성");
};

const testMarkupUsesTheAgreedTerminology = async () => {
  const markup = await readFile(join(staticRoot, "workspace.html"), "utf8");
  const styles = await readFile(join(staticRoot, "workspace.css"), "utf8");
  assert.ok(markup.includes("<h1>Trace 마케팅</h1>"), "the workspace opens with a short task title");
  assert.ok(!markup.includes("Trace marketing pipeline"), "the decorative pipeline eyebrow is gone");
  assert.ok(markup.includes('class="context-detail"'), "full context is progressively disclosed");
  assert.ok(markup.includes('class="workflow-tools"'), "manual and feedback tools are progressively disclosed");
  assert.ok(!markup.includes('<details class="context-detail" open'), "context details start collapsed");
  assert.ok(!markup.includes('<details class="workflow-tools" open'), "advanced tools start collapsed");
  assert.ok(
    styles.includes(".candidate-list .candidate-row__caption") && styles.includes(".candidate-list .journey"),
    "the candidate overview removes duplicate review detail",
  );
  assert.ok(markup.includes(">검수</button>"), "approval tab is labelled 검수");
  assert.ok(!markup.includes("오늘의 승인"), "the old approval tab label is gone");
  assert.ok(markup.includes("Appium 프롬프트"), "the shooting order field is renamed");
  assert.ok(!markup.includes("촬영 주문서"), "the insider shooting-order label is gone");
  assert.ok(
    markup.includes('<select id="candidate-country" name="country" required>'),
    "country is a select",
  );
  assert.ok(markup.includes('id="candidate-topic" name="topic" required'), "topic is required");
  assert.ok(
    markup.indexOf('id="candidate-topic"') < markup.indexOf('id="candidate-country"'),
    "topic comes before country in the manual form",
  );
  assert.ok(markup.includes("주제/컨셉"), "the topic field is labelled 주제/컨셉");
  assert.ok(markup.includes("① 캡션·주제"), "the caption stage is titled");
  assert.ok(markup.includes("② 이미지"), "the image stage is titled");
  assert.ok(markup.includes("data-image-list"), "the image stage renders its own list");
  assert.ok(
    markup.includes('id="candidate-schedule" name="trace-items" required'),
    "the manual form collects the lock-screen schedule",
  );
  assert.ok(
    markup.includes('id="candidate-device-time" name="device-time" required'),
    "the manual form collects the device time",
  );
  assert.ok(
    markup.includes('id="candidate-background-subject" name="background-subject" required'),
    "the manual form collects the background subject",
  );
  assert.ok(markup.includes(">풍경</option>"), "background subjects are labelled in Korean");
  assert.ok(markup.includes("Cloudflare Queue → Mac Appium → R2"), "the native capture boundary is visible");
  assert.ok(markup.includes("부팅 가능한 Simulator를 찾아 Appium"), "dynamic Simulator discovery is explained");
  assert.ok(markup.includes('value="KR" selected>한국 (KR)'), "the form has a safe KR fallback");
  const live = await readFile(join(staticRoot, "workspace-live.js"), "utf8");
  assert.ok(live.includes('? "팀" : "기본"'), "context provenance uses short Korean labels");
  assert.ok(!live.includes("촬영 주문서"), "no insider shooting-order copy in the live script");
  assert.ok(!live.includes("촬영 전"), "the image placeholder is renamed");
  assert.ok(live.includes("이미지 생성 전"), "the image placeholder explains itself");
  assert.ok(live.includes("/api/context-countries"), "country options come from the hosted manifest");
};

await testCandidatesIsTheDefaultTab();
await testArrowKeysMoveBetweenTheTwoTabs();
await testCommandMenuOnlyLeadsToApproval();
await testOpenReviewButtonRevealsTheApprovalTab();
await testWorkspaceLoadOnlyReadsCandidates();
await testRefreshShowsAndClearsBusyState();
await testLoginShowsAndClearsActionBusyState();
await testRefreshFailureDoesNotSignOut();
await testAuthFailureSignsOut();
await testLoginValidationExplainsTheMissingAccessId();
await testLoginParsesCompositeAccessId();
await testLoginRejectsMalformedCompositeAccessId();
await testMemberAccessIdUsesMemberLoginRoute();
await testOwnerCanInviteMemberAndSeeOneTimeAccessId();
await testInviteValidationAndFailureStayNearby();
await testAutogenGeneratesAndRefreshesTheList();
await testAutogenShowsTheServerMessageVerbatim();
await testManualCandidateSubmitsParsedListFields();
await testManualCandidateValidationExplainsTheCountryCode();
await testManualCandidateValidationRequiresATopic();
await testManualCandidateValidationRequiresASchedule();
await testManualCandidateValidationCapsTheSchedule();
await testManualCandidateValidationExplainsTheDeviceTime();
await testTopicLeadsTheRowAndTheApprovalCard();
await testOnlyAwaitingCaptionsFillTheApprovalGate();
await testJourneyIsVisibleOnRowsAndCards();
await testJourneyPositionFollowsTheStatus();
await testImageStageSplitsCaptionAndImageWork();
await testImageGenerationButtonRunsTheStage();
await testImageGenerationFailureShowsTheServerMessage();
await testImageApprovalPostsTheDecision();
await testGenerationProvenanceIsShownOnRowsAndCards();
await testCandidatesWithoutProvenanceSayWhyThePanelIsEmpty();
await testAutogenNoticeReportsWhatTheRunRead();
await testMarkupUsesTheAgreedTerminology();
console.log("workspace static behavior: 35 passed");
