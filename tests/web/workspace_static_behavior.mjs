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
    ["workspace-id", "workspace-1"],
    ["member-id", "member-1"],
    ["workspace-code", "workspace-code"],
    ["member-code", "member-code"],
  ]);
  const candidateForm = new FakeElement("candidate-form");
  candidateForm.formValues = new Map([
    ["topic", "  시험기간 일정 관리 — 잠금화면 데모  "],
    ["country", " jp "],
    ["caption", "시험 기간엔 잠금화면부터 바꾼다"],
    ["hypothesis", "1인칭 감탄이 저장률을 올린다"],
    ["refs-used", "ref-a, ref-b"],
    ["principles-applied", "1, 4"],
    ["shooting-order", "- 책상 위 아이폰"],
  ]);
  const candidateFeedback = new FakeElement("candidate-feedback");
  candidateFeedback.hidden = true;
  const candidateList = new FakeElement("candidate-list");
  const candidateEmpty = new FakeElement("candidate-empty");
  const candidateCount = new FakeElement("candidate-count");
  const approvalList = new FakeElement("approval-list");
  const approvalEmpty = new FakeElement("approval-empty");
  const approvalCount = new FakeElement("approval-count");
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
    ["[data-autogen-feedback]", autogenFeedback],
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
    countryField,
    topicField,
    autogenButton,
    autogenFeedback,
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
    countryField,
    topicField,
    autogenButton,
    autogenFeedback,
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

const findJourney = (node) => {
  if (node?.className === "journey") return node;
  for (const child of node?.children ?? []) {
    const found = findJourney(child);
    if (found) return found;
  }
  return null;
};

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
  ai_verdict: null,
  image_path: null,
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

const testLoginValidationExplainsTheFirstMissingValue = async () => {
  const fixture = makeLiveDocument();
  fixture.memberForm.formValues.set("workspace-id", "");
  await loadLive(fixture, signedOut);
  await fixture.memberForm.submit();
  assert.equal(fixture.memberFeedback.textContent, "워크스페이스 ID 값을 입력해 주세요.");
};

const testLoginValidationReportsEveryMissingValue = async () => {
  const fixture = makeLiveDocument();
  fixture.memberForm.formValues.forEach((_value, name) => fixture.memberForm.formValues.set(name, ""));
  await loadLive(fixture, signedOut);
  await fixture.memberForm.submit();
  assert.equal(
    fixture.memberFeedback.textContent,
    "워크스페이스 ID, 멤버 ID, 워크스페이스 코드, 멤버 코드 값을 입력해 주세요.",
  );
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
    assert.equal(journey.children[0].title, undefined);
    assert.equal(journey.children[1].title, "다음 단계에서 연결됩니다");
    assert.equal(journey.children[2].title, "다음 단계에서 연결됩니다");
    assert.ok(journey.children[1].className.includes("is-planned"));
    assert.ok(journey.children[2].className.includes("is-planned"));
  }
};

const testJourneyPositionFollowsTheStatus = async () => {
  const cases = [
    ["awaiting_review", ["is-current", "", ""]],
    ["caption_approved", ["is-done", "is-current", ""]],
    ["rejected", ["is-rejected", "", ""]],
    ["image_approved", ["is-done", "is-done", "is-current"]],
    ["submitted", ["is-done", "is-done", "is-done"]],
  ];
  for (const [status, expected] of cases) {
    const fixture = makeLiveDocument();
    await loadCandidates(fixture, [candidate({ status })]);
    const journey = findJourney(fixture.candidateList.children[0]);
    assert.ok(journey, `journey rendered for ${status}`);
    const states = journey.children.map((step) => {
      const marks = step.className.split(" ").filter((name) => name.startsWith("is-") && name !== "is-planned");
      return marks.join(" ");
    });
    assert.deepEqual(states, expected, `journey state for ${status}`);
    assert.equal(
      journey.children.filter((step) => step.getAttribute("aria-current") === "step").length,
      expected.includes("is-current") ? 1 : 0,
    );
  }
};

const testMarkupUsesTheAgreedTerminology = async () => {
  const markup = await readFile(join(staticRoot, "workspace.html"), "utf8");
  assert.ok(markup.includes(">캡션·주제 승인</button>"), "approval tab is labelled 캡션·주제 승인");
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
  for (const option of ['value="KR" selected>한국', 'value="JP">일본', 'value="TW">대만', 'value="US">미국']) {
    assert.ok(markup.includes(option), `country option ${option}`);
  }
  const live = await readFile(join(staticRoot, "workspace-live.js"), "utf8");
  assert.ok(!live.includes("촬영 주문서"), "no insider shooting-order copy in the live script");
  assert.ok(!live.includes("촬영 전"), "the image placeholder is renamed");
  assert.ok(live.includes("이미지 생성 전"), "the image placeholder explains itself");
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
await testLoginValidationExplainsTheFirstMissingValue();
await testLoginValidationReportsEveryMissingValue();
await testAutogenGeneratesAndRefreshesTheList();
await testAutogenShowsTheServerMessageVerbatim();
await testManualCandidateSubmitsParsedListFields();
await testManualCandidateValidationExplainsTheCountryCode();
await testManualCandidateValidationRequiresATopic();
await testTopicLeadsTheRowAndTheApprovalCard();
await testOnlyAwaitingCaptionsFillTheApprovalGate();
await testJourneyIsVisibleOnRowsAndCards();
await testJourneyPositionFollowsTheStatus();
await testMarkupUsesTheAgreedTerminology();
console.log("workspace static behavior: 21 passed");
