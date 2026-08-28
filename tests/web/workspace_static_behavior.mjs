import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { runInNewContext } from "node:vm";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = dirname(dirname(dirname(fileURLToPath(import.meta.url))));
const staticRoot = join(root, "src", "ads_booster", "web", "static");
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

  get classList() {
    const owner = this;
    return {
      add(...names) {
        const present = new Set(String(owner.className ?? "").split(" ").filter(Boolean));
        for (const name of names) present.add(name);
        owner.className = [...present].join(" ");
      },
      remove(...names) {
        const present = String(owner.className ?? "").split(" ").filter(Boolean);
        owner.className = present.filter((name) => !names.includes(name)).join(" ");
      },
      contains(name) {
        return String(owner.className ?? "").split(" ").includes(name);
      },
    };
  }

  async click() {
    const listener = this.listeners.get("click");
    if (listener) await listener({ currentTarget: this, target: this, preventDefault() {} });
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
    const listener = this.listeners.get("close");
    if (listener) listener({ currentTarget: this, target: this });
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
    const element = new FakeElement(id);
    element.ownerDocument = this;
    return element;
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
  const workerManagerOpen = new FakeElement("worker-manager-open");
  const workerManager = new FakeElement("worker-manager");
  const workerManagerClose = new FakeElement("worker-manager-close");
  const workerAdminLocked = new FakeElement("worker-admin-locked");
  const workerAdminPanel = new FakeElement("worker-admin-panel");
  workerAdminPanel.hidden = true;
  const workerAdminForm = new FakeElement("worker-admin-form");
  workerAdminForm.formValues = new Map([["control-token", "admin-secret"]]);
  const workerAdminTokenField = new FakeElement("worker-control-token");
  const workerAdminFeedback = new FakeElement("worker-admin-feedback");
  workerAdminFeedback.hidden = false;
  const workerAdminSubmit = new FakeElement("worker-admin-submit");
  workerAdminSubmit.textContent = "관리 열기";
  const workerAdminRefresh = new FakeElement("worker-admin-refresh");
  workerAdminRefresh.textContent = "새로고침";
  const workerAdminLock = new FakeElement("worker-admin-lock");
  const workerList = new FakeElement("worker-list");
  const workerListEmpty = new FakeElement("worker-list-empty");
  const workerAdminSummary = new FakeElement("worker-admin-summary");
  const workerEnrollmentForm = new FakeElement("worker-enrollment-form");
  workerEnrollmentForm.formValues = new Map([
    ["display-name", "새 스튜디오 Mac"],
    ["pool", "appium"],
    ["ttl-seconds", "600"],
  ]);
  const workerDisplayName = new FakeElement("worker-display-name");
  const workerPool = new FakeElement("worker-pool");
  workerPool.value = "appium";
  const workerEnrollmentFeedback = new FakeElement("worker-enrollment-feedback");
  workerEnrollmentFeedback.hidden = false;
  const workerEnrollmentSubmit = new FakeElement("worker-enrollment-submit");
  workerEnrollmentSubmit.textContent = "일회용 코드 만들기";
  const workerEnrollmentResult = new FakeElement("worker-enrollment-result");
  workerEnrollmentResult.hidden = true;
  const workerEnrollmentCode = new FakeElement("worker-enrollment-code");
  const workerEnrollmentCommand = new FakeElement("worker-enrollment-command");
  const workerEnrollmentExpiry = new FakeElement("worker-enrollment-expiry");
  const workerEnrollmentCodeCopy = new FakeElement("worker-enrollment-code-copy");
  workerEnrollmentCodeCopy.textContent = "코드 복사";
  const workerEnrollmentCommandCopy = new FakeElement("worker-enrollment-command-copy");
  workerEnrollmentCommandCopy.textContent = "명령 복사";
  const workerAgentPrompt = new FakeElement("worker-agent-prompt");
  const workerAgentPromptCopy = new FakeElement("worker-agent-prompt-copy");
  workerAgentPromptCopy.textContent = "에이전트 프롬프트 복사";
  const workerAgentPromptFeedback = new FakeElement("worker-agent-prompt-feedback");
  const workerTitle = new FakeElement("worker-title");
  const workerCopy = new FakeElement("worker-copy");
  const workerSignal = new FakeElement("worker-signal");
  const workerBadges = new FakeElement("worker-badges");
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
  const countryHome = new FakeElement("country-home");
  const countryGrid = new FakeElement("country-grid");
  const countryEmpty = new FakeElement("country-empty");
  const countryCount = new FakeElement("country-count");
  const countryBack = new FakeElement("country-back");
  const countryAddAccount = new FakeElement("country-add-account");
  const countryCurrentName = new FakeElement("country-current-name");
  const accountHome = new FakeElement("account-home");
  accountHome.hidden = true;
  const accountWorkspace = new FakeElement("account-workspace");
  const accountGrid = new FakeElement("account-grid");
  const accountEmpty = new FakeElement("account-empty");
  const accountCount = new FakeElement("account-count");
  const accountBack = new FakeElement("account-back");
  const accountCurrentName = new FakeElement("account-current-name");
  const accountVerdict = new FakeElement("account-verdict");
  const accountFormDetails = new FakeElement("account-form-details");
  const accountCountryField = new FakeElement("account-country");
  const accountProposeButton = new FakeElement("account-propose", { accountPropose: "" });
  accountProposeButton.textContent = "🤖 AI 제안 받기";
  const accountProposalGrid = new FakeElement("account-proposals");
  accountProposalGrid.hidden = true;
  const accountProposalFeedback = new FakeElement("account-proposal-feedback");
  accountProposalFeedback.hidden = true;
  // The create form fields a chosen proposal fills, looked up by id like the live script does.
  const accountFields = [
    "account-name", "account-age", "account-region", "account-occupation", "account-domain",
    "account-concept", "account-interests", "account-rhythm", "account-background-subject",
    "account-background-mood", "account-font",
  ].map((id) => new FakeElement(id));
  const accountFormEl = new FakeElement("account-form");
  accountFormEl.checkValidity = () => true;
  accountFormEl.formValues = new Map([
    ["country", "JP"],
    ["display-name", "사토 유이"],
    ["age", "26"],
    ["region", "도쿄"],
    ["occupation", "카페 바리스타"],
    ["concept", "새벽 오픈조로 사는 바리스타"],
    ["domain", "office_worker"],
    ["interests", "커피, 러닝"],
    ["life-rhythm", "5시 기상, 6시 오픈"],
    ["background-subject", "scenery"],
    ["background-mood", "이른 아침 가게 앞"],
    ["font", "sf_pro"],
  ]);
  const accountFormFeedback = new FakeElement("account-feedback");
  accountFormFeedback.hidden = true;
  const accountDomainField = new FakeElement("account-domain");
  const accountBackgroundField = new FakeElement("account-background-subject");
  const accountFontField = new FakeElement("account-font");
  for (const field of [accountDomainField, accountBackgroundField, accountFontField]) {
    field.options = [];
  }
  const selectors = new Map([
    ["[data-account-form]", accountFormEl],
    ["[data-account-feedback]", accountFormFeedback],
    ["[data-account-domain]", accountDomainField],
    ["[data-account-background-subject]", accountBackgroundField],
    ["[data-account-font]", accountFontField],
    ["[data-country-home]", countryHome],
    ["[data-country-grid]", countryGrid],
    ["[data-country-empty]", countryEmpty],
    ["[data-country-count]", countryCount],
    ["[data-country-back]", countryBack],
    ["[data-country-add-account]", countryAddAccount],
    ["[data-country-current-name]", countryCurrentName],
    ["[data-account-form-details]", accountFormDetails],
    ["[data-account-home]", accountHome],
    ["[data-account-workspace]", accountWorkspace],
    ["[data-account-grid]", accountGrid],
    ["[data-account-empty]", accountEmpty],
    ["[data-account-count]", accountCount],
    ["[data-account-back]", accountBack],
    ["[data-account-current-name]", accountCurrentName],
    ["[data-account-propose]", accountProposeButton],
    ["[data-account-proposals]", accountProposalGrid],
    ["[data-account-proposal-feedback]", accountProposalFeedback],
    ["[data-account-verdict]", accountVerdict],
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
    ["[data-worker-manager-open]", workerManagerOpen],
    ["[data-worker-manager]", workerManager],
    ["[data-worker-manager-close]", workerManagerClose],
    ["[data-worker-admin-locked]", workerAdminLocked],
    ["[data-worker-admin-panel]", workerAdminPanel],
    ["[data-worker-admin-form]", workerAdminForm],
    ["#worker-control-token", workerAdminTokenField],
    ["[data-worker-admin-feedback]", workerAdminFeedback],
    ["[data-worker-admin-submit]", workerAdminSubmit],
    ["[data-worker-admin-refresh]", workerAdminRefresh],
    ["[data-worker-admin-lock]", workerAdminLock],
    ["[data-worker-list]", workerList],
    ["[data-worker-list-empty]", workerListEmpty],
    ["[data-worker-admin-summary]", workerAdminSummary],
    ["[data-worker-enrollment-form]", workerEnrollmentForm],
    ["#worker-display-name", workerDisplayName],
    ["#worker-pool", workerPool],
    ["[data-worker-enrollment-feedback]", workerEnrollmentFeedback],
    ["[data-worker-enrollment-submit]", workerEnrollmentSubmit],
    ["[data-worker-enrollment-result]", workerEnrollmentResult],
    ["[data-worker-enrollment-code]", workerEnrollmentCode],
    ["[data-worker-enrollment-command]", workerEnrollmentCommand],
    ["[data-worker-enrollment-expiry]", workerEnrollmentExpiry],
    ["[data-worker-enrollment-code-copy]", workerEnrollmentCodeCopy],
    ["[data-worker-enrollment-command-copy]", workerEnrollmentCommandCopy],
    ["[data-worker-agent-prompt]", workerAgentPrompt],
    ["[data-worker-agent-prompt-copy]", workerAgentPromptCopy],
    ["[data-worker-agent-prompt-feedback]", workerAgentPromptFeedback],
    ["[data-worker-title]", workerTitle],
    ["[data-worker-copy]", workerCopy],
    ["[data-worker-signal]", workerSignal],
    ["[data-worker-badges]", workerBadges],
  ]);
  const captionStageTab = new FakeElement("stage-tab-caption");
  captionStageTab.dataset.stageTab = "caption";
  const imageStageTab = new FakeElement("stage-tab-image");
  imageStageTab.dataset.stageTab = "image";
  const captionStagePanel = new FakeElement("stage-caption");
  captionStagePanel.dataset.stagePanel = "caption";
  const imageStagePanel = new FakeElement("stage-image");
  imageStagePanel.dataset.stagePanel = "image";
  imageStagePanel.hidden = true;
  const selectorGroups = new Map([
    ["[data-autogen]", [autogenButton]],
    ["[data-stage-tab]", [captionStageTab, imageStageTab]],
    ["[data-stage-panel]", [captionStagePanel, imageStagePanel]],
  ]);
  const document = new FakeDocument([
    ...accountFields,
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
    workerManagerOpen,
    workerManager,
    workerManagerClose,
    workerAdminLocked,
    workerAdminPanel,
    workerAdminForm,
    workerAdminTokenField,
    workerAdminFeedback,
    workerAdminSubmit,
    workerAdminRefresh,
    workerAdminLock,
    workerList,
    workerListEmpty,
    workerAdminSummary,
    workerEnrollmentForm,
    workerDisplayName,
    workerPool,
    workerEnrollmentFeedback,
    workerEnrollmentSubmit,
    workerEnrollmentResult,
    workerEnrollmentCode,
    workerEnrollmentCommand,
    workerEnrollmentExpiry,
    workerEnrollmentCodeCopy,
    workerEnrollmentCommandCopy,
    workerAgentPrompt,
    workerAgentPromptCopy,
    workerAgentPromptFeedback,
    workerTitle,
    workerCopy,
    workerSignal,
    workerBadges,
    scheduleField,
    deviceTimeField,
    backgroundMoodField,
    accountCountryField,
    accountProposeButton,
    accountProposalGrid,
    accountProposalFeedback,
    accountFields,
  ]);
  document.querySelector = (selector) => selectors.get(selector) ?? null;
  document.querySelectorAll = (selector) => selectorGroups.get(selector) ?? [];
  return {
    document,
    captionStageTab,
    imageStageTab,
    captionStagePanel,
    imageStagePanel,
    countryHome,
    countryGrid,
    countryEmpty,
    countryCount,
    countryBack,
    countryAddAccount,
    countryCurrentName,
    accountHome,
    accountWorkspace,
    accountGrid,
    accountEmpty,
    accountCount,
    accountBack,
    accountCurrentName,
    accountVerdict,
    accountFormDetails,
    accountFormEl,
    accountCountryField,
    accountProposeButton,
    accountProposalGrid,
    accountProposalFeedback,
    accountFields,
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
    workerManagerOpen,
    workerManager,
    workerManagerClose,
    workerAdminLocked,
    workerAdminPanel,
    workerAdminForm,
    workerAdminTokenField,
    workerAdminFeedback,
    workerAdminSubmit,
    workerAdminRefresh,
    workerAdminLock,
    workerList,
    workerListEmpty,
    workerAdminSummary,
    workerEnrollmentForm,
    workerDisplayName,
    workerPool,
    workerEnrollmentFeedback,
    workerEnrollmentSubmit,
    workerEnrollmentResult,
    workerEnrollmentCode,
    workerEnrollmentCommand,
    workerEnrollmentExpiry,
    workerEnrollmentCodeCopy,
    workerEnrollmentCommandCopy,
    workerAgentPrompt,
    workerAgentPromptCopy,
    workerAgentPromptFeedback,
    workerTitle,
    workerCopy,
    workerSignal,
    workerBadges,
    scheduleField,
    deviceTimeField,
    backgroundMoodField,
  };
};

// The live script re-arms a capture poll through window.setTimeout, so handing it the real
// timer would keep the Node event loop alive after every assertion has passed and the run
// would never exit. The pending callbacks are recorded instead: the harness decides when,
// or whether, a scheduled poll runs.
const fakeTimers = () => {
  const pending = new Map();
  let nextId = 1;
  return {
    pending,
    setTimeout(callback) {
      const id = nextId;
      nextId += 1;
      pending.set(id, callback);
      return id;
    },
    clearTimeout(id) {
      pending.delete(id);
    },
  };
};

const loadLive = async (fixture, fetchImplementation) => {
  const timers = fakeTimers();
  fixture.timers = timers;
  fixture.clipboard ??= [];
  // Every fixture now carries the account home, so a fresh load always reads the account
  // list. Answering it here keeps each test's own stub about the thing it is testing;
  // a test that cares about the list sets `fixture.accounts` before loading.
  fixture.accounts ??= [];
  const fetchWithAccounts = async (path, options) => {
    if (path === "/api/accounts" && (options?.method ?? "GET") === "GET") {
      return response(200, fixture.accounts);
    }
    return fetchImplementation(path, options);
  };
  runInNewContext(liveSource, {
    document: fixture.document,
    fetch: fetchWithAccounts,
    Headers,
    URL,
    navigator: { clipboard: { writeText: async (value) => { fixture.clipboard.push(value); } } },
    window: {
      clearTimeout: timers.clearTimeout,
      setTimeout: timers.setTimeout,
      confirm: () => true,
      location: { origin: "https://workspace.borca.ai" },
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

const testAutogenCountsTheWaitAndRefusesASecondPress = async () => {
  // Generation runs for minutes. A still button read as a hang and got pressed again,
  // which wrote a second batch, so the wait has to be visible while it is happening.
  const fixture = makeLiveDocument();
  const generation = deferred();
  let generateCalls = 0;
  await loadLive(fixture, async (path, options = {}) => {
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (path === "/api/candidates/generate") { generateCalls += 1; return generation.promise; }
    if (path === "/api/candidates") return response(200, []);
    throw new Error(`unexpected path: ${path}`);
  });

  const running = fixture.autogenButton.click();
  await nextTurn();
  assert.equal(fixture.autogenButton.disabled, true);
  assert.equal(fixture.autogenButton.textContent, "생성 중… 0초 (보통 1~3분)");

  // One tick of the clock, and the label moves rather than sitting still.
  const [tickId, tick] = [...fixture.timers.pending.entries()].at(-1);
  fixture.timers.pending.delete(tickId);
  tick();
  assert.equal(fixture.autogenButton.textContent, "생성 중… 1초 (보통 1~3분)");

  // A second press while the first is in flight must not start a second batch.
  await fixture.autogenButton.click();
  assert.equal(generateCalls, 1);

  generation.resolve(response(201, []));
  await running;
  await nextTurn();
  assert.equal(fixture.autogenButton.disabled, false);
  assert.equal(fixture.autogenButton.textContent, "🤖 후보 자동 생성");
  // And the clock is stopped rather than left ticking against a finished request.
  assert.equal(fixture.timers.pending.size, 0);
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
    background_search_query: null,
    language: "ja",
  });
  assert.equal(payload.persona_domain, null, "an unselected domain is sent as absent, not as an empty token");
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

const findByClass = (node, className) => {
  if (node?.className?.split(" ").includes(className)) return node;
  for (const child of node?.children ?? []) {
    const found = findByClass(child, className);
    if (found) return found;
  }
  return null;
};

const flatten = (node, into = []) => {
  if (!node) return into;
  into.push(node);
  for (const child of node.children ?? []) flatten(child, into);
  return into;
};

const provenance = () => ({
  documents: [
    { relative_path: "core/FACTS.md", size_bytes: 2048 },
    { relative_path: "core/VOICE-KR.md", size_bytes: 1024 },
  ],
  model: "gpt-5.5",
  instruction_chars: 12_345,
  generated_at: 1_770_000_000,
  assigned_domains: ["sports_fan", "exam_prepper"],
});

const judgedBackground = () => ({
  query: "김도영 직캠",
  provider: "ddgs",
  image_url: "https://cdn.example/a.jpg",
  source_url: "https://www.blog.example/a",
  sha256: "a".repeat(64),
  pipeline: "local_fallback",
  judgment: {
    reviews: [
      {
        image_id: "img-a",
        image_url: "https://cdn.example/a.jpg",
        source_url: "https://www.blog.example/a",
        gated: false,
        grades: { authenticity: "상", persona_fit: "상", background_fit: "중" },
        score: 8,
        note: "실제 관중석에서 찍힌 사진",
      },
      {
        image_id: "img-b",
        image_url: "https://cdn.example/b.jpg",
        source_url: "https://stock.example/b",
        gated: true,
        gate_reason: "워터마크",
        note: "",
      },
    ],
    chosen_id: "img-a",
    reason: "실제 관중석에서 찍힌 사진",
    model: "gpt-5.5",
    query: "김도영 직캠",
    attempts: [
      { query: "김도영 타격 직캠 고화질", source: "original", results: 0, passed_filters: 0 },
      { query: "김도영 직캠", source: "broadened", results: 6, passed_filters: 2 },
    ],
    tie_broken: false,
    tie_break_inconsistent: false,
  },
});

const testGenerationProvenanceIsVisibleOnEveryCandidate = async () => {
  const fixture = makeLiveDocument();
  await loadCandidates(fixture, [
    candidate({ source: "auto", generation_provenance: provenance() }),
  ]);
  const panel = findByText(fixture.candidateList.children[0], "🧠 생성 근거");
  assert.ok(panel, "the generation provenance panel is on the candidate row");
  const texts = flatten(fixture.candidateList.children[0]).map((node) => node.textContent);
  assert.ok(texts.includes("읽은 문서 2개"), "the document count is named");
  assert.ok(texts.some((text) => text?.includes("context/core/FACTS.md · 2.0KB")));
  assert.ok(texts.some((text) => text?.includes("gpt-5.5")), "the model that answered is named");
  assert.ok(texts.includes("이번 배치 배정"), "the coverage assignment is shown");
  assert.ok(
    texts.some((text) => text?.includes("스포츠 팬 · 수험생")),
    "assigned domains are shown with their Korean labels",
  );
};

const testAManualCandidateSaysItHasNoGenerationProvenance = async () => {
  const fixture = makeLiveDocument();
  await loadCandidates(fixture, [candidate({ source: "manual" })]);
  const texts = flatten(fixture.candidateList.children[0]).map((node) => node.textContent);
  assert.ok(texts.includes("수동 등록 — 생성 근거 없음"));
};

const testDeleteAsksBeforeItDeletes = async () => {
  const fixture = makeLiveDocument();
  const calls = [];
  await loadLive(fixture, async (path, options = {}) => {
    calls.push([path, options.method ?? "GET"]);
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (path === "/api/candidates" && (options.method ?? "GET") === "GET") {
      return response(200, calls.some(([, method]) => method === "DELETE") ? [] : [candidate({})]);
    }
    if (options.method === "DELETE") return response(204, null);
    throw new Error(`unexpected path: ${path}`);
  });
  const control = findByClass(fixture.candidateList.children[0], "candidate-delete");
  assert.ok(control, "every candidate row carries a delete control");

  // The first click only arms the control; nothing is deleted yet.
  await control.children[0].click();
  assert.deepEqual(calls.filter(([, method]) => method === "DELETE"), []);
  assert.equal(control.children[0].textContent, "정말 삭제할까요?");
  assert.equal(control.children[2].textContent, "취소");

  // Cancelling puts it back without touching the server.
  await control.children[2].click();
  assert.equal(control.children[0].textContent, "삭제");
  assert.deepEqual(calls.filter(([, method]) => method === "DELETE"), []);

  // Arming again and confirming is what deletes.
  await control.children[0].click();
  await control.children[1].click();
  assert.deepEqual(
    calls.filter(([, method]) => method === "DELETE"),
    [["/api/candidates/candidate-1", "DELETE"]],
  );
  assert.equal(fixture.notice.textContent, "후보를 삭제했습니다.");
};

const testTheImageCardShowsTheBackgroundQueryAndJudgement = async () => {
  const fixture = makeLiveDocument();
  await loadCandidates(fixture, [
    candidate({
      status: "image_awaiting_review",
      image_path: "candidates/candidate-1/r2/outputs/final.png",
      background_provenance: judgedBackground(),
      image_inputs: {
        ...candidate({}).image_inputs,
        background_search_query: "김도영 타격 직캠 고화질",
      },
    }),
  ]);
  const card = fixture.imageList.children[0];
  const texts = flatten(card).map((node) => node.textContent);
  assert.ok(texts.includes("배경 검색어"), "the authored query stays on the card");
  assert.ok(texts.some((text) => text?.includes("김도영 타격 직캠 고화질")));
  assert.ok(texts.includes("배경 출처"), "the source page is named");
  assert.ok(texts.includes("blog.example"), "the source host is shown without www.");
  assert.ok(
    texts.includes("배경 심사 · 2장 검토 → 1장 게이트 탈락"),
    "the judgement summary counts what was reviewed and gated",
  );
  assert.ok(texts.some((text) => text?.includes("진정성 상 · 페르소나 상 · 배경 중 (8점)")));
  assert.ok(texts.some((text) => text?.includes("게이트 탈락 — 워터마크")));
  assert.ok(
    texts.some((text) => text?.includes("시도한 검색어 2개") && text.includes("범위 확장")),
    "every rung of the query ladder is listed",
  );
  assert.ok(
    texts.some((text) => text?.includes("로컬 합성")),
    "a locally composed image says so rather than passing as a native capture",
  );
  assert.ok(findByText(card, "Appium 프롬프트"), "the Appium prompt stays on the image card");
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
  assert.equal(fixture.notice.textContent, "Mac 캡처 작업을 등록했습니다. 완료되면 이미지 검수 카드가 자동으로 갱신됩니다.");
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, "Mac에서 이미지 생성");
  assert.ok(findByText(fixture.imageList.children[0], "온라인 Mac worker가 작업 lease를 가져가면 Appium 캡처가 시작됩니다. 완료되면 이 카드가 자동으로 갱신됩니다."));
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

const testMacConnectionsAreManagedWithAnEphemeralControlToken = async () => {
  const fixture = makeLiveDocument();
  const calls = [];
  let workerState = "active";
  let rejectEnrollment = false;
  const workerRecord = () => ({
    worker_id: "worker-1",
    display_name: "스튜디오 Mac",
    pool: "appium",
    state: workerState,
    status: workerState === "active" ? "busy" : workerState,
    capabilities: { native_appium: true },
    doctor: { ready: true, summary: "ready" },
    version: "0.2.3",
    last_seen_at: new Date().toISOString(),
    current_task_id: "task-1",
  });
  await loadLive(fixture, async (path, options = {}) => {
    calls.push([path, options]);
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (path === "/api/candidates") return response(200, []);
    if (path === "/v1/workers" && new Headers(options.headers).get("authorization") !== "Bearer admin-secret") {
      return response(401, { error: "unauthorized" });
    }
    if (path === "/v1/workers" && options.method === undefined) {
      return response(200, { workers: [workerRecord()] });
    }
    if (path === "/v1/workers/worker-1/state") {
      workerState = JSON.parse(options.body).state;
      return response(200, { worker_id: "worker-1", state: workerState });
    }
    if (path === "/v1/workers/worker-1/revoke") {
      workerState = "revoked";
      return response(200, { worker_id: "worker-1", state: workerState });
    }
    if (path === "/v1/worker-enrollments") {
      if (rejectEnrollment) return response(401, { error: "unauthorized" });
      return response(201, {
        enrollment_code: "trace-enroll_once",
        expires_at: "2026-08-26T04:00:00.000Z",
      });
    }
    throw new Error(`unexpected path: ${path}`);
  });

  await fixture.workerManagerOpen.click();
  assert.equal(fixture.workerManager.open, true);
  assert.ok(fixture.workerAdminTokenField.events.includes("focus"));
  fixture.workerAdminForm.formValues.set("control-token", "wrong-token");
  await fixture.workerAdminForm.submit();
  assert.equal(fixture.workerAdminLocked.hidden, false);
  assert.equal(fixture.workerAdminPanel.hidden, true);
  assert.equal(
    fixture.workerAdminFeedback.textContent,
    "제어 토큰이 맞지 않습니다. Cloudflare의 CONTROL_PLANE_TOKEN 값을 확인해 주세요.",
  );
  assert.equal(fixture.workerAdminTokenField.getAttribute("aria-invalid"), "true");

  fixture.workerAdminForm.formValues.set("control-token", "admin-secret");
  await fixture.workerAdminForm.submit();
  assert.equal(fixture.workerAdminLocked.hidden, true);
  assert.equal(fixture.workerAdminPanel.hidden, false);
  assert.equal(fixture.workerList.children.length, 1);
  assert.ok(findByText(fixture.workerList.children[0], "작업 중"));
  assert.equal(fixture.workerAdminSummary.textContent, "전체 1대 · 작업 가능 0대 · 작업 중 1대 · 확인 필요 0대");

  const protectedCalls = calls.filter(([path]) => path.startsWith("/v1/"));
  assert.ok(protectedCalls.length > 0);
  assert.ok(protectedCalls.some(([_path, options]) =>
    new Headers(options.headers).get("authorization") === "Bearer wrong-token"));
  assert.ok(protectedCalls.some(([_path, options]) =>
    new Headers(options.headers).get("authorization") === "Bearer admin-secret"));

  assert.ok(fixture.workerAgentPrompt.textContent.includes("ComputerName"));
  assert.ok(fixture.workerAgentPrompt.textContent.includes("https://workspace.borca.ai"));
  assert.ok(fixture.workerAgentPrompt.textContent.includes("CONTROL_PLANE_TOKEN을 채팅으로 요청하지 않는다"));
  assert.ok(fixture.workerAgentPrompt.textContent.includes("com.corca.trace-marketing-updater"));
  assert.ok(!fixture.workerAgentPrompt.textContent.includes("admin-secret"));
  assert.ok(!fixture.workerAgentPrompt.textContent.includes("trace-enroll_once"));
  await fixture.workerAgentPromptCopy.click();
  assert.equal(fixture.clipboard.at(-1), fixture.workerAgentPrompt.textContent);
  assert.equal(fixture.workerAgentPromptCopy.textContent, "프롬프트 복사됨");

  await findByText(fixture.workerList.children[0], "새 작업 중지").click();
  assert.equal(workerState, "draining");
  assert.ok(findByText(fixture.workerList.children[0], "다시 활성화"));

  await fixture.workerEnrollmentForm.submit();
  assert.equal(fixture.workerEnrollmentResult.hidden, false);
  assert.equal(fixture.workerEnrollmentCode.textContent, "trace-enroll_once");
  const firstEnrollmentCall = calls.findLast(([path]) => path === "/v1/worker-enrollments");
  assert.equal(JSON.parse(firstEnrollmentCall[1].body).ttl_seconds, 600);
  assert.ok(fixture.workerEnrollmentCommand.textContent.includes(
    "trace-marketing worker enroll --url https://workspace.borca.ai --code 'trace-enroll_once'",
  ));
  // The enrollment command is a signed bootstrap block now: it resolves the latest release,
  // verifies every asset's attestation, and only then runs anything.
  assert.ok(fixture.workerEnrollmentCommand.textContent.includes(
    'repository="corca-ai/ads-booster"',
  ));
  assert.ok(fixture.workerEnrollmentCommand.textContent.includes(
    'gh release view --repo "$repository"',
  ));
  assert.ok(fixture.workerEnrollmentCommand.textContent.includes(
    'gh attestation verify "$asset"',
  ));
  assert.ok(fixture.workerEnrollmentCommand.textContent.includes("--deny-self-hosted-runners"));
  assert.ok(fixture.workerEnrollmentCommand.textContent.includes(
    "trace-marketing worker finish-bootstrap",
  ));
  assert.ok(fixture.workerEnrollmentCommand.textContent.includes(
    "trace-marketing worker updater-status",
  ));
  assert.match(
    fixture.workerEnrollmentCommand.textContent,
    /^bash -euo pipefail <<'TRACE_MAC_BOOTSTRAP'/u,
  );
  assert.match(fixture.workerEnrollmentCommand.textContent, /\nTRACE_MAC_BOOTSTRAP$/u);
  assert.doesNotMatch(fixture.workerEnrollmentCommand.textContent, /worker install-service/u);

  // And it stops before touching the machine when release resolution fails: a fake `gh` that
  // answers everything with a version string leaves no manifest behind, so the block must
  // abort rather than run trace-marketing against nothing.
  const fakeRoot = mkdtempSync(join(tmpdir(), "trace-enrollment-fail-fast-"));
  const fakeBin = join(fakeRoot, "bin");
  const traceLog = join(fakeRoot, "trace.log");
  try {
    mkdirSync(fakeBin);
    const commands = {
      gh: "#!/bin/sh\nif [ \"$1 $2\" = \"auth status\" ]; then exit 0; fi\nif [ \"$1 $2\" = \"release view\" ]; then printf 'v0.3.0\\n'; exit 0; fi\nexit 42\n",
      "trace-marketing": "#!/bin/sh\nprintf 'trace-marketing %s\\n' \"$*\" >> \"$TRACE_TEST_LOG\"\n",
    };
    for (const [name, source] of Object.entries(commands)) {
      const target = join(fakeBin, name);
      writeFileSync(target, source);
      chmodSync(target, 0o755);
    }
    const failedInstall = spawnSync(
      "/bin/bash",
      ["-c", fixture.workerEnrollmentCommand.textContent],
      {
        encoding: "utf8",
        env: {
          ...process.env,
          HOME: fakeRoot,
          PATH: `${fakeBin}:/usr/bin:/bin`,
          TRACE_TEST_LOG: traceLog,
        },
      },
    );
    assert.notEqual(failedInstall.status, 0);
    assert.throws(() => readFileSync(traceLog, "utf8"), /ENOENT/u);
  } finally {
    rmSync(fakeRoot, { recursive: true, force: true });
  }
  await fixture.workerEnrollmentCommandCopy.click();
  assert.equal(fixture.clipboard.at(-1), fixture.workerEnrollmentCommand.textContent);
  assert.equal(fixture.workerEnrollmentCommandCopy.textContent, "명령 복사됨");

  const row = fixture.workerList.children[0];
  await findByText(row, "연결 폐기").click();
  const revoke = findByText(row, "폐기 확정");
  assert.ok(revoke, "revocation requires an explicit second action");
  assert.ok(findByText(
    row,
    "스튜디오 Mac의 자격 증명을 폐기하고 현재 작업을 해제합니다. 콜백 반영 중이면 자격 증명 폐기가 거절되므로 Appium 결과를 먼저 확인하세요.",
  ));
  await revoke.click();
  assert.equal(workerState, "revoked");
  assert.ok(findByText(fixture.workerList.children[0], "연결 폐기됨"));

  fixture.workerEnrollmentForm.formValues.set("ttl-seconds", "");
  rejectEnrollment = true;
  await fixture.workerEnrollmentForm.submit();
  const rejectedEnrollmentCall = calls.findLast(
    ([path]) => path === "/v1/worker-enrollments",
  );
  assert.equal(Object.hasOwn(JSON.parse(rejectedEnrollmentCall[1].body), "ttl_seconds"), false);
  assert.equal(fixture.workerAdminLocked.hidden, false);
  assert.equal(fixture.workerAdminPanel.hidden, true);

  await fixture.workerManagerClose.click();
  assert.equal(fixture.workerManager.open, false);
  assert.equal(fixture.workerAdminPanel.hidden, true);
  assert.equal(fixture.workerAdminLocked.hidden, false);
  assert.equal(fixture.workerEnrollmentResult.hidden, true);
  assert.equal(fixture.workerEnrollmentCode.textContent, "");
};

let passed = 0;

const testUnregisteredMacDisablesHostedImageCapture = async () => {
  const fixture = makeLiveDocument();
  await loadLive(fixture, async (path) => {
    if (path === "/api/auth/session") {
      return response(200, {
        member_id: "public",
        workspace_id: "cloudflare:trace_demo_kr",
        account_id: "trace_demo_kr",
        display_name: "Public reviewer",
      });
    }
    if (path === "/api/accounts") return response(200, []);
    if (path.startsWith("/api/personas")) return response(200, []);
    if (path === "/api/context-countries") return response(200, []);
    if (path === "/api/context-profiles") return response(200, []);
    if (path === "/api/candidates") return response(200, []);
    if (path === "/api/feedback-summary") {
      return response(200, { rejected_reviews: 0, top_tags: [], rule_candidates: [], active_rules: [] });
    }
    if (path === "/api/workers/status") {
      return response(200, {
        status: "not_configured",
        counts: { online: 0, busy: 0, draining: 0, registered: 0 },
        workers: [],
      });
    }
    throw new Error(`unexpected path: ${path}`);
  });

  assert.equal(fixture.workerTitle.textContent, "Mac worker 미등록");
  assert.equal(
    fixture.workerCopy.textContent,
    "Mac worker를 등록하기 전에는 이미지 캡처를 시작할 수 없습니다.",
  );
  assert.doesNotMatch(fixture.workerCopy.textContent, /Queue/u);
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
  assert.ok(markup.includes(">캡션 승인<"), "the caption stage has its own tab");
  assert.ok(markup.includes(">이미지 승인<"), "the image stage has its own tab");
  assert.ok(
    markup.includes('data-stage-panel="image" hidden'),
    "only one approval stage is on screen at a time",
  );
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
  assert.ok(markup.includes("Cloudflare D1 lease → Mac Appium → R2"), "the native capture boundary is visible");
  assert.ok(markup.includes("data-worker-title"), "the hosted UI exposes sanitized Mac availability");
  assert.ok(markup.includes("data-worker-manager-open"), "the status strip opens Mac connection management");
  assert.ok(markup.includes("data-worker-admin-form"), "the Mac manager unlocks protected controls explicitly");
  assert.ok(markup.includes("data-worker-enrollment-form"), "the Mac manager creates one-time enrollment codes");
  assert.ok(markup.includes("data-worker-agent-prompt-copy"), "the Mac manager exposes a copyable agent guide");
  assert.ok(markup.includes("data-worker-list"), "the Mac manager renders the protected worker inventory");
  assert.ok(
    markup.includes('id="worker-control-token" name="control-token" type="password" required autocomplete="off"'),
    "the control token is an unseeded password field",
  );
  assert.ok(markup.includes("부팅 가능한 Simulator를 동적으로 찾습니다"), "dynamic Simulator discovery is explained");
  assert.ok(markup.includes('value="KR" selected>한국 (KR)'), "the form has a safe KR fallback");
  const live = await readFile(join(staticRoot, "workspace-live.js"), "utf8");
  assert.ok(live.includes('let workerAdminToken = ""'), "the control token starts only in JavaScript memory");
  const workerCodeRule = styles.match(/\.worker-enrollment-result__code code \{[^}]+\}/)?.[0] ?? "";
  assert.ok(!workerCodeRule.includes("word-break"), "worker enrollment codes avoid deprecated word-break");
  assert.ok(!/localStorage[^\n]*workerAdminToken|workerAdminToken[^\n]*localStorage/.test(live), "the control token is never persisted in browser storage");
  assert.ok(live.includes('? "팀" : "기본"'), "context provenance uses short Korean labels");
  assert.ok(!live.includes("촬영 주문서"), "no insider shooting-order copy in the live script");
  assert.ok(!live.includes("촬영 전"), "the image placeholder is renamed");
  assert.ok(live.includes("이미지 생성 전"), "the image placeholder explains itself");
  assert.ok(live.includes("/api/context-countries"), "country options come from the hosted manifest");
};

const allText = (element) =>
  [element.textContent ?? "", ...element.children.map(allText)].join(" ");

const _account = (accountId, name, country = "KR") => ({
  account_id: accountId,
  display_name: name,
  country,
  language: "ko",
  status: "observing",
  revision: 1,
  identity: {
    age: 27,
    region: "서울",
    occupation: "병동 간호사",
    concept: "3교대를 잠금화면 일정으로 버티는 간호사",
    domain: "office_worker",
  },
});

const openCountry = async (fixture, index) => {
  const card = fixture.countryGrid.children[index];
  await card.children.at(-1).click();
};

const openAccount = async (fixture, index) => {
  const card = fixture.accountGrid.children[index];
  await card.children.at(-1).click();
};

const testOneAccountGeneratingLeavesEveryOtherAccountFree = async () => {
  // Generation runs for minutes. Locking the whole screen meant one account's batch also
  // froze every other account's, on a server that takes the requests concurrently.
  const fixture = makeLiveDocument();
  fixture.accounts = [_account("acc-1", "이서진"), _account("acc-2", "김도현")];
  const generated = [];
  const first = deferred();
  await loadLive(fixture, async (path, options = {}) => {
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (path.startsWith("/api/candidates/generate")) {
      generated.push(path);
      return path.includes("acc-1") ? first.promise : response(201, []);
    }
    if (path.startsWith("/api/candidates")) return response(200, []);
    throw new Error(`unexpected path: ${path}`);
  });

  await openCountry(fixture, 0);
  await openAccount(fixture, 0);
  const label = fixture.autogenButton.textContent;
  const running = fixture.autogenButton.click();
  await nextTurn();
  assert.equal(fixture.autogenButton.disabled, true);
  assert.equal(fixture.autogenButton.textContent, "생성 중… 0초 (보통 1~3분)");
  // The screen itself is no longer held: review work is unrelated to generation.
  assert.equal(fixture.workspaceLive.getAttribute("aria-busy"), "false");

  // Stepping out to the account home already frees the button.
  await fixture.accountBack.click();
  assert.equal(fixture.autogenButton.disabled, false);
  assert.equal(fixture.autogenButton.textContent, label);

  // And the other account can start its own batch while the first is still in flight.
  await openAccount(fixture, 1);
  assert.equal(fixture.autogenButton.disabled, false);
  await fixture.autogenButton.click();
  assert.deepEqual(generated, [
    "/api/candidates/generate?account_id=acc-1",
    "/api/candidates/generate?account_id=acc-2",
  ]);

  // Coming back to the account that is still generating shows it still generating.
  await fixture.accountBack.click();
  await openAccount(fixture, 0);
  assert.equal(fixture.autogenButton.disabled, true);
  assert.equal(fixture.autogenButton.textContent, "생성 중… 0초 (보통 1~3분)");

  // And finishing releases only that account's button.
  first.resolve(response(201, []));
  await running;
  await nextTurn();
  assert.equal(fixture.autogenButton.disabled, false);
  assert.equal(fixture.autogenButton.textContent, label);
  assert.equal(fixture.timers.pending.size, 0);
};

const testTheApprovalStagesAreViewedOneAtATime = async () => {
  const fixture = makeLiveDocument();
  await loadLive(fixture, async (path) => {
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (path.startsWith("/api/candidates")) return response(200, []);
    if (path === "/api/accounts") return response(200, []);
    throw new Error(`unexpected path: ${path}`);
  });

  assert.equal(fixture.captionStagePanel.hidden, false, "captions open first");
  assert.equal(fixture.imageStagePanel.hidden, true);

  fixture.imageStageTab.click();

  assert.equal(fixture.captionStagePanel.hidden, true);
  assert.equal(fixture.imageStagePanel.hidden, false);
  assert.equal(fixture.imageStageTab.attributes.get("aria-selected"), "true");
  assert.equal(fixture.captionStageTab.attributes.get("aria-selected"), "false");

  fixture.captionStageTab.click();

  assert.equal(fixture.captionStagePanel.hidden, false);
  assert.equal(fixture.imageStagePanel.hidden, true);
};

const _proposalPayload = (name, reason) => ({
  identity: {
    display_name: name,
    age: 27,
    region: "서울 마포구",
    occupation: "병동 간호사",
    concept: `${name}의 3교대 잠금화면`,
    domain: "office_worker",
    interests: ["쿠로미", "필라테스", "동네 베이커리"],
    life_rhythm: "데이 근무일은 5시 40분 기상",
    taste: {
      background_subject: "character_other",
      background_mood: "파스텔 톤 캐릭터 화면",
      font: "sf_pro_rounded",
    },
  },
  reason,
});

const _field = (fixture, id) => fixture.accountFields.find((element) => element.id === id);

const testAiProposalsFillTheCreateFormWithoutSubmittingIt = async () => {
  // Opening an account meant writing twelve fields from a blank form. The proposal turns
  // that into a choice: pick a card, the form fills, and the person edits and submits it
  // down the ordinary route. Nothing is stored until they do.
  const fixture = makeLiveDocument();
  fixture.accounts = [];
  const posted = [];
  await loadLive(fixture, async (path, options = {}) => {
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (path === "/api/accounts/proposals") {
      posted.push(JSON.parse(options.body));
      return response(200, [
        _proposalPayload("이서진", "kr-014·kr-003처럼 질문형 훅이 도달을 만든 사례가 있다"),
        _proposalPayload("김도현", "kr-001의 직장인 공감 계열"),
      ]);
    }
    if (path.startsWith("/api/candidates")) return response(200, []);
    throw new Error(`unexpected path: ${path}`);
  });

  await fixture.accountProposeButton.click();

  // The request names the country the form is pointed at.
  assert.deepEqual(posted, [{ country: "KR" }]);
  // Two cards, each showing the concept and the evidence behind it.
  assert.equal(fixture.accountProposalGrid.hidden, false);
  assert.equal(fixture.accountProposalGrid.children.length, 2);
  assert.match(allText(fixture.accountProposalGrid.children[0]), /이서진/);
  assert.match(allText(fixture.accountProposalGrid.children[0]), /병동 간호사/);
  assert.match(allText(fixture.accountProposalGrid.children[0]), /kr-014/);
  // The button came back for another try.
  assert.equal(fixture.accountProposeButton.disabled, false);
  assert.equal(fixture.accountProposeButton.textContent, "🤖 AI 제안 받기");
  // And nothing has been created by looking.
  assert.equal(fixture.accountGrid.children.length, 0);

  // Choosing one fills the form rather than submitting it.
  await fixture.accountProposalGrid.children[0].children.at(-1).click();
  assert.equal(_field(fixture, "account-name").value, "이서진");
  assert.equal(_field(fixture, "account-age").value, "27");
  assert.equal(_field(fixture, "account-occupation").value, "병동 간호사");
  assert.equal(_field(fixture, "account-domain").value, "office_worker");
  assert.equal(_field(fixture, "account-interests").value, "쿠로미, 필라테스, 동네 베이커리");
  assert.equal(_field(fixture, "account-font").value, "sf_pro_rounded");
  // The cards clear once one is taken, and still nothing is stored.
  assert.equal(fixture.accountProposalGrid.hidden, true);
  assert.equal(fixture.accountGrid.children.length, 0);
};

const testAFailedProposalSaysSoNextToTheButton = async () => {
  const fixture = makeLiveDocument();
  const detail = "AI 응답이 형식을 통과하지 못했습니다 — 다시 시도해 주세요.";
  await loadLive(fixture, async (path) => {
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (path === "/api/accounts/proposals") return response(502, { detail });
    if (path.startsWith("/api/candidates")) return response(200, []);
    throw new Error(`unexpected path: ${path}`);
  });

  await fixture.accountProposeButton.click();

  assert.equal(fixture.accountProposalFeedback.hidden, false);
  assert.equal(fixture.accountProposalFeedback.textContent, detail);
  assert.equal(fixture.accountProposeButton.disabled, false);
  assert.equal(fixture.accountProposalGrid.children.length, 0);
};

const testTheCountryHomeOpensBeforeAnyAccountWork = async () => {
  // The screen is three storeys now: country, then that country's accounts, then that
  // account's work. Countries are not stored anywhere — the list is derived from the
  // accounts, because an account is the only evidence a market is being worked at all.
  const fixture = makeLiveDocument();
  fixture.accounts = [
    _account("acc-1", "이서진"),
    _account("acc-2", "김도현"),
    _account("acc-3", "사토 유이", "JP"),
  ];
  await loadLive(fixture, async (path) => {
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (path.startsWith("/api/candidates")) return response(200, []);
    throw new Error(`unexpected path: ${path}`);
  });

  assert.equal(fixture.countryHome.hidden, false, "the home is the country grid");
  assert.equal(fixture.accountHome.hidden, true);
  assert.equal(fixture.accountWorkspace.hidden, true);
  assert.equal(fixture.countryCount.textContent, "국가 2개");
  assert.equal(fixture.countryGrid.children.length, 2);

  const [japan, korea] = fixture.countryGrid.children;
  assert.match(allText(japan), /일본/);
  assert.match(allText(japan), /JP/);
  assert.match(allText(japan), /계정 1개/);
  assert.match(allText(korea), /한국/);
  assert.match(allText(korea), /계정 2개/);
  assert.match(allText(korea), /이서진 · 김도현/);

  // Opening a country drops one storey and keeps only that country's accounts.
  await openCountry(fixture, 1);
  assert.equal(fixture.countryHome.hidden, true);
  assert.equal(fixture.accountHome.hidden, false);
  assert.equal(fixture.accountWorkspace.hidden, true);
  assert.equal(fixture.countryCurrentName.textContent, "한국");
  assert.equal(fixture.accountCount.textContent, "계정 2개");
  assert.deepEqual(
    fixture.accountGrid.children.map((card) => allText(card).match(/이서진|김도현|사토 유이/)[0]),
    ["이서진", "김도현"],
  );
  // Adding another account here almost always means adding it to this country.
  assert.equal(fixture.accountCountryField.value, "KR");

  // And the other country shows only its own.
  await fixture.countryBack.click();
  assert.equal(fixture.countryHome.hidden, false);
  await openCountry(fixture, 0);
  assert.equal(fixture.countryCurrentName.textContent, "일본");
  assert.equal(fixture.accountCount.textContent, "계정 1개");
  assert.match(allText(fixture.accountGrid.children[0]), /사토 유이/);
};

const _hostedSession = () => response(200, {
  member_id: "public",
  workspace_id: "cloudflare:trace_demo_kr",
  account_id: "trace_demo_kr",
  display_name: "Public reviewer",
});

const _hostedPersona = (accountId, name) => ({
  workspace_id: "cloudflare:trace_demo_kr",
  account_id: accountId,
  display_name: name,
  country: "KR",
  language: "ko",
  timezone: "Asia/Seoul",
  morning_time: "08:00",
  evening_time: "20:00",
  generation_enabled: false,
  status: "observing",
  note: "",
  revision: 1,
  created_at: 1,
  updated_at: 1,
  identity: {
    display_name: name,
    age: 27,
    region: "서울 마포구",
    occupation: "병동 간호사",
    concept: `${name}의 3교대 잠금화면`,
    domain: "office_worker",
    interests: ["쿠로미"],
    life_rhythm: "데이 출근일 5시 40분 기상",
    taste: {
      background_subject: "character_other",
      background_mood: "파스텔 톤의 캐릭터 배경",
      font: "sf_pro_rounded",
    },
  },
});

const testHostedCountryOpensItsPersonasAndThenTheWork = async () => {
  // On the hosted plane the middle storey was empty: /api/accounts is the country's
  // operating account, and personas had nowhere to live. They are their own resource now,
  // so the same three storeys work there — country, that country's personas, that
  // persona's work.
  const fixture = makeLiveDocument();
  // `/api/accounts` on the hosted plane is the country's operating account, and the harness
  // answers it from `fixture.accounts`.
  fixture.accounts = [{
    account_id: "trace_demo_kr",
    display_name: "Trace Korea",
    country: "KR",
    language: "ko",
    timezone: "Asia/Seoul",
    morning_time: "07:30",
    evening_time: "19:30",
    generation_enabled: true,
    revision: 1,
  }];
  const requested = [];
  await loadLive(fixture, async (path) => {
    requested.push(path);
    if (path === "/api/auth/session") return _hostedSession();
    if (path.startsWith("/api/personas")) {
      return response(200, [_hostedPersona("persona-1", "이서진"), _hostedPersona("persona-2", "김도현")]);
    }
    if (path === "/api/context-countries") return response(200, []);
    if (path === "/api/context-profiles") return response(200, []);
    if (path.startsWith("/api/candidates")) return response(200, []);
    if (path === "/api/feedback-summary") {
      return response(200, { rejected_reviews: 0, top_tags: [], rule_candidates: [], active_rules: [] });
    }
    if (path === "/api/workers/status") {
      return response(200, { status: "online", counts: { online: 1, busy: 0, draining: 0, registered: 1 }, workers: [] });
    }
    throw new Error(`unexpected path: ${path}`);
  });

  // The persona layer is read from its own endpoint, not from the operating account list.
  assert.ok(requested.some((path) => path.startsWith("/api/personas")));

  // Storey one: the country, derived from the operating account.
  assert.equal(fixture.countryHome.hidden, false);
  assert.equal(fixture.countryCount.textContent, "국가 1개");
  assert.match(allText(fixture.countryGrid.children[0]), /한국/);
  assert.match(allText(fixture.countryGrid.children[0]), /계정 2개/);
  assert.match(allText(fixture.countryGrid.children[0]), /이서진 · 김도현/);

  // Storey two: that country's personas, with the identity the hosted table now carries.
  await openCountry(fixture, 0);
  assert.equal(fixture.countryHome.hidden, true);
  assert.equal(fixture.accountHome.hidden, false);
  assert.equal(fixture.countryCurrentName.textContent, "한국");
  assert.equal(fixture.accountCount.textContent, "계정 2개");
  assert.match(allText(fixture.accountGrid.children[0]), /이서진/);
  assert.match(allText(fixture.accountGrid.children[0]), /병동 간호사/);

  // Storey three: that persona's work screen.
  await fixture.accountGrid.children[0].children.at(-1).click();
  assert.equal(fixture.accountHome.hidden, true);
  assert.equal(fixture.accountWorkspace.hidden, false);
  assert.equal(fixture.accountCurrentName.textContent, "이서진");
};

const testTheLocalSurfaceStillReadsPersonasFromAccounts = async () => {
  // The two surfaces read different tables; the local one must not follow the hosted one.
  const fixture = makeLiveDocument();
  fixture.accounts = [_account("acc-1", "이서진")];
  const requested = [];
  await loadLive(fixture, async (path) => {
    requested.push(path);
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (path.startsWith("/api/candidates")) return response(200, []);
    throw new Error(`unexpected path: ${path}`);
  });

  // The harness answers `/api/accounts` itself, so the proof that the local surface read it
  // is that the persona it serves reached the grid — and that nothing asked for /api/personas.
  assert.ok(!requested.some((path) => path.startsWith("/api/personas")));
  assert.equal(fixture.countryCount.textContent, "국가 1개");
  await openCountry(fixture, 0);
  assert.equal(fixture.accountCount.textContent, "계정 1개");
  assert.match(allText(fixture.accountGrid.children[0]), /이서진/);
};

const testTheAccountHomeOpensBeforeAnyCandidateWork = async () => {
  const fixture = makeLiveDocument();
  fixture.accounts = [
    {
      account_id: "acc-1",
      display_name: "박세나",
      country: "KR",
      language: "ko",
      status: "observing",
      revision: 1,
      identity: {
        age: 27,
        region: "서울",
        occupation: "병동 간호사",
        concept: "3교대를 잠금화면 일정으로 버티는 간호사",
        domain: "office_worker",
      },
    },
  ];
  const listed = [];
  await loadLive(fixture, async (path) => {
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (path.startsWith("/api/candidates")) { listed.push(path); return response(200, []); }
    throw new Error(`unexpected path: ${path}`);
  });

  await openCountry(fixture, 0);
  assert.equal(fixture.accountHome.hidden, false);
  assert.equal(fixture.accountWorkspace.hidden, true);
  assert.equal(fixture.accountCount.textContent, "계정 1개");
  const card = fixture.accountGrid.children[0];
  assert.match(allText(card), /박세나/);
  assert.match(allText(card), /병동 간호사/);

  const open = card.children.at(-1);
  await open.click();

  assert.equal(fixture.countryHome.hidden, true);
  assert.equal(fixture.accountHome.hidden, true);
  assert.equal(fixture.accountWorkspace.hidden, false);
  assert.equal(fixture.accountCurrentName.textContent, "박세나");
  assert.match(fixture.notice.textContent, /박세나 계정으로 작업합니다/);
  // The first load is workspace-wide because no account is open yet; opening one scopes
  // the list to it, so another account's drafts never appear on its screens.
  assert.deepEqual(listed, ["/api/candidates", "/api/candidates?account_id=acc-1"]);

  // Back walks up one storey at a time: work → that country's accounts → the countries.
  await fixture.accountBack.click();
  assert.equal(fixture.accountWorkspace.hidden, true);
  assert.equal(fixture.accountHome.hidden, false);
  assert.equal(fixture.countryHome.hidden, true);
  assert.equal(fixture.countryCurrentName.textContent, "한국");

  await fixture.countryBack.click();
  assert.equal(fixture.accountHome.hidden, true);
  assert.equal(fixture.countryHome.hidden, false);
  assert.match(fixture.notice.textContent, /국가 목록으로 돌아왔습니다/);
};

const testTheFirstAccountCreatesItsCountry = async () => {
  // With no accounts there is no country to open, so the empty country home has to lead
  // somewhere: the account form. The country picked there is the country being created.
  const fixture = makeLiveDocument();
  fixture.accounts = [];
  const created = {
    ..._account("acc-jp-1", "사토 유이", "JP"),
    identity: { age: 26, region: "도쿄", occupation: "카페 바리스타", concept: "새벽 오픈조로 사는 바리스타", domain: "office_worker" },
  };
  let posted = null;
  await loadLive(fixture, async (path, options = {}) => {
    if (path === "/api/auth/session") return response(200, { display_name: "Ada" });
    if (path === "/api/accounts" && options.method === "POST") {
      posted = JSON.parse(options.body);
      fixture.accounts = [created];
      return response(201, created);
    }
    if (path.startsWith("/api/candidates")) return response(200, []);
    throw new Error(`unexpected path: ${path}`);
  });

  assert.equal(fixture.countryHome.hidden, false);
  assert.equal(fixture.countryEmpty.hidden, false, "an empty country home says so");
  assert.equal(fixture.countryCount.textContent, "등록된 국가가 없습니다");
  assert.equal(fixture.countryGrid.children.length, 0);

  await fixture.countryAddAccount.click();
  assert.equal(fixture.countryHome.hidden, true);
  assert.equal(fixture.accountHome.hidden, false);
  assert.equal(fixture.accountFormDetails.open, true, "the form is already unfolded");
  assert.equal(fixture.countryCurrentName.textContent, "새 국가");
  assert.equal(fixture.accountEmpty.hidden, false);

  await fixture.accountFormEl.submit();

  assert.equal(posted.country, "JP");
  assert.equal(posted.schedule.timezone, "Asia/Tokyo");
  // The new account's country is now a country, and the maker is standing inside it.
  assert.equal(fixture.accountHome.hidden, false);
  assert.equal(fixture.countryCurrentName.textContent, "일본");
  assert.equal(fixture.accountCount.textContent, "계정 1개");
  assert.match(allText(fixture.accountGrid.children[0]), /사토 유이/);

  await fixture.countryBack.click();
  assert.equal(fixture.countryHome.hidden, false);
  assert.equal(fixture.countryEmpty.hidden, true);
  assert.equal(fixture.countryCount.textContent, "국가 1개");
  assert.match(allText(fixture.countryGrid.children[0]), /일본/);
};


await testCandidatesIsTheDefaultTab();
passed += 1;
await testArrowKeysMoveBetweenTheTwoTabs();
passed += 1;
await testCommandMenuOnlyLeadsToApproval();
passed += 1;
await testOpenReviewButtonRevealsTheApprovalTab();
passed += 1;
await testWorkspaceLoadOnlyReadsCandidates();
passed += 1;
await testRefreshShowsAndClearsBusyState();
passed += 1;
await testLoginShowsAndClearsActionBusyState();
passed += 1;
await testRefreshFailureDoesNotSignOut();
passed += 1;
await testAuthFailureSignsOut();
passed += 1;
await testLoginValidationExplainsTheMissingAccessId();
passed += 1;
await testLoginParsesCompositeAccessId();
passed += 1;
await testLoginRejectsMalformedCompositeAccessId();
passed += 1;
await testMemberAccessIdUsesMemberLoginRoute();
passed += 1;
await testOwnerCanInviteMemberAndSeeOneTimeAccessId();
passed += 1;
await testInviteValidationAndFailureStayNearby();
passed += 1;
await testAutogenGeneratesAndRefreshesTheList();
passed += 1;
await testAutogenShowsTheServerMessageVerbatim();
passed += 1;
await testAutogenCountsTheWaitAndRefusesASecondPress();
passed += 1;
await testManualCandidateSubmitsParsedListFields();
passed += 1;
await testManualCandidateValidationExplainsTheCountryCode();
passed += 1;
await testManualCandidateValidationRequiresATopic();
passed += 1;
await testManualCandidateValidationRequiresASchedule();
passed += 1;
await testManualCandidateValidationCapsTheSchedule();
passed += 1;
await testManualCandidateValidationExplainsTheDeviceTime();
passed += 1;
await testTopicLeadsTheRowAndTheApprovalCard();
passed += 1;
await testOnlyAwaitingCaptionsFillTheApprovalGate();
passed += 1;
await testJourneyIsVisibleOnRowsAndCards();
passed += 1;
await testJourneyPositionFollowsTheStatus();
passed += 1;
await testImageStageSplitsCaptionAndImageWork();
passed += 1;
await testImageGenerationButtonRunsTheStage();
passed += 1;
await testImageGenerationFailureShowsTheServerMessage();
passed += 1;
await testImageApprovalPostsTheDecision();
passed += 1;
await testMacConnectionsAreManagedWithAnEphemeralControlToken();
passed += 1;
await testUnregisteredMacDisablesHostedImageCapture();
passed += 1;
await testMarkupUsesTheAgreedTerminology();
passed += 1;
await testGenerationProvenanceIsVisibleOnEveryCandidate();
passed += 1;
await testAManualCandidateSaysItHasNoGenerationProvenance();
passed += 1;
await testDeleteAsksBeforeItDeletes();
passed += 1;
await testTheImageCardShowsTheBackgroundQueryAndJudgement();
passed += 1;
await testTheCountryHomeOpensBeforeAnyAccountWork();
passed += 1;
await testAiProposalsFillTheCreateFormWithoutSubmittingIt();
passed += 1;
await testAFailedProposalSaysSoNextToTheButton();
passed += 1;
await testHostedCountryOpensItsPersonasAndThenTheWork();
passed += 1;
await testTheLocalSurfaceStillReadsPersonasFromAccounts();
passed += 1;
await testTheAccountHomeOpensBeforeAnyCandidateWork();
passed += 1;
await testTheFirstAccountCreatesItsCountry();
passed += 1;
await testOneAccountGeneratingLeavesEveryOtherAccountFree();
passed += 1;
await testTheApprovalStagesAreViewedOneAtATime();
passed += 1;
console.log(`workspace static behavior: ${passed} passed`);
