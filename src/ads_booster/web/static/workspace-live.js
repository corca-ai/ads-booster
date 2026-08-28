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
  const candidateForm = one("[data-candidate-form]");
  const manualEntry = one("[data-manual-entry]");
  const candidateFormTitle = one("[data-candidate-form-title]");
  const candidateSubmit = one("[data-candidate-submit]");
  const candidateCancel = one("[data-candidate-cancel]");
  const candidateEditNote = one("[data-candidate-edit-note]");
  const contextSelect = one("[data-context-select]");
  const contextForm = one("[data-context-form]");
  const contextFeedback = one("[data-context-feedback]");
  const contextCancel = one("[data-context-cancel]");
  const contextSubmit = one("[data-context-submit]");
  const contextFormTitle = one("[data-context-form-title]");
  const accountSelect = one("[data-account-select]");
  const accountEditForm = one("[data-account-edit-form]");
  const accountCreateForm = one("[data-account-create-form]");
  const accountEditFeedback = one("[data-account-edit-feedback]");
  const accountCreateFeedback = one("[data-account-create-feedback]");
  const workerManager = one("[data-worker-manager]");
  const workerManagerOpen = one("[data-worker-manager-open]");
  const workerManagerClose = one("[data-worker-manager-close]");
  const workerAdminLocked = one("[data-worker-admin-locked]");
  const workerAdminPanel = one("[data-worker-admin-panel]");
  const workerAdminForm = one("[data-worker-admin-form]");
  const workerAdminTokenField = one("#worker-control-token");
  const workerAdminFeedback = one("[data-worker-admin-feedback]");
  const workerAdminActionFeedback = one("[data-worker-admin-action-feedback]");
  const workerAdminSubmit = one("[data-worker-admin-submit]");
  const workerAdminRefresh = one("[data-worker-admin-refresh]");
  const workerAdminLock = one("[data-worker-admin-lock]");
  const workerEnrollmentForm = one("[data-worker-enrollment-form]");
  const workerEnrollmentFeedback = one("[data-worker-enrollment-feedback]");
  const workerEnrollmentSubmit = one("[data-worker-enrollment-submit]");
  const workerEnrollmentResult = one("[data-worker-enrollment-result]");
  const workerEnrollmentCode = one("[data-worker-enrollment-code]");
  const workerEnrollmentCommand = one("[data-worker-enrollment-command]");
  const workerEnrollmentExpiry = one("[data-worker-enrollment-expiry]");
  const workerEnrollmentCodeCopy = one("[data-worker-enrollment-code-copy]");
  const workerEnrollmentCommandCopy = one("[data-worker-enrollment-command-copy]");
  const workerAgentPrompt = one("[data-worker-agent-prompt]");
  const workerAgentPromptCopy = one("[data-worker-agent-prompt-copy]");
  const workerAgentPromptFeedback = one("[data-worker-agent-prompt-feedback]");
  let hostedCandidateControls = false;
  let editingCandidate = null;
  let editingCandidateContextChanged = false;
  let editingContextProfile = null;
  let contextProfiles = [];
  let contextCountries = [];
  let selectedContextProfileId = "";
  let candidateRecords = [];
  let hostedAccounts = [];
  let selectedAccountId = "";
  let feedbackSignal = null;
  let capturePoll = null;
  let workerPoll = null;
  let macWorkerStatus = null;
  let workerAdminToken = "";
  let workerAdminPoll = null;
  let managedWorkers = [];

  const HANGUL = /[가-힣]/;
  const ERROR_MESSAGES = Object.freeze({
    "authentication required": "로그인이 필요합니다.",
    "invalid credentials": "워크스페이스 또는 멤버 정보가 올바르지 않습니다.",
    "candidate not found": "후보를 찾을 수 없습니다. 새로고침 후 다시 시도해 주세요.",
    "candidate revision conflict": "다른 사람이 먼저 처리했습니다. 새로고침 후 다시 시도해 주세요.",
    "candidate already reviewed": "이미 승인 또는 반려된 후보입니다. 새로고침 후 다시 시도해 주세요.",
  });
  const CANDIDATE_SOURCE_LABELS = Object.freeze({
    auto: "AI 생성",
    manual: "수동",
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
    DE: "de",
    FR: "fr",
    BR: "pt",
  });
  // 국가 홈의 카드 이름. 계정 추가 폼의 `한국 (KR)` 라벨과 같은 이름을 쓰되, 코드는 카드의
  // 배지가 따로 보여 주므로 여기서는 나라 이름만 담는다.
  const COUNTRY_LABELS = Object.freeze({
    KR: "한국",
    JP: "일본",
    TW: "대만",
    US: "미국",
    DE: "독일",
    FR: "프랑스",
    BR: "브라질",
  });
  const COUNTRY_TIMEZONES = Object.freeze({
    KR: "Asia/Seoul",
    JP: "Asia/Tokyo",
    TW: "Asia/Taipei",
    US: "America/New_York",
    DE: "Europe/Berlin",
    FR: "Europe/Paris",
    BR: "America/Sao_Paulo",
  });
  const REVIEW_TAGS = Object.freeze([
    "이미지 품질·AI 티",
    "앱 화면·데이터 오류",
    "국가·언어 부적합",
    "계정 페르소나 불일치",
    "컨셉이 약함",
    "기존 게시물과 중복",
    "캡션 부적합",
    "브랜드·정책 위험",
    "기타",
  ]);
  const REVIEW_TAGS_BY_STAGE = Object.freeze({
    caption: Object.freeze(REVIEW_TAGS.filter((tag) => ![
      "이미지 품질·AI 티",
      "앱 화면·데이터 오류",
    ].includes(tag))),
    image: Object.freeze(REVIEW_TAGS.filter((tag) => ![
      "컨셉이 약함",
      "기존 게시물과 중복",
      "캡션 부적합",
    ].includes(tag))),
  });
  const PERSONA_DOMAIN_LABELS = Object.freeze({
    sports_fan: "스포츠 팬",
    idol_fandom: "아이돌·밴드 팬덤",
    exam_prepper: "수험생",
    parenting: "육아",
    office_worker: "직군 직장인",
    fitness_crew: "러닝·등산 크루",
    pet_owner: "반려동물 보호자",
    cert_student: "자격증 준비생",
    small_business: "자영업",
  });
  const QUERY_SOURCE_LABELS = Object.freeze({
    original: "원본",
    broadened: "범위 확장",
    rewritten: "AI 재작성",
  });
  const CONFIRM_REVERT_MS = 8000;
  const AUTOGEN_TICK_MS = 1000;
  const DEFAULT_LANGUAGE = "en";
  const MAX_TRACE_ITEMS = 8;

  const countryLanguage = (country) => COUNTRY_LANGUAGES[country] ?? DEFAULT_LANGUAGE;
  const countryLabel = (country) => COUNTRY_LABELS[country] ?? country;

  const localizeError = (message) => ERROR_MESSAGES[message] ?? "요청에 실패했습니다. 잠시 후 다시 시도해 주세요.";
  const candidateSourceLabel = (source) => CANDIDATE_SOURCE_LABELS[source] ?? source;
  const candidateStatusLabel = (record) => {
    if (record.capture_state === "queued") return "Mac 캡처 대기·실행 중";
    if (record.capture_state === "failed") return "Mac 캡처 실패 · 재시도 가능";
    return CANDIDATE_STATUS_LABELS[record.status] ?? record.status;
  };

  const renderMacWorkerStatus = () => {
    if (!macWorkerStatus) return;
    const title = one("[data-worker-title]");
    const copy = one("[data-worker-copy]");
    const signal = one("[data-worker-signal]");
    const badges = one("[data-worker-badges]");
    const readyAliases = macWorkerStatus.workers
      .filter((worker) => ["ready", "busy"].includes(worker.status))
      .map((worker) => worker.display_name);
    const presentation = macWorkerStatus.counts.draining > 0 && macWorkerStatus.counts.online === 0
      ? ["Mac worker 전환 중", "기존 worker가 draining 상태라 새 작업을 받지 않습니다. 새 Mac을 활성화해 주세요.", "warning"]
      : ({
      ready: ["Mac 캡처 가능", readyAliases.length
        ? `현재 ${readyAliases.join(", ")}에서 이미지 작업을 받을 수 있습니다.`
        : "연결된 Mac이 이미지 작업을 받을 수 있습니다.", "success"],
      degraded: ["Mac 환경 점검 필요", "worker는 온라인이지만 Appium 준비 항목이 부족해 작업을 받지 않습니다.", "warning"],
      offline: ["Mac worker 오프라인", "작업은 Cloudflare에 대기하며 worker가 다시 켜지면 자동으로 이어집니다.", "danger"],
      not_configured: ["Mac worker 미등록", "Mac worker를 등록하기 전에는 이미지 캡처를 시작할 수 없습니다.", "warning"],
    }[macWorkerStatus.status] ?? ["Mac 상태 확인 불가", "잠시 후 다시 확인합니다.", "warning"]);
    if (title) title.textContent = presentation[0];
    if (copy) copy.textContent = presentation[1];
    if (signal) signal.className = `signal-mark ${presentation[2]}`;
    if (badges) badges.replaceChildren(
      badge("approval-badge", `온라인 ${macWorkerStatus.counts.online}`),
      badge("approval-badge", `작업 중 ${macWorkerStatus.counts.busy}`),
      badge("approval-badge", `전환 중 ${macWorkerStatus.counts.draining}`),
      badge("approval-badge quiet", `등록 ${macWorkerStatus.counts.registered}`),
    );
  };

  const loadMacWorkerStatus = async () => {
    if (!hostedCandidateControls || !one("[data-worker-title]")) return;
    try {
      macWorkerStatus = await request("/api/workers/status");
      renderMacWorkerStatus();
    } finally {
      if (workerPoll) window.clearTimeout(workerPoll);
      workerPoll = window.setTimeout(() => {
        loadMacWorkerStatus().catch((error) => setNotice(error.message));
      }, 15000);
    }
  };
  const candidateDate = (seconds) => new Date(seconds * 1000).toLocaleDateString("ko-KR");
  const postingSlotLabel = (slot) => ({ morning: "오전", evening: "저녁", manual: "수동" })[slot] ?? slot;
  const candidateDateTime = (seconds) => new Date(seconds * 1000).toLocaleString("ko-KR");
  const kilobytes = (bytes) => `${(bytes / 1024).toFixed(1)}KB`;
  const provenanceBytes = (provenance) =>
    provenance.documents.reduce((total, document) => total + document.size_bytes, 0);
  const domainLabel = (domain) => PERSONA_DOMAIN_LABELS[domain] ?? domain;
  const sourceHost = (url) => {
    try {
      return new URL(url).hostname.replace(/^www\./, "");
    } catch {
      return url;
    }
  };

  const setNotice = (message) => {
    if (notice) notice.textContent = message;
  };

  const setBusy = (element, busy, message = null) => {
    if (!element) return;
    element.setAttribute("aria-busy", String(busy));
    if (busy && message) setNotice(message);
  };

  const request = async (path, options = {}) => {
    const headers = new Headers(options.headers ?? {});
    if (hostedCandidateControls && selectedAccountId) {
      headers.set("X-Trace-Account-ID", selectedAccountId);
    }
    const response = await fetch(path, { credentials: "same-origin", ...options, headers });
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
    hostedCandidateControls = member.member_id === "public" && String(member.workspace_id).startsWith("cloudflare:");
    if (hostedCandidateControls && !selectedAccountId) selectedAccountId = member.account_id;
    if (entryScreen) entryScreen.hidden = true;
    if (workspaceLive) workspaceLive.hidden = false;
    if (skipLink) skipLink.setAttribute("href", "#workspace-content");
    if (memberFields) memberFields.hidden = true;
    if (memberConnected) memberConnected.hidden = false;
    if (memberLabel) memberLabel.textContent = hostedCandidateControls ? "Cloudflare 연결됨" : "로컬 연결됨";
    if (memberName) memberName.textContent = member.display_name;
    const workspaceAccount = one("[data-workspace-account]");
    if (workspaceAccount) workspaceAccount.textContent = selectedAccountId || member.account_id || member.workspace_id;
    if (inviteButton) inviteButton.hidden = member.is_admin !== true;
    all("[data-hosted-only]").forEach((element) => { element.hidden = !hostedCandidateControls; });
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
    all("[data-hosted-only]").forEach((element) => { element.hidden = true; });
    if (workerPoll) window.clearTimeout(workerPoll);
    workerPoll = null;
    if (workerManager?.open) workerManager.close();
    else lockWorkerManager();
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

  const setWorkerAdminFeedback = (message) => {
    if (!workerAdminFeedback) return;
    workerAdminFeedback.textContent = message;
  };

  const setWorkerAdminActionFeedback = (message) => {
    if (!workerAdminActionFeedback) return;
    workerAdminActionFeedback.textContent = message;
  };

  const setWorkerEnrollmentFeedback = (message) => {
    if (!workerEnrollmentFeedback) return;
    workerEnrollmentFeedback.textContent = message;
  };

  const setWorkerAgentPromptFeedback = (message) => {
    if (!workerAgentPromptFeedback) return;
    workerAgentPromptFeedback.textContent = message;
  };

  const macEnrollmentAgentPrompt = () => [
    "현재 이 Mac을 Trace 마케팅의 동적 Mac worker로 등록하고 자동 업데이트까지 활성화해줘.",
    "",
    `워크스페이스: ${window.location.origin.replace(/\/$/, "")}`,
    "작업 풀: appium",
    "Mac 이름: 현재 macOS ComputerName을 사용하고, 없으면 HostName을 사용",
    "",
    "목표:",
    "- Mac 연결 관리 화면에서 일회용 등록 명령을 만든다.",
    "- 최신 stable trace-marketing release의 tag·commit·digest·attestation을 검증해 설치한다.",
    "- worker와 별도 updater LaunchAgent를 시작한다.",
    "- doctor, launchd, heartbeat, 정확한 설치 버전을 확인한다.",
    "- 실제 이미지 캡처나 게시 작업은 만들지 않는다.",
    "",
    "보안과 중단 원칙:",
    "- CONTROL_PLANE_TOKEN, enrollment code, worker credential, GitHub/Codex 인증정보를 대화·로그·보고서에 출력하지 않는다.",
    "- CONTROL_PLANE_TOKEN을 채팅으로 요청하지 않는다. 입력 단계에서 내가 브라우저 비밀번호 필드에 직접 입력하게 한다.",
    "- 토큰을 보내 일회용 credential과 지속 실행 LaunchAgent를 만들기 직전에 영향 범위를 설명하고 한 번 확인받는다.",
    "- mutable main, Git checkout, uv tool --force를 설치 경로로 사용하지 않는다.",
    "- Codex CLI, Xcode, Appium, XCUITest, Trace 앱, gh, uv, Python을 자동 설치·업그레이드하지 않는다.",
    "- 실행 중 task, pending callback, 미완료 execution marker가 있으면 강제 종료·재등록하지 않고 상태를 보고한다.",
    "- ads-booster 소유가 확인되지 않은 LaunchAgent를 변경하거나 삭제하지 않는다.",
    "",
    "진행:",
    "1. 읽기 전용으로 uname -m, Python 3.14, gh, uv, codex, Appium 3, XCUITest, iPhone Simulator, com.corca.Trace 준비 상태를 점검한다.",
    "2. gh auth status와 codex login status는 성공 여부만 확인하고 인증값을 출력하지 않는다.",
    "3. 기존 설치가 있으면 current/bin/trace-marketing worker status와 updater-status를 먼저 읽는다. 이미 정상 등록된 Mac이면 새 코드를 만들지 않는다.",
    "4. 필수 환경이 부족하면 등록 전에 중단하고 부족한 항목만 보고한다.",
    "5. 브라우저에서 워크스페이스를 열고 Mac 연결 관리 → 운영 권한 열기로 이동한다.",
    "6. 내가 CONTROL_PLANE_TOKEN을 직접 입력하고 관리 열기를 누른 뒤에만 계속한다. 토큰을 읽거나 복사하거나 저장하지 않는다.",
    "7. 새 Mac 연결에서 Mac 이름, appium, 10분을 선택하고 일회용 코드 만들기를 누른다.",
    "8. 코드 자체를 출력하지 말고 명령 복사로 전체 fail-fast 블록을 복사한다.",
    "9. 복사한 블록이 bash -euo pipefail, corca-ai/ads-booster, gh attestation verify, worker doctor → enroll → finish-bootstrap → status 순서를 포함하는지 확인한다.",
    "10. 같은 macOS 사용자의 Terminal에 멀티라인 paste로 명령을 변형 없이 한 번 실행하고 종료 상태를 기다린다.",
    "11. 설치 후 절대 경로 current/bin/trace-marketing으로 version --json, worker doctor, worker status, worker updater-status를 확인한다.",
    "12. launchctl에서 com.corca.trace-marketing-worker와 com.corca.trace-marketing-updater가 실행 중인지 확인한다.",
    "13. 관리 화면을 새로고침하며 최대 90초 동안 Mac이 작업 가능이고 화면 버전이 로컬 버전과 일치하는지 확인한다.",
    "",
    "실패 처리:",
    "- enrolled 출력 전 실패면 환경 문제를 보고하고 무작정 새 코드를 만들지 않는다.",
    "- enrolled 출력 후 finish-bootstrap 실패면 새 enrollment 대신 기존 credential을 보존하고 finish-bootstrap 재실행 가능성을 확인한다.",
    "- 만료 또는 이미 사용된 코드가 명확할 때만 내 확인 후 새 코드를 만든다.",
    "",
    "완료 보고에는 Mac 이름, 설치 version/commit, doctor, worker/updater 상태, 화면 heartbeat 버전과 남은 문제만 포함한다. secret과 enrollment code는 포함하지 말고 실제 capture canary는 실행하지 않았다고 명시한다.",
  ].join("\n");

  const workerAdminRequest = async (path, options = {}) => {
    if (!workerAdminToken) {
      const failure = new Error("Mac 관리 토큰을 다시 입력해 주세요.");
      failure.status = 401;
      throw failure;
    }
    const headers = new Headers(options.headers ?? {});
    headers.set("Authorization", `Bearer ${workerAdminToken}`);
    const response = await fetch(path, { credentials: "same-origin", ...options, headers });
    const payload = response.status === 204 ? null : await response.json();
    if (!response.ok) {
      const message = ({
        400: "요청 값을 확인해 주세요.",
        401: "제어 토큰이 맞지 않습니다. Cloudflare의 CONTROL_PLANE_TOKEN 값을 확인해 주세요.",
        404: "Mac 연결을 찾지 못했습니다. 목록을 새로고침해 주세요.",
        409: "다른 작업이 먼저 상태를 바꿨습니다. 목록을 새로고침해 주세요.",
      })[response.status] ?? `Mac 관리 요청에 실패했습니다 (${response.status}).`;
      const failure = new Error(message);
      failure.status = response.status;
      throw failure;
    }
    return payload;
  };

  const managedWorkerState = (worker) => {
    if (worker.state === "revoked") return "revoked";
    return worker.status ?? (worker.state === "draining" ? "draining" : "offline");
  };

  const MANAGED_WORKER_STATE_LABELS = Object.freeze({
    ready: "작업 가능",
    busy: "작업 중",
    degraded: "환경 점검 필요",
    draining: "새 작업 중지",
    offline: "오프라인",
    revoked: "연결 폐기됨",
  });

  const workerSeenLabel = (value) => {
    const seenAt = Date.parse(value ?? "");
    if (!Number.isFinite(seenAt)) return "연결 기록 없음";
    const seconds = Math.max(0, Math.floor((Date.now() - seenAt) / 1000));
    if (seconds < 60) return "방금 연결";
    if (seconds < 3600) return `${Math.floor(seconds / 60)}분 전 연결`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}시간 전 연결`;
    return `${new Date(seenAt).toLocaleDateString("ko-KR")} 연결`;
  };

  const setWorkerActionBusy = (button, busy, label) => {
    if (!button) return;
    if (busy) {
      button.dataset.idleLabel = button.textContent;
      button.textContent = label;
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      return;
    }
    button.textContent = button.dataset.idleLabel || button.textContent;
    delete button.dataset.idleLabel;
    button.disabled = false;
    button.removeAttribute("aria-busy");
  };

  const updateManagedWorkerState = async (worker, state, button) => {
    setWorkerAdminActionFeedback("");
    setWorkerActionBusy(button, true, "변경 중…");
    try {
      await workerAdminRequest(`/v1/workers/${encodeURIComponent(worker.worker_id)}/state`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state }),
      });
      await Promise.all([loadManagedWorkers(), loadMacWorkerStatus()]);
    } catch (error) {
      setWorkerAdminActionFeedback(error.message);
      throw error;
    } finally {
      setWorkerActionBusy(button, false);
    }
  };

  const revokeManagedWorker = async (worker, button) => {
    setWorkerAdminActionFeedback("");
    setWorkerActionBusy(button, true, "폐기 중…");
    try {
      await workerAdminRequest(`/v1/workers/${encodeURIComponent(worker.worker_id)}/revoke`, {
        method: "POST",
      });
      await Promise.all([loadManagedWorkers(), loadMacWorkerStatus()]);
    } catch (error) {
      setWorkerAdminActionFeedback(error.message);
      throw error;
    } finally {
      setWorkerActionBusy(button, false);
    }
  };

  const workerRowNode = (worker) => {
    const state = managedWorkerState(worker);
    const row = document.createElement("article");
    row.className = "worker-row";
    row.dataset.state = state;
    row.setAttribute("role", "listitem");

    const heading = document.createElement("div");
    heading.className = "worker-row__heading";
    const identity = document.createElement("div");
    identity.className = "worker-row__identity";
    const name = document.createElement("strong");
    name.textContent = worker.display_name;
    const meta = document.createElement("span");
    meta.className = "worker-row__meta";
    meta.textContent = `${worker.pool} · ${workerSeenLabel(worker.last_seen_at)}`;
    identity.append(name, meta);
    const status = badge("worker-state", MANAGED_WORKER_STATE_LABELS[state] ?? state);
    status.dataset.state = state;
    heading.append(identity, status);

    const detail = document.createElement("p");
    detail.className = "worker-row__detail";
    const version = worker.version ? `버전 ${worker.version}` : "버전 정보 없음";
    const doctor = worker.doctor?.summary && worker.doctor.summary !== "ready"
      ? ` · 점검: ${worker.doctor.summary}`
      : "";
    const task = worker.current_task_id ? ` · 현재 작업 ${worker.current_task_id}` : "";
    detail.textContent = `${version}${doctor}${task}`;
    row.append(heading, detail);

    if (worker.state !== "revoked") {
      const actions = document.createElement("div");
      actions.className = "worker-row__actions";
      const toggle = document.createElement("button");
      toggle.className = "button button-secondary";
      toggle.type = "button";
      const nextState = worker.state === "draining" ? "active" : "draining";
      toggle.textContent = nextState === "active" ? "다시 활성화" : "새 작업 중지";
      toggle.addEventListener("click", () =>
        updateManagedWorkerState(worker, nextState, toggle).catch(() => undefined));

      const revoke = document.createElement("button");
      revoke.className = "button button-quiet candidate-row__action--danger";
      revoke.type = "button";
      revoke.textContent = "연결 폐기";
      revoke.addEventListener("click", () => {
        revoke.hidden = true;
        const confirmation = document.createElement("div");
        confirmation.className = "worker-revoke-confirm";
        const explanation = document.createElement("p");
        explanation.textContent = worker.current_task_id
          ? worker.display_name + "의 자격 증명을 폐기하고 현재 작업을 해제합니다. 콜백 반영 중이면 자격 증명 폐기가 거절되므로 Appium 결과를 먼저 확인하세요."
          : worker.display_name + "의 자격 증명을 폐기합니다. 이 Mac을 다시 쓰려면 새 코드로 등록해야 합니다.";
        const confirm = document.createElement("button");
        confirm.className = "button button-secondary candidate-row__action--danger";
        confirm.type = "button";
        confirm.textContent = "폐기 확정";
        confirm.addEventListener("click", () =>
          revokeManagedWorker(worker, confirm).catch(() => undefined));
        const cancel = document.createElement("button");
        cancel.className = "button button-quiet";
        cancel.type = "button";
        cancel.textContent = "취소";
        cancel.addEventListener("click", () => {
          confirmation.hidden = true;
          revoke.hidden = false;
          revoke.focus();
        });
        confirmation.append(explanation, confirm, cancel);
        row.append(confirmation);
        confirm.focus();
      });
      actions.append(toggle, revoke);
      row.append(actions);
    }
    return row;
  };

  const renderManagedWorkers = () => {
    const list = one("[data-worker-list]");
    const empty = one("[data-worker-list-empty]");
    const summary = one("[data-worker-admin-summary]");
    if (list) list.replaceChildren(...managedWorkers.map(workerRowNode));
    if (empty) empty.hidden = managedWorkers.length > 0;
    if (summary) {
      const states = managedWorkers.map(managedWorkerState);
      const available = states.filter((state) => state === "ready").length;
      const busy = states.filter((state) => state === "busy").length;
      const attention = states.filter((state) => ["degraded", "offline"].includes(state)).length;
      summary.textContent = `전체 ${managedWorkers.length}대 · 작업 가능 ${available}대 · 작업 중 ${busy}대 · 확인 필요 ${attention}대`;
    }
  };

  const scheduleWorkerAdminPoll = () => {
    if (workerAdminPoll) window.clearTimeout(workerAdminPoll);
    workerAdminPoll = null;
    if (!workerAdminToken || !workerManager?.open) return;
    workerAdminPoll = window.setTimeout(() => {
      loadManagedWorkers().catch((error) => {
        if (error.status === 401) lockWorkerManager(error.message);
        else setWorkerAdminActionFeedback(error.message);
      });
    }, 15000);
  };

  const loadManagedWorkers = async () => {
    const result = await workerAdminRequest("/v1/workers");
    managedWorkers = Array.isArray(result?.workers) ? result.workers : [];
    renderManagedWorkers();
    scheduleWorkerAdminPoll();
  };

  const clearWorkerEnrollmentResult = () => {
    if (workerEnrollmentResult) workerEnrollmentResult.hidden = true;
    if (workerEnrollmentCode) workerEnrollmentCode.textContent = "";
    if (workerEnrollmentCommand) workerEnrollmentCommand.textContent = "";
    if (workerEnrollmentExpiry) workerEnrollmentExpiry.textContent = "";
    setWorkerEnrollmentFeedback("");
  };

  const lockWorkerManager = (message = "") => {
    workerAdminToken = "";
    managedWorkers = [];
    if (workerAdminPoll) window.clearTimeout(workerAdminPoll);
    workerAdminPoll = null;
    workerAdminForm?.reset();
    workerAdminTokenField?.removeAttribute("aria-invalid");
    if (workerAdminLocked) workerAdminLocked.hidden = false;
    if (workerAdminPanel) workerAdminPanel.hidden = true;
    clearWorkerEnrollmentResult();
    setWorkerAdminFeedback(message);
    setWorkerAdminActionFeedback("");
  };

  const copyWorkerSetupText = async (
    value,
    button,
    copiedLabel,
    reportFailure = setWorkerEnrollmentFeedback,
  ) => {
    const original = button?.textContent ?? "복사";
    try {
      if (!value || typeof navigator === "undefined" || !navigator.clipboard) {
        throw new Error("clipboard unavailable");
      }
      await navigator.clipboard.writeText(value);
      if (button) {
        button.textContent = copiedLabel;
        button.dataset.state = "success";
        window.setTimeout(() => {
          button.textContent = original;
          delete button.dataset.state;
        }, 2500);
      }
    } catch {
      reportFailure("복사하지 못했습니다. 값을 직접 선택해 복사해 주세요.");
    }
  };

  if (workerAgentPrompt) workerAgentPrompt.textContent = macEnrollmentAgentPrompt();

  const selectedContextProfile = () =>
    contextProfiles.find((profile) => profile.profile_id === selectedContextProfileId) ?? null;

  const setContextFeedback = (message) => {
    if (!contextFeedback) return;
    contextFeedback.hidden = !message;
    contextFeedback.textContent = message;
  };

  const renderSelectedContext = () => {
    const profile = selectedContextProfile();
    const value = (selector, text) => {
      const element = one(selector);
      if (element) element.textContent = text;
    };
    value("[data-context-source]", profile?.source === "custom" ? "팀" : "기본");
    value("[data-context-audience]", profile?.audience ?? "사용할 수 있는 컨텍스트가 없습니다.");
    value("[data-context-situation]", profile?.situation ?? "—");
    value("[data-context-tone]", profile?.tone ?? "—");
    value("[data-context-guidance]", profile?.guidance ?? "—");
    value("[data-context-refs]", profile?.reference_ids?.join(", ") || "연결된 레퍼런스 없음");
    if (contextSelect) contextSelect.value = profile?.profile_id ?? "";
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

  const provenanceField = (label, node) => {
    const field = document.createElement("div"); field.className = "provenance__field";
    const name = document.createElement("span"); name.className = "eyebrow";
    name.textContent = label;
    field.append(name, node);
    return field;
  };

  const provenanceDocuments = (provenance) => {
    const list = document.createElement("ul"); list.className = "provenance__documents";
    provenance.documents.forEach((document_) => {
      const item = document.createElement("li"); item.className = "provenance__document mono";
      item.textContent = `context/${document_.relative_path} · ${kilobytes(document_.size_bytes)}`;
      list.append(item);
    });
    return list;
  };

  const provenanceNode = (record) => {
    const panel = document.createElement("details"); panel.className = "advanced-input provenance";
    const summary = document.createElement("summary"); summary.textContent = "🧠 생성 근거";
    const body = document.createElement("div"); body.className = "advanced-input__body";
    const provenance = record.generation_provenance;
    if (!provenance) {
      const missing = document.createElement("p"); missing.className = "provenance__missing";
      missing.textContent = record.source === "manual"
        ? "수동 등록 — 생성 근거 없음"
        : "생성 근거가 기록되지 않은 후보입니다.";
      body.append(missing);
      panel.append(summary, body);
      return panel;
    }
    const model = document.createElement("span"); model.className = "provenance__model mono";
    model.textContent = [
      provenance.model,
      `지시문 ${provenance.instruction_chars.toLocaleString("ko-KR")}자`,
      candidateDateTime(provenance.generated_at),
    ].join(" · ");
    const note = document.createElement("p"); note.className = "provenance__note";
    note.textContent = "적용 원리·참조 레퍼런스는 위 배지에 표시됩니다";
    body.append(
      provenanceField(`읽은 문서 ${provenance.documents.length}개`, provenanceDocuments(provenance)),
      provenanceField("모델", model),
    );
    const assigned = provenance.assigned_domains ?? [];
    if (assigned.length) {
      const batch = document.createElement("span");
      batch.className = "provenance__batch";
      batch.textContent = `${assigned.map(domainLabel).join(" · ")} (누적 커버리지 기준)`;
      body.append(provenanceField("이번 배치 배정", batch));
    }
    body.append(note);
    panel.append(summary, body);
    return panel;
  };

  const deleteControl = (record, compact) => {
    const wrap = document.createElement("span");
    wrap.className = compact ? "candidate-delete candidate-delete--compact" : "candidate-delete";
    let timer = null;
    const clear = () => {
      if (timer === null) return;
      window.clearTimeout(timer);
      timer = null;
    };
    const showIdle = () => {
      clear();
      const button = document.createElement("button");
      button.className = "button button-quiet candidate-delete__start";
      button.type = "button";
      button.textContent = "삭제";
      button.setAttribute("aria-label", `후보 삭제: ${record.topic || "주제 없음"}`);
      button.addEventListener("click", () => showConfirm());
      wrap.replaceChildren(button);
    };
    const showConfirm = () => {
      clear();
      const prompt = document.createElement("span");
      prompt.className = "candidate-delete__prompt";
      prompt.textContent = "정말 삭제할까요?";
      const confirm = document.createElement("button");
      confirm.className = "button candidate-delete__confirm";
      confirm.type = "button";
      confirm.textContent = "삭제 확정";
      const cancel = document.createElement("button");
      cancel.className = "button button-secondary candidate-delete__cancel";
      cancel.type = "button";
      cancel.textContent = "취소";
      confirm.addEventListener("click", () => deleteCandidate(record, confirm, [confirm, cancel]));
      cancel.addEventListener("click", () => showIdle());
      wrap.replaceChildren(prompt, confirm, cancel);
      // An armed control left alone is a trap for the next click, so it disarms itself.
      timer = window.setTimeout(showIdle, CONFIRM_REVERT_MS);
    };
    showIdle();
    return wrap;
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
    meta.textContent = `${record.country} · ${postingSlotLabel(record.posting_slot)} 슬롯 · ${candidateDate(record.created_at)}`;
    content.append(title, caption, meta);
    if (record.context_profile) {
      content.append(badge("candidate-context", `Context · ${record.context_profile.name}`));
    }
    if (record.review_rating) {
      content.append(badge("candidate-context", `최근 평가 · ${record.review_rating}점`));
    }
    content.append(journeyNode(record), provenanceNode(record));
    const trailing = document.createElement("span"); trailing.className = "candidate-row__trailing";
    trailing.append(badge(
      `candidate-status ${record.capture_state ? `capture_${record.capture_state}` : record.status}`,
      candidateStatusLabel(record),
    ));
    if (hostedCandidateControls) {
      const edit = document.createElement("button");
      edit.className = "button button-quiet candidate-row__action";
      edit.type = "button";
      edit.textContent = "수정";
      edit.addEventListener("click", () => beginCandidateEdit(record));
      trailing.append(edit);
    }
    trailing.append(deleteControl(record, false));
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

  const reviewControls = (record, stage, approveLabel) => {
    const actions = document.createElement("div");
    actions.className = "review-controls";
    const feedback = document.createElement("p");
    feedback.className = "candidate-feedback";
    feedback.setAttribute("role", "alert");
    feedback.hidden = true;

    const approve = document.createElement("button");
    approve.className = "button button-primary";
    approve.type = "button";
    approve.textContent = approveLabel;

    const rejection = document.createElement("details");
    rejection.className = "review-rejection";
    const summary = document.createElement("summary");
    summary.textContent = "반려 사유 선택";
    const body = document.createElement("div");
    body.className = "review-rejection__body";
    const fieldset = document.createElement("fieldset");
    fieldset.className = "review-tag-grid";
    const legend = document.createElement("legend");
    legend.textContent = "이유 태그 · 하나 이상";
    fieldset.append(legend);
    const tagInputs = REVIEW_TAGS_BY_STAGE[stage].map((tag, index) => {
      const label = document.createElement("label");
      label.className = "review-tag";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = tag;
      input.id = `review-${stage}-${record.candidate_id}-${index}`;
      const text = document.createElement("span");
      text.textContent = tag;
      label.append(input, text);
      fieldset.append(label);
      return input;
    });
    const ratingLabel = document.createElement("label");
    ratingLabel.className = "review-rating";
    ratingLabel.textContent = "평점";
    const rating = document.createElement("select");
    [[1, "1 · 사용 어려움"], [2, "2 · 큰 수정 필요"], [3, "3 · 보완 필요"]].forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = String(value);
      option.textContent = label;
      option.selected = value === 2;
      rating.append(option);
    });
    ratingLabel.append(rating);
    const note = document.createElement("textarea");
    note.className = "approval-card__reason";
    note.rows = 2;
    note.maxLength = 2000;
    note.placeholder = "선택 사항 · 기타를 고르면 상세 이유를 적어 주세요.";
    note.setAttribute("aria-label", "반려 상세 이유");
    const reject = document.createElement("button");
    reject.className = "button button-secondary";
    reject.type = "button";
    reject.textContent = "반려 저장";
    body.append(fieldset, ratingLabel, note, reject);
    rejection.append(summary, body);
    actions.append(approve, rejection, feedback);

    const buttons = () => [approve, reject];
    const submit = stage === "caption" ? reviewCandidate : reviewCandidateImage;
    approve.addEventListener("click", () => submit(
      record,
      true,
      { rating: 5, tags: [], note: null },
      approve,
      feedback,
      buttons,
    ));
    reject.addEventListener("click", () => submit(
      record,
      false,
      {
        rating: Number(rating.value),
        tags: tagInputs.filter((input) => input.checked).map((input) => input.value),
        note: note.value.trim() || null,
      },
      reject,
      feedback,
      buttons,
    ));
    return actions;
  };

  const approvalVisual = (record) => {
    const visual = document.createElement("div"); visual.className = "approval-visual";
    if (record.image_path) {
      const image = document.createElement("img");
      const accountQuery = selectedAccountId ? `?account_id=${encodeURIComponent(selectedAccountId)}` : "";
      image.src = `/api/candidates/${encodeURIComponent(record.candidate_id)}/image${accountQuery}`;
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
      badge("mono", `${record.country} · ${postingSlotLabel(record.posting_slot)} · ${candidateDate(record.created_at)}`),
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
      approvalField("생성 컨텍스트", record.context_profile?.name || "기록 없음"),
      approvalPrinciples(record),
      approvalField("참조", record.refs_used.length ? record.refs_used.join(", ") : "—"),
      approvalField("AI 검수", record.ai_verdict || "—"),
    );
    const order = shootingOrderNode(record);
    const actions = document.createElement("div"); actions.className = "approval-card__actions";
    actions.append(reviewControls(record, "caption", "캡션·주제 승인 · 5점"), deleteControl(record, true));
    card.append(header, journey, body, facts, order, provenanceNode(record), actions);
    return card;
  };

  const backgroundQueryNode = (record) => {
    const query = record.image_inputs?.background_search_query;
    const line = document.createElement("p"); line.className = "background-source";
    const label = document.createElement("span"); label.className = "eyebrow";
    label.textContent = "배경 검색어";
    const value = document.createElement("span");
    value.className = query ? "background-source__query mono" : "background-source__missing";
    value.textContent = query ? `“${query}”` : "기록 없음 — 배경 소재와 분위기로 자동 생성합니다";
    line.append(label, value);
    return line;
  };

  const backgroundSourceNode = (record) => {
    const provenance = record.background_provenance;
    if (!provenance) return null;
    const line = document.createElement("p"); line.className = "background-source";
    const label = document.createElement("span"); label.className = "eyebrow";
    label.textContent = "배경 출처";
    const link = document.createElement("a"); link.className = "background-source__link";
    link.href = provenance.source_url;
    link.textContent = sourceHost(provenance.source_url);
    link.title = provenance.source_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    const query = document.createElement("span"); query.className = "background-source__query mono";
    query.textContent = `“${provenance.query}”`;
    line.append(label, link, query);
    if (provenance.pipeline === "local_fallback") {
      line.append(badge("candidate-context", "로컬 합성 — 일정·시각은 그려지지 않습니다"));
    }
    return line;
  };

  const gradeText = (grades) =>
    grades ? `진정성 ${grades.authenticity} · 페르소나 ${grades.persona_fit} · 배경 ${grades.background_fit}` : "";

  const backgroundJudgmentNode = (record) => {
    const judgment = record.background_provenance?.judgment;
    if (!judgment) return null;
    const reviews = judgment.reviews ?? [];
    const gatedCount = reviews.filter((review) => review.gated).length;
    const details = document.createElement("details");
    details.className = "advanced-input background-judgment";
    const summary = document.createElement("summary");
    summary.textContent = `배경 심사 · ${reviews.length}장 검토 → ${gatedCount}장 게이트 탈락`;
    const reason = document.createElement("p");
    reason.className = "background-judgment__reason";
    reason.textContent = `채택 이유: ${judgment.reason}`;
    const list = document.createElement("ul");
    list.className = "background-judgment__list";
    for (const review of reviews) {
      const item = document.createElement("li");
      item.className = "background-judgment__item";
      if (review.image_id === judgment.chosen_id) item.classList.add("is-chosen");
      const host = document.createElement("a");
      host.className = "background-source__link";
      host.href = review.source_url;
      host.textContent = sourceHost(review.source_url);
      host.title = review.source_url;
      host.target = "_blank";
      host.rel = "noopener noreferrer";
      const outcome = document.createElement("span");
      outcome.className = "background-judgment__outcome";
      outcome.textContent = review.gated
        ? `게이트 탈락 — ${review.gate_reason ?? "사유 없음"}`
        : `${gradeText(review.grades)} (${review.score ?? 0}점)`;
      item.append(host, outcome);
      list.append(item);
    }
    details.append(summary, reason, list);
    if (judgment.tie_break_inconsistent) {
      const flipped = document.createElement("p");
      flipped.className = "background-judgment__reason";
      flipped.textContent = "동점 비교가 순서에 따라 뒤집혀, 심사 총점이 높은 쪽을 썼습니다.";
      details.append(flipped);
    }
    const attempts = judgment.attempts ?? [];
    if (attempts.length > 1) {
      const tried = document.createElement("p");
      tried.className = "background-judgment__reason";
      tried.textContent = `시도한 검색어 ${attempts.length}개: ${attempts
        .map((attempt) => `“${attempt.query}” (${QUERY_SOURCE_LABELS[attempt.source] ?? attempt.source}) → 결과 ${attempt.results}건 · 통과 ${attempt.passed_filters}장`)
        .join(" / ")}`;
      details.append(tried);
    } else if (judgment.rewritten_query) {
      const rewritten = document.createElement("p");
      rewritten.className = "background-judgment__reason";
      rewritten.textContent = `검색어를 다시 써서 재검색했습니다: “${judgment.rewritten_query}”`;
      details.append(rewritten);
    }
    return details;
  };

  const shootingOrderNode = (record) => {
    const order = document.createElement("details"); order.className = "advanced-input";
    const summary = document.createElement("summary"); summary.textContent = "Appium 프롬프트";
    const body = document.createElement("pre"); body.className = "approval-card__order";
    body.textContent = record.shooting_order || "Appium 프롬프트가 비어 있습니다.";
    order.append(summary, body);
    return order;
  };

  const imageSummary = (record) => {
    const text = document.createElement("div"); text.className = "approval-card__text";
    const label = document.createElement("span"); label.className = "eyebrow";
    label.textContent = "주제/컨셉";
    const topic = document.createElement("h3"); topic.className = "approval-card__topic";
    topic.textContent = record.topic || "(주제 없음)";
    const caption = document.createElement("p"); caption.className = "approval-card__caption";
    caption.textContent = record.caption;
    text.append(label, topic, caption, backgroundQueryNode(record));
    const source = backgroundSourceNode(record);
    if (source) text.append(source);
    const judgment = backgroundJudgmentNode(record);
    if (judgment) text.append(judgment);
    return text;
  };

  const imageNode = (record) => {
    const card = document.createElement("article"); card.className = "approval-card";
    const header = document.createElement("div"); header.className = "approval-card__header";
    header.append(
      badge("candidate-source", candidateSourceLabel(record.source)),
      badge(
        `candidate-status ${record.capture_state ? `capture_${record.capture_state}` : record.status}`,
        candidateStatusLabel(record),
      ),
    );
    const body = document.createElement("div"); body.className = "approval-card__body";
    body.append(imageSummary(record), approvalVisual(record));
    const actions = document.createElement("div"); actions.className = "approval-card__actions";
    const feedback = document.createElement("p");
    feedback.className = "candidate-feedback";
    feedback.setAttribute("role", "alert");
    feedback.hidden = true;
    const order = shootingOrderNode(record);
    if (record.status === "caption_approved") {
      if (record.capture_state === "queued") {
        const waiting = document.createElement("p");
        waiting.className = "candidate-feedback";
        waiting.textContent = "온라인 Mac worker가 작업 lease를 가져가면 Appium 캡처가 시작됩니다. 완료되면 이 카드가 자동으로 갱신됩니다.";
        actions.append(waiting);
      } else {
        const button = document.createElement("button");
        button.className = "button button-primary";
        button.type = "button";
        button.textContent = record.capture_state === "failed" ? "Mac 캡처 다시 시도" : "Mac에서 이미지 생성";
        button.addEventListener("click", () => generateCandidateImage(record, button, feedback));
        actions.append(button);
      }
      if (record.capture_state === "failed") {
        const failure = approvalField("캡처 실패 코드", record.capture_error || "native_capture_failed");
        card.append(header, body, failure, order, actions, feedback);
        return card;
      }
      if (record.review_note) {
        const note = approvalField("직전 반려 사유", record.review_note);
        card.append(header, body, note, order, actions, feedback);
        return card;
      }
    } else {
      actions.append(reviewControls(record, "image", "이미지 승인 · 5점"));
    }
    card.append(header, body, order, actions, feedback);
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
    button.textContent = "Mac 작업 등록 중…";
    setCardFeedback(feedback, "");
    setBusy(button, true, "잠금화면 이미지를 만드는 중… (1~3분 소요)");
    try {
      await request(`/api/candidates/${encodeURIComponent(record.candidate_id)}/generate-image`, {
        method: "POST",
      });
      await Promise.all([loadCandidates(), loadFeedbackSummary()]);
      setNotice("Mac 캡처 작업을 등록했습니다. 완료되면 이미지 검수 카드가 자동으로 갱신됩니다.");
    } catch (error) {
      setCardFeedback(feedback, error.message);
      setNotice(error.message);
    } finally {
      button.disabled = false;
      button.textContent = label;
      setBusy(button, false);
    }
  };

  const reviewCandidateImage = async (record, accepted, review, target, feedback, siblings) => {
    const disabled = siblings();
    disabled.forEach((button) => { button.disabled = true; });
    setCardFeedback(feedback, "");
    setBusy(target, true, "이미지 검수 결과를 저장하는 중…");
    try {
      await request(`/api/candidates/${encodeURIComponent(record.candidate_id)}/review-image`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accepted, ...review, expected_revision: record.revision }),
      });
      await Promise.all([loadCandidates(), loadFeedbackSummary()]);
      setNotice(accepted ? "제출 준비가 끝났습니다." : "이미지를 반려했습니다. 다시 생성할 수 있습니다.");
    } catch (error) {
      setCardFeedback(feedback, error.message);
      setNotice(error.message);
      disabled.forEach((button) => { button.disabled = false; });
    } finally {
      setBusy(target, false);
    }
  };

  const reviewCandidate = async (record, accepted, review, target, feedback, siblings) => {
    const disabled = siblings();
    disabled.forEach((button) => { button.disabled = true; });
    setCardFeedback(feedback, "");
    setBusy(target, true, "검수 결과를 저장하는 중…");
    try {
      await request(`/api/candidates/${encodeURIComponent(record.candidate_id)}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accepted, ...review, expected_revision: record.revision }),
      });
      await Promise.all([loadCandidates(), loadFeedbackSummary()]);
      setNotice(accepted ? "주제와 캡션을 승인했습니다. 이미지 승인 단계로 넘어갑니다." : "후보를 반려했습니다.");
    } catch (error) {
      setCardFeedback(feedback, error.message);
      setNotice(error.message);
      disabled.forEach((button) => { button.disabled = false; });
    } finally {
      setBusy(target, false);
    }
  };


  const renderCandidateList = () => {
    one("[data-candidate-list]")?.replaceChildren(...candidateRecords.map(candidateNode));
    const count = one("[data-candidate-count]");
    if (count) count.textContent = `후보 ${candidateRecords.length}개`;
    if (candidateEmpty) candidateEmpty.hidden = candidateRecords.length > 0;
    const emptyTitle = one("[data-candidate-empty-title]");
    const emptyCopy = one("[data-candidate-empty-copy]");
    if (emptyTitle) emptyTitle.textContent = "등록된 후보가 없습니다";
    if (emptyCopy) emptyCopy.textContent = "오늘 후보 4개 생성을 눌러 첫 작업을 만드세요.";
  };


  const loadCandidates = async () => {
    // Scoped to the open account: another account's drafts are another person's.
    const listPath = currentAccount
      ? `/api/candidates?account_id=${encodeURIComponent(currentAccount.account_id)}`
      : "/api/candidates";
    const records = await request(listPath);
    candidateRecords = records;
    renderCandidateList();
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
      const generationPending = imageStage.filter((record) => record.status === "caption_approved");
      const reviewPending = imageStage.filter((record) => record.status === "image_awaiting_review");
      const queued = generationPending.filter((record) => record.capture_state === "queued").length;
      const failed = generationPending.filter((record) => record.capture_state === "failed").length;
      const ready = generationPending.length - queued - failed;
      imageCount.textContent = `생성 가능 ${ready}건 · Mac 대기·실행 ${queued}건 · 실패 ${failed}건 · 검수 대기 ${reviewPending.length}건`;
    }
    if (capturePoll) window.clearTimeout(capturePoll);
    if (records.some((record) => record.capture_state === "queued")) {
      capturePoll = window.setTimeout(() => {
        loadCandidates().catch((error) => setNotice(error.message));
      }, 5000);
    }
  };

  const contextProfileNode = (profile) => {
    const row = document.createElement("article");
    row.className = "context-profile-row";
    const content = document.createElement("div");
    content.className = "context-profile-row__content";
    const name = document.createElement("strong");
    name.textContent = profile.name;
    const meta = document.createElement("span");
    meta.textContent = `${profile.country} · ${profile.persona_id} · ${profile.source === "custom" ? "팀 추가" : "기본"}`;
    content.append(name, meta);
    const actions = document.createElement("div");
    actions.className = "context-profile-row__actions";
    const edit = document.createElement("button");
    edit.className = "button button-quiet";
    edit.type = "button";
    edit.textContent = "수정";
    edit.addEventListener("click", () => beginContextEdit(profile));
    const remove = document.createElement("button");
    remove.className = "button button-quiet candidate-row__action--danger";
    remove.type = "button";
    remove.textContent = "숨기기";
    remove.addEventListener("click", () => deleteContextProfile(profile, remove));
    actions.append(edit, remove);
    row.append(content, actions);
    return row;
  };

  const renderContextProfiles = () => {
    if (contextSelect) {
      const options = contextProfiles.map((profile) => {
        const option = document.createElement("option");
        option.value = profile.profile_id;
        option.textContent = `${profile.name} · ${profile.country}`;
        return option;
      });
      contextSelect.replaceChildren(...options);
    }
    one("[data-context-profile-list]")?.replaceChildren(...contextProfiles.map(contextProfileNode));
    renderSelectedContext();
  };

  const renderContextCountries = () => {
    const displayNames = typeof Intl?.DisplayNames === "function"
      ? new Intl.DisplayNames(["ko"], { type: "region" })
      : null;
    const countryOption = ({ country }) => {
      const option = document.createElement("option");
      option.value = country;
      option.textContent = `${displayNames?.of(country) ?? country} (${country})`;
      return option;
    };
    const currentCountry = selectedHostedAccount()?.country;
    const scopedCountries = currentCountry
      ? contextCountries.filter(({ country }) => country === currentCountry)
      : contextCountries;
    [document.getElementById("context-country"), document.getElementById("candidate-country")]
      .filter(Boolean)
      .forEach((select) => select.replaceChildren(...scopedCountries.map(countryOption)));
    const newAccountCountry = document.getElementById("new-account-country");
    if (newAccountCountry) {
      const previous = newAccountCountry.value;
      newAccountCountry.replaceChildren(...contextCountries.map(countryOption));
      if (contextCountries.some(({ country }) => country === previous)) newAccountCountry.value = previous;
      const timezone = document.getElementById("new-account-timezone");
      if (timezone && !timezone.value) timezone.value = COUNTRY_TIMEZONES[newAccountCountry.value] ?? "UTC";
    }
  };

  const selectedHostedAccount = () =>
    hostedAccounts.find((account) => account.account_id === selectedAccountId) ?? null;

  const setAccountFeedback = (element, message) => {
    if (!element) return;
    element.hidden = !message;
    element.textContent = message;
  };

  const renderHostedAccounts = () => {
    const account = selectedHostedAccount();
    if (accountSelect) {
      const options = hostedAccounts.map((item) => {
        const option = document.createElement("option");
        option.value = item.account_id;
        option.textContent = `${item.display_name} · ${item.country}`;
        return option;
      });
      accountSelect.replaceChildren(...options);
      accountSelect.value = account?.account_id ?? "";
    }
    const text = (selector, value) => {
      const element = one(selector);
      if (element) element.textContent = value;
    };
    text("[data-workspace-account]", account?.account_id ?? "—");
    text("[data-account-market]", account ? `${account.country} · ${account.language} · ${account.timezone}` : "—");
    text("[data-account-slots]", account ? `오전 ${account.morning_time} · 저녁 ${account.evening_time}` : "—");
    const next = account?.next_generation_at
      ? new Intl.DateTimeFormat("ko-KR", {
        timeZone: account.timezone,
        month: "numeric",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }).format(new Date(account.next_generation_at))
      : null;
    text(
      "[data-account-automation]",
      account?.generation_enabled ? `사용 · 다음 ${next}` : "중지 · 수동 생성만",
    );
    if (account) {
      const values = {
        "account-display-name": account.display_name,
        "account-timezone": account.timezone,
        "account-morning-time": account.morning_time,
        "account-evening-time": account.evening_time,
      };
      Object.entries(values).forEach(([id, value]) => {
        const field = document.getElementById(id);
        if (field) field.value = value;
      });
      const enabled = document.getElementById("account-generation-enabled");
      if (enabled) enabled.checked = account.generation_enabled;
    }
    renderContextCountries();
  };

  const loadHostedAccounts = async () => {
    if (!hostedCandidateControls) return;
    hostedAccounts = await request("/api/accounts");
    let remembered = "";
    try { remembered = window.localStorage.getItem("trace:hosted-account") ?? ""; } catch (error) {
      if (!(error instanceof DOMException)) throw error;
    }
    const preferred = remembered || selectedAccountId;
    selectedAccountId = hostedAccounts.some((account) => account.account_id === preferred)
      ? preferred
      : hostedAccounts[0]?.account_id ?? "";
    try { window.localStorage.setItem("trace:hosted-account", selectedAccountId); } catch (error) {
      if (!(error instanceof DOMException)) throw error;
    }
    renderHostedAccounts();
  };

  const renderFeedbackSummary = () => {
    const summary = one("[data-feedback-learning]");
    const tags = one("[data-feedback-tags]");
    if (!summary || !tags) return;
    if (!feedbackSignal || feedbackSignal.rejected_reviews === 0) {
      summary.textContent = "아직 누적된 반려 신호가 없습니다. 반려 태그는 같은 계정·페르소나의 다음 생성에 사용됩니다.";
      tags.replaceChildren();
      return;
    }
    summary.textContent = feedbackSignal.rule_candidates.length
      ? `같은 단계의 강한 반려로 확인된 규칙 ${feedbackSignal.rule_candidates.length}개가 다음 생성에 자동 반영됩니다.`
      : `반려 ${feedbackSignal.rejected_reviews}건이 누적되었습니다. 같은 단계·태그의 1~2점 반려가 서로 다른 후보 revision에서 3회 확인되면 생성 규칙이 됩니다.`;
    tags.replaceChildren(...feedbackSignal.top_tags.slice(0, 6).map(({ tag, count }) =>
      badge("approval-badge", `${tag} · ${count}`)));
  };

  const loadFeedbackSummary = async () => {
    if (!hostedCandidateControls) return;
    const profile = selectedContextProfile();
    const query = profile ? `?context_profile_id=${encodeURIComponent(profile.profile_id)}` : "";
    feedbackSignal = await request(`/api/feedback-summary${query}`);
    renderFeedbackSummary();
  };

  const loadContextProfiles = async () => {
    if (!hostedCandidateControls) return;
    const previous = selectedContextProfileId;
    [contextCountries, contextProfiles] = await Promise.all([
      request("/api/context-countries"),
      request("/api/context-profiles"),
    ]);
    selectedContextProfileId = contextProfiles.some((profile) => profile.profile_id === previous)
      ? previous
      : (contextProfiles.find((profile) => profile.is_default) ?? contextProfiles[0])?.profile_id ?? "";
    renderContextCountries();
    renderContextProfiles();
  };

  const cancelContextEdit = () => {
    editingContextProfile = null;
    contextForm?.reset();
    if (contextFormTitle) contextFormTitle.textContent = "새 컨텍스트 추가";
    if (contextSubmit) contextSubmit.textContent = "컨텍스트 추가";
    if (contextCancel) contextCancel.hidden = true;
    setContextFeedback("");
  };

  const beginContextEdit = (profile) => {
    editingContextProfile = profile;
    const manager = one("[data-context-manager]");
    if (manager) manager.open = true;
    if (contextFormTitle) contextFormTitle.textContent = "컨텍스트 수정";
    if (contextSubmit) contextSubmit.textContent = "수정 저장";
    if (contextCancel) contextCancel.hidden = false;
    const values = {
      "context-name": profile.name,
      "context-persona-id": profile.persona_id,
      "context-country": profile.country,
      "context-tone": profile.tone,
      "context-audience": profile.audience,
      "context-situation": profile.situation,
      "context-guidance": profile.guidance,
      "context-refs": profile.reference_ids.join(", "),
    };
    Object.entries(values).forEach(([id, value]) => {
      const field = document.getElementById(id);
      if (field) field.value = value;
    });
    setContextFeedback("");
    document.getElementById("context-name")?.focus({ preventScroll: true });
  };

  const deleteContextProfile = async (profile, button) => {
    if (!window.confirm(`“${profile.name}” 컨텍스트를 목록에서 숨길까요? 기존 후보의 생성 기록은 유지됩니다.`)) return;
    button.disabled = true;
    setBusy(button, true, "컨텍스트를 숨기는 중…");
    try {
      await request(`/api/context-profiles/${encodeURIComponent(profile.profile_id)}`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_revision: profile.revision }),
      });
      if (editingContextProfile?.profile_id === profile.profile_id) cancelContextEdit();
      await loadContextProfiles();
      setNotice("컨텍스트를 숨겼습니다. 기존 후보의 생성 기록은 유지됩니다.");
    } catch (error) {
      setNotice(error.message);
      button.disabled = false;
    } finally {
      setBusy(button, false);
    }
  };

  const commaList = (value) => String(value ?? "").split(",").map((item) => item.trim()).filter(Boolean);
  const lineList = (value) => String(value ?? "").split("\n").map((item) => item.trim()).filter(Boolean);

  const candidateField = (id) => document.getElementById(id);

  const cancelCandidateEdit = () => {
    editingCandidate = null;
    editingCandidateContextChanged = false;
    candidateForm?.reset();
    if (candidateFormTitle) candidateFormTitle.textContent = "수동 등록";
    if (candidateSubmit) candidateSubmit.textContent = "후보 등록";
    if (candidateCancel) candidateCancel.hidden = true;
    if (candidateEditNote) candidateEditNote.hidden = true;
    setCandidateFeedback("");
    const profile = selectedContextProfile();
    if (profile) candidateField("candidate-country").value = profile.country;
  };

  const beginCandidateEdit = (record) => {
    editingCandidate = record;
    editingCandidateContextChanged = false;
    const recordedProfileId = record.context_profile?.profile_id;
    if (recordedProfileId && contextProfiles.some((profile) => profile.profile_id === recordedProfileId)) {
      selectedContextProfileId = recordedProfileId;
      renderSelectedContext();
    }
    if (manualEntry) manualEntry.open = true;
    if (candidateFormTitle) candidateFormTitle.textContent = "후보 수정";
    if (candidateSubmit) candidateSubmit.textContent = "수정 저장";
    if (candidateCancel) candidateCancel.hidden = false;
    if (candidateEditNote) candidateEditNote.hidden = false;
    candidateField("candidate-topic").value = record.topic;
    candidateField("candidate-country").value = record.country;
    candidateField("candidate-posting-slot").value = record.posting_slot || "manual";
    candidateField("candidate-hypothesis").value = record.hypothesis;
    candidateField("candidate-caption").value = record.caption;
    candidateField("candidate-refs").value = record.refs_used.join(", ");
    candidateField("candidate-principles").value = record.principles_applied.join(", ");
    candidateField("candidate-shooting-order").value = record.shooting_order || "";
    candidateField("candidate-schedule").value = record.image_inputs.trace_items.join("\n");
    candidateField("candidate-device-time").value = record.image_inputs.device_time;
    candidateField("candidate-background-subject").value = record.image_inputs.background_subject;
    candidateField("candidate-background-mood").value = record.image_inputs.background_mood;
    candidateField("candidate-background-query").value =
      record.image_inputs.background_search_query ?? "";
    candidateField("candidate-persona-domain").value = record.persona_domain ?? "";
    setCandidateFeedback("");
    manualEntry?.scrollIntoView({ behavior: "smooth", block: "start" });
    candidateField("candidate-topic")?.focus({ preventScroll: true });
  };

  // Confirmation is the two-step control in `deleteControl`, not a modal: a browser confirm
  // cannot be styled, cannot be reached by the static harness, and disappears entirely in a
  // hosted iframe. The revision still travels because the hosted worker requires it.
  const deleteCandidate = async (record, target, siblings) => {
    siblings.forEach((button) => { button.disabled = true; });
    setBusy(target, true, "후보를 삭제하는 중…");
    try {
      await request(`/api/candidates/${encodeURIComponent(record.candidate_id)}`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_revision: record.revision }),
      });
      if (editingCandidate?.candidate_id === record.candidate_id) cancelCandidateEdit();
      await loadCandidates();
      setNotice("후보를 삭제했습니다.");
    } catch (error) {
      setNotice(error.message);
      siblings.forEach((button) => { button.disabled = false; });
    } finally {
      setBusy(target, false);
    }
  };


  // ---- 국가 홈 · 계정 홈 ---------------------------------------------------
  // 화면은 세 층이다: 국가 → 그 국가의 계정 → 그 계정의 작업 화면. 계정 하나가 하나의
  // 컨셉이고, 컨셉은 어느 시장에서 쓰는지가 정해진 다음에야 뜻이 생기기 때문에 국가가
  // 가장 위에 온다. 세 층을 같은 페이지에서 번갈아 보여 주는 것은 그래서다.
  const countryHome = one("[data-country-home]");
  const countryGrid = one("[data-country-grid]");
  const countryEmpty = one("[data-country-empty]");
  const countryCount = one("[data-country-count]");
  const countryBack = one("[data-country-back]");
  const countryAddAccount = one("[data-country-add-account]");
  const countryCurrentName = one("[data-country-current-name]");
  const accountHome = one("[data-account-home]");
  const accountWorkspace = one("[data-account-workspace]");
  const accountGrid = one("[data-account-grid]");
  const accountEmpty = one("[data-account-empty]");
  const accountCount = one("[data-account-count]");
  const accountBack = one("[data-account-back]");
  const accountCurrentName = one("[data-account-current-name]");
  const accountCurrentConcept = one("[data-account-current-concept]");
  const accountVerdict = one("[data-account-verdict]");
  const accountForm = one("[data-account-form]");
  const accountFormDetails = one("[data-account-form-details]");
  const accountFormFeedback = one("[data-account-feedback]");

  const BACKGROUND_LABELS = Object.freeze({
    character_kitty: "캐릭터(고양이)", character_other: "캐릭터(기타)", family_photo: "가족 사진",
    person: "인물", pet: "반려동물", scenery: "풍경", minimal: "미니멀",
    sports_team: "스포츠 팀", none: "없음",
  });
  const FONT_LABELS = Object.freeze({
    sf_pro: "SF Pro", sf_pro_rounded: "SF Pro Rounded", sf_compact: "SF Compact",
    new_york: "New York", sf_mono: "SF Mono",
  });
  const ACCOUNT_STATUS_LABELS = Object.freeze({
    proposed: "제안됨", observing: "관찰", active: "활성", retired: "폐기",
  });
  const ACCOUNT_ZONES = Object.freeze({
    KR: "Asia/Seoul", JP: "Asia/Tokyo", TW: "Asia/Taipei", US: "America/Los_Angeles",
  });

  let accounts = [];
  let currentAccount = null;
  // 레벨 2가 어느 국가로 좁혀져 있는지. null이면 국가 홈에 있거나, 아직 국가가 하나도
  // 없어 첫 계정을 만드는 중이다.
  let activeCountry = null;

  const fillOptions = (select, labels) => {
    if (!select || select.options.length) return;
    Object.entries(labels).forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value; option.textContent = label;
      select.append(option);
    });
  };

  const setAccountFormFeedback = (message) => {
    if (!accountFormFeedback) return;
    accountFormFeedback.textContent = message;
    accountFormFeedback.hidden = !message;
  };

  const accountCard = (account) => {
    const card = document.createElement("article");
    card.className = "account-card";
    const head = document.createElement("div");
    head.className = "account-card__head";
    const badges = document.createElement("div");
    badges.className = "account-card__badges";
    const country = document.createElement("span");
    country.className = "candidate-source"; country.textContent = account.country;
    const state = document.createElement("span");
    state.className = `candidate-status ${account.status}`;
    state.textContent = ACCOUNT_STATUS_LABELS[account.status] ?? account.status;
    badges.append(country, state);
    const name = document.createElement("strong");
    name.className = "account-card__name";
    name.textContent = account.display_name;
    head.append(name, badges);

    const concept = document.createElement("p");
    concept.className = "account-card__concept";
    concept.textContent = account.identity?.concept ?? "";

    const meta = document.createElement("p");
    meta.className = "mono account-card__meta";
    const identity = account.identity;
    meta.textContent = identity
      ? `${identity.age}세 · ${identity.region} · ${identity.occupation} · ${domainLabel(identity.domain)}`
      : account.language;

    const open = document.createElement("button");
    open.className = "button button-primary";
    open.type = "button";
    open.textContent = "이 계정으로 작업";
    open.addEventListener("click", () => enterAccount(account));

    card.append(head, concept, meta, open);
    return card;
  };

  const accountsInCountry = (country) => accounts.filter((account) => account.country === country);

  const renderAccounts = () => {
    if (!accountGrid) return;
    // 국가를 고르고 들어온 목록이면 그 국가의 계정만 남는다. 국가 없이 열린 목록(첫 계정을
    // 만드는 중)에서는 가진 계정을 전부 보여 준다.
    const visible = activeCountry ? accountsInCountry(activeCountry) : accounts;
    accountGrid.replaceChildren(...visible.map(accountCard));
    if (accountEmpty) accountEmpty.hidden = visible.length > 0;
    if (accountCount) {
      accountCount.textContent = visible.length
        ? `계정 ${visible.length}개`
        : "등록된 계정이 없습니다";
    }
  };

  // 국가는 따로 저장하지 않는다. 계정의 country 값이 곧 그 국가가 쓰이고 있다는 증거라서,
  // 목록은 계정에서 파생한다. 새 국가는 그 국가의 첫 계정과 함께 생긴다.
  const countryCodes = () => [...new Set(accounts.map((account) => account.country))].sort();

  const countryCard = (country) => {
    const members = accountsInCountry(country);
    const card = document.createElement("article");
    card.className = "country-card";
    const head = document.createElement("div");
    head.className = "account-card__head";
    const name = document.createElement("strong");
    name.className = "account-card__name";
    name.textContent = countryLabel(country);
    const badges = document.createElement("div");
    badges.className = "account-card__badges";
    const code = document.createElement("span");
    code.className = "candidate-source";
    code.textContent = country;
    badges.append(code);
    head.append(name, badges);

    const count = document.createElement("p");
    count.className = "account-card__concept";
    count.textContent = `계정 ${members.length}개`;

    const meta = document.createElement("p");
    meta.className = "mono account-card__meta";
    meta.textContent = members.map((account) => account.display_name).join(" · ");

    const open = document.createElement("button");
    open.className = "button button-primary";
    open.type = "button";
    open.textContent = "이 국가로 작업";
    open.addEventListener("click", () => enterCountry(country));

    card.append(head, count, meta, open);
    return card;
  };

  const renderCountries = () => {
    if (!countryGrid) return;
    const codes = countryCodes();
    countryGrid.replaceChildren(...codes.map(countryCard));
    if (countryEmpty) countryEmpty.hidden = codes.length > 0;
    if (countryCount) {
      countryCount.textContent = codes.length
        ? `국가 ${codes.length}개`
        : "등록된 국가가 없습니다";
    }
  };

  const renderAccountVerdict = () => {
    if (!accountVerdict) return;
    accountVerdict.replaceChildren();
    if (!currentAccount) return;
    const next = currentAccount.status === "active" ? "retired" : "active";
    const button = document.createElement("button");
    button.className = "button button-quiet";
    button.type = "button";
    button.textContent = next === "active" ? "활성으로 승격" : "폐기";
    button.addEventListener("click", async () => {
      setBusy(button, true, "계정 상태를 바꾸는 중…");
      try {
        const updated = await request(
          `/api/accounts/${encodeURIComponent(currentAccount.account_id)}/status`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: next, expected_revision: currentAccount.revision }),
          },
        );
        currentAccount = updated;
        accounts = accounts.map((item) => item.account_id === updated.account_id ? updated : item);
        renderAccounts(); renderAccountVerdict();
        setNotice(`계정을 ${ACCOUNT_STATUS_LABELS[updated.status]} 상태로 바꿨습니다.`);
      } catch (error) { setNotice(error.message); }
      finally { setBusy(button, false); }
    });
    accountVerdict.append(button);
  };

  // 레벨 1. 국가를 벗어나면 열려 있던 계정도 함께 닫힌다.
  const showCountryHome = () => {
    currentAccount = null;
    activeCountry = null;
    if (countryHome) countryHome.hidden = false;
    if (accountHome) accountHome.hidden = true;
    if (accountWorkspace) accountWorkspace.hidden = true;
    renderCountries();
    renderAutogenButtons();
  };

  // 레벨 2. 어느 국가로 좁혔는지는 activeCountry가 들고 있다.
  const showAccountHome = () => {
    currentAccount = null;
    if (countryHome) countryHome.hidden = true;
    if (accountHome) accountHome.hidden = false;
    if (accountWorkspace) accountWorkspace.hidden = true;
    if (countryCurrentName) {
      countryCurrentName.textContent = activeCountry ? countryLabel(activeCountry) : "새 국가";
    }
    renderAccounts();
    renderAutogenButtons();
  };

  const enterCountry = (country) => {
    activeCountry = country;
    // 이 국가에 계정을 하나 더 붙이는 것이 여기서 가장 흔한 다음 일이라, 추가 폼의 국가도
    // 같이 맞춰 둔다.
    const countryField = document.getElementById("account-country");
    if (countryField && country) countryField.value = country;
    showAccountHome();
    setNotice(`${countryLabel(country)} 계정 목록입니다.`);
  };

  const enterAccount = async (account) => {
    currentAccount = account;
    activeCountry = account.country;
    selectedAccountId = account.account_id;
    if (countryHome) countryHome.hidden = true;
    if (accountHome) accountHome.hidden = true;
    if (accountWorkspace) accountWorkspace.hidden = false;
    if (accountCurrentName) accountCurrentName.textContent = account.display_name;
    if (accountCurrentConcept) {
      accountCurrentConcept.textContent = account.identity?.concept ?? account.country;
    }
    renderAccountVerdict();
    renderAutogenButtons();
    setBusy(workspaceLive, true, `${account.display_name} 계정을 여는 중…`);
    try {
      await Promise.all([loadCandidates(), loadFeedbackSummary()]);
      setNotice(`${account.display_name} 계정으로 작업합니다.`);
    } catch (error) { setNotice(error.message); }
    finally { setBusy(workspaceLive, false); }
  };

  const loadAccounts = async () => {
    // The account home is part of the full shell; a surface rendered without it (the
    // hosted build strips sections it does not serve) should not pay for the request.
    if (!accountGrid) return;
    fillOptions(one("[data-account-domain]"), PERSONA_DOMAIN_LABELS);
    fillOptions(one("[data-account-background-subject]"), BACKGROUND_LABELS);
    fillOptions(one("[data-account-font]"), FONT_LABELS);
    accounts = await request("/api/accounts");
    renderAccounts();
    renderCountries();
    if (currentAccount) {
      const refreshed = accounts.find((item) => item.account_id === currentAccount.account_id);
      if (refreshed) { currentAccount = refreshed; renderAccountVerdict(); }
      else showAccountHome();
    }
  };

  accountBack?.addEventListener("click", () => {
    showAccountHome();
    setNotice("계정 목록으로 돌아왔습니다.");
  });

  countryBack?.addEventListener("click", () => {
    showCountryHome();
    setNotice("국가 목록으로 돌아왔습니다.");
  });

  countryAddAccount?.addEventListener("click", () => {
    // 계정이 하나도 없으면 고를 국가도 없다. 폼에서 국가를 고르는 것이 곧 그 국가를 여는
    // 일이라, 국가 없이 계정 목록으로 내려보내고 추가 폼을 펼쳐 준다.
    activeCountry = null;
    showAccountHome();
    if (accountFormDetails) accountFormDetails.open = true;
  });

  accountForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const target = event.currentTarget;
    if (!target.checkValidity()) { target.reportValidity(); return; }
    const form = new FormData(target);
    const country = String(form.get("country") ?? "KR");
    const interests = String(form.get("interests") ?? "")
      .split(",").map((value) => value.trim()).filter(Boolean);
    if (!interests.length) {
      setAccountFormFeedback("관심사를 한 개 이상 입력해 주세요.");
      return;
    }
    setAccountFormFeedback("");
    setBusy(target, true, "계정을 추가하는 중…");
    try {
      const created = await request("/api/accounts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          country,
          identity: {
            display_name: String(form.get("display-name") ?? "").trim(),
            age: Number(form.get("age")),
            region: String(form.get("region") ?? "").trim(),
            occupation: String(form.get("occupation") ?? "").trim(),
            concept: String(form.get("concept") ?? "").trim(),
            domain: String(form.get("domain") ?? ""),
            interests,
            life_rhythm: String(form.get("life-rhythm") ?? "").trim(),
            taste: {
              background_subject: String(form.get("background-subject") ?? ""),
              background_mood: String(form.get("background-mood") ?? "").trim(),
              font: String(form.get("font") ?? ""),
            },
          },
          schedule: {
            language: countryLanguage(country),
            timezone: ACCOUNT_ZONES[country] ?? "UTC",
          },
        }),
      });
      target.reset();
      if (accountFormDetails) accountFormDetails.open = false;
      // 첫 계정이면 그 계정의 국가가 방금 생긴 국가다. 만든 사람을 국가 홈으로 되돌리는
      // 대신 그 국가의 계정 목록에 세워 둔다.
      activeCountry = created.country;
      await loadAccounts();
      showAccountHome();
      setNotice(`${created.display_name} 계정을 추가했습니다.`);
    } catch (error) {
      setAccountFormFeedback(error.message);
      setNotice(error.message);
    } finally { setBusy(target, false); }
  });

  const refreshWorkspace = async () => {
    setBusy(workspaceLive, true, "워크스페이스를 새로고침하는 중…");
    try {
      await loadAccounts();
      // 새로 입장하면 언제나 맨 위 층부터다.
      if (!currentAccount) showCountryHome();
      await loadHostedAccounts();
      await loadContextProfiles();
      await Promise.all([
        loadCandidates(),
        loadFeedbackSummary(),
        loadMacWorkerStatus().catch((error) => setNotice(error.message)),
      ]);
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

  workerManagerOpen?.addEventListener("click", () => {
    lockWorkerManager();
    workerManager?.showModal();
    workerAdminTokenField?.focus();
  });

  workerManagerClose?.addEventListener("click", () => workerManager?.close());
  workerManager?.addEventListener("close", () => lockWorkerManager());
  workerManager?.addEventListener("click", (event) => {
    if (event.target === workerManager) workerManager.close();
  });

  workerAdminForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const target = event.currentTarget;
    const form = new FormData(target);
    const token = String(form.get("control-token") ?? "").trim();
    workerAdminTokenField?.removeAttribute("aria-invalid");
    setWorkerAdminFeedback("");
    if (!token) {
      workerAdminTokenField?.setAttribute("aria-invalid", "true");
      setWorkerAdminFeedback("Cloudflare CONTROL_PLANE_TOKEN 값을 입력해 주세요.");
      workerAdminTokenField?.focus();
      return;
    }
    workerAdminToken = token;
    target.reset();
    setWorkerActionBusy(workerAdminSubmit, true, "확인 중…");
    try {
      await loadManagedWorkers();
      if (workerAdminLocked) workerAdminLocked.hidden = true;
      if (workerAdminPanel) workerAdminPanel.hidden = false;
    } catch (error) {
      workerAdminToken = "";
      workerAdminTokenField?.setAttribute("aria-invalid", "true");
      setWorkerAdminFeedback(error.message);
      workerAdminTokenField?.focus();
    } finally {
      setWorkerActionBusy(workerAdminSubmit, false);
    }
  });

  workerAdminRefresh?.addEventListener("click", async () => {
    setWorkerAdminActionFeedback("");
    setWorkerActionBusy(workerAdminRefresh, true, "새로고침 중…");
    try {
      await loadManagedWorkers();
    } catch (error) {
      if (error.status === 401) lockWorkerManager(error.message);
      else setWorkerAdminActionFeedback(error.message);
    } finally {
      setWorkerActionBusy(workerAdminRefresh, false);
    }
  });

  workerAdminLock?.addEventListener("click", () => {
    lockWorkerManager();
    workerAdminTokenField?.focus();
  });

  workerEnrollmentForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const target = event.currentTarget;
    const form = new FormData(target);
    const displayName = String(form.get("display-name") ?? "").trim();
    const pool = String(form.get("pool") ?? "").trim();
    const requestedTtl = Number(form.get("ttl-seconds"));
    const ttlSeconds = Number.isInteger(requestedTtl) && requestedTtl > 0
      ? requestedTtl
      : undefined;
    const nameField = one("#worker-display-name");
    const poolField = one("#worker-pool");
    nameField?.removeAttribute("aria-invalid");
    poolField?.removeAttribute("aria-invalid");
    setWorkerEnrollmentFeedback("");
    clearWorkerEnrollmentResult();
    if (!displayName) {
      nameField?.setAttribute("aria-invalid", "true");
      setWorkerEnrollmentFeedback("팀에서 구분할 Mac 이름을 입력해 주세요.");
      nameField?.focus();
      return;
    }
    if (!pool) {
      poolField?.setAttribute("aria-invalid", "true");
      setWorkerEnrollmentFeedback("작업 풀 이름을 입력해 주세요. 기본값은 appium입니다.");
      poolField?.focus();
      return;
    }
    setWorkerActionBusy(workerEnrollmentSubmit, true, "코드 만드는 중…");
    try {
      const enrollment = await workerAdminRequest("/v1/worker-enrollments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          display_name: displayName,
          pool,
          ...(ttlSeconds === undefined ? {} : { ttl_seconds: ttlSeconds }),
        }),
      });
      const origin = window.location.origin.replace(/\/$/, "");
      const commands = [
        "bash -euo pipefail <<'TRACE_MAC_BOOTSTRAP'",
        "repository=\"corca-ai/ads-booster\"",
        "gh auth status >/dev/null",
        "release=\"$(gh release view --repo \"$repository\" --json tagName,isDraft,isPrerelease --jq 'select(.isDraft == false and .isPrerelease == false) | .tagName')\"",
        "release_dir=\"$(mktemp -d \"${TMPDIR:-/tmp}/trace-marketing-bootstrap.XXXXXX\")\"",
        "trap 'rm -rf -- \"$release_dir\"' EXIT",
        "gh release download \"$release\" --repo \"$repository\" --dir \"$release_dir\" --pattern trace-marketing-release.json --pattern trace-marketing-bootstrap.py",
        "manifest=\"$release_dir/trace-marketing-release.json\"",
        "bootstrap=\"$release_dir/trace-marketing-bootstrap.py\"",
        "bundle_name=\"$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[\"bundle\"][\"name\"])' \"$manifest\")\"",
        "commit_sha=\"$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[\"commit_sha\"])' \"$manifest\")\"",
        "[[ \"$bundle_name\" =~ ^trace-marketing-macos-arm64-v[0-9]+\\.[0-9]+\\.[0-9]+\\.tar\\.gz$ ]]",
        "[[ \"$commit_sha\" =~ ^[0-9a-f]{40}$ ]]",
        "gh release download \"$release\" --repo \"$repository\" --dir \"$release_dir\" --pattern \"$bundle_name\"",
        "for asset in \"$manifest\" \"$bootstrap\" \"$release_dir/$bundle_name\"; do gh attestation verify \"$asset\" --repo \"$repository\" --signer-workflow \"$repository/.github/workflows/release-mac-worker.yml\" --source-ref refs/heads/main --source-digest \"$commit_sha\" --deny-self-hosted-runners; done",
        "python3 \"$bootstrap\" --manifest \"$manifest\" --bundle \"$release_dir/$bundle_name\" --uv \"$(command -v uv)\" --gh \"$(command -v gh)\"",
        "export PATH=\"$HOME/.local/share/trace-marketing/current/bin:$PATH\"",
        "trace-marketing worker doctor",
        `trace-marketing worker enroll --url ${origin} --code '${enrollment.enrollment_code}'`,
        "trace-marketing worker finish-bootstrap --home \"$HOME/.trace-agent\" --install-root \"$HOME/.local/share/trace-marketing\" --uv \"$(command -v uv)\" --gh \"$(command -v gh)\"",
        "trace-marketing worker status",
        "trace-marketing worker updater-status",
        "TRACE_MAC_BOOTSTRAP",
      ].join("\n");
      if (workerEnrollmentCode) workerEnrollmentCode.textContent = enrollment.enrollment_code;
      if (workerEnrollmentCommand) workerEnrollmentCommand.textContent = commands;
      if (workerEnrollmentExpiry) {
        workerEnrollmentExpiry.textContent = `${new Date(enrollment.expires_at).toLocaleString("ko-KR")}까지 한 번만 사용할 수 있습니다.`;
      }
      if (workerEnrollmentResult) workerEnrollmentResult.hidden = false;
      target.reset();
      const poolReset = one("#worker-pool");
      if (poolReset) poolReset.value = "appium";
    } catch (error) {
      if (error.status === 401) lockWorkerManager(error.message);
      else setWorkerEnrollmentFeedback(error.message);
    } finally {
      setWorkerActionBusy(workerEnrollmentSubmit, false);
    }
  });

  workerEnrollmentCodeCopy?.addEventListener("click", () =>
    copyWorkerSetupText(workerEnrollmentCode?.textContent?.trim(), workerEnrollmentCodeCopy, "코드 복사됨"));

  workerEnrollmentCommandCopy?.addEventListener("click", () =>
    copyWorkerSetupText(workerEnrollmentCommand?.textContent?.trim(), workerEnrollmentCommandCopy, "명령 복사됨"));

  workerAgentPromptCopy?.addEventListener("click", () =>
    copyWorkerSetupText(
      workerAgentPrompt?.textContent?.trim(),
      workerAgentPromptCopy,
      "프롬프트 복사됨",
      setWorkerAgentPromptFeedback,
    ));

  const autogenNotice = (created) => {
    const registered = `후보 ${created.length}개가 등록되었습니다`;
    const provenance = created[0]?.generation_provenance;
    if (!provenance) return `${registered}.`;
    const documents = provenance.documents.length;
    return `${registered} — 문서 ${documents}개(${kilobytes(provenanceBytes(provenance))})를 읽고 생성`;
  };

  // A batch belongs to the account that asked for it. The server takes concurrent requests
  // and the accounts are already isolated, so the only thing that has to be exclusive is one
  // account generating twice — locking the whole screen meant 이서진's two-minute batch also
  // froze 김도현's, and froze review work that has nothing to do with generation.
  const WORKSPACE_BATCH = "workspace";
  const generationRuns = new Map();
  const autogenLabels = new Map();

  const generationKey = () => {
    // The opened account on the local shell, the selected one on the hosted shell, and one
    // shared key when neither names an account.
    const account = currentAccount?.account_id
      || (hostedCandidateControls ? selectedAccountId : "");
    return account || WORKSPACE_BATCH;
  };

  const autogenLabel = (button) => {
    // Captured the first time the button is painted, which is always while it is idle.
    if (!autogenLabels.has(button)) autogenLabels.set(button, button.textContent);
    return autogenLabels.get(button);
  };

  const renderAutogenButtons = () => {
    const run = generationRuns.get(generationKey());
    all("[data-autogen]").forEach((button) => {
      const label = autogenLabel(button);
      button.disabled = Boolean(run);
      button.textContent = run ? run.label : label;
    });
  };

  // Generation takes minutes, and a button that only says "생성 중…" looks the same at ten
  // seconds and at two minutes. Without a moving number people read the wait as a hang and
  // press again, and the second press wrote a second batch. The count is ticks rather than
  // wall-clock so the label never stalls behind a busy main thread.
  const countUpFor = (run, key) => {
    const paint = () => {
      run.label = `생성 중… ${run.seconds}초 (보통 1~3분)`;
      // A run for an account nobody is looking at keeps counting but paints nothing.
      if (generationKey() === key) renderAutogenButtons();
    };
    const tick = () => {
      run.seconds += 1;
      paint();
      run.timer = window.setTimeout(tick, AUTOGEN_TICK_MS);
    };
    paint();
    run.timer = window.setTimeout(tick, AUTOGEN_TICK_MS);
  };

  const generateCandidates = async () => {
    const key = generationKey();
    if (generationRuns.has(key)) return;
    const run = { seconds: 0, timer: null, label: "" };
    generationRuns.set(key, run);
    countUpFor(run, key);
    setAutogenFeedback("");
    setNotice("AI가 후보를 만드는 중… (1~3분 소요)");
    try {
      const profile = selectedContextProfile();
      const options = hostedCandidateControls
        ? {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ context_profile_id: profile?.profile_id ?? null }),
          }
        : { method: "POST" };
      // The account is the concept the batch is written as, so it travels with the request.
      const generatePath = currentAccount
        ? `/api/candidates/generate?account_id=${encodeURIComponent(currentAccount.account_id)}`
        : "/api/candidates/generate";
      const created = await request(generatePath, options);
      // Only the screen that asked for the batch is repainted. Somebody who moved on to
      // another account must not have its list replaced by candidates it never requested;
      // opening an account reloads its own list anyway.
      if (generationKey() === key) {
        await loadCandidates();
        setNotice(autogenNotice(created));
      }
    } catch (error) {
      if (generationKey() === key) {
        setAutogenFeedback(error.message);
        setNotice(error.message);
      }
    } finally {
      if (run.timer) window.clearTimeout(run.timer);
      generationRuns.delete(key);
      renderAutogenButtons();
    }
  };

  const showStage = (stage) => {
    // The two approval stages are separate jobs: reading captions and judging images ask
    // for different attention, so only one is on screen at a time.
    all("[data-stage-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.stagePanel !== stage;
    });
    all("[data-stage-tab]").forEach((tab) => {
      const selected = tab.dataset.stageTab === stage;
      tab.setAttribute("aria-selected", String(selected));
      if (selected) tab.removeAttribute("tabindex");
      else tab.setAttribute("tabindex", "-1");
    });
  };

  all("[data-stage-tab]").forEach((tab) => {
    tab.addEventListener("click", () => showStage(tab.dataset.stageTab));
  });

  all("[data-autogen]").forEach((button) => {
    autogenLabel(button);
    button.addEventListener("click", () => generateCandidates());
  });



  accountSelect?.addEventListener("change", async () => {
    const next = accountSelect.value;
    if (!hostedAccounts.some((account) => account.account_id === next)) return;
    selectedAccountId = next;
    try { window.localStorage.setItem("trace:hosted-account", selectedAccountId); } catch (error) {
      if (!(error instanceof DOMException)) throw error;
    }
    cancelCandidateEdit();
    cancelContextEdit();
    renderHostedAccounts();
    renderAutogenButtons();
    setBusy(workspaceLive, true, "운영 계정을 바꾸는 중…");
    try {
      await loadContextProfiles();
      await Promise.all([loadCandidates(), loadFeedbackSummary()]);
      setNotice(`${selectedHostedAccount()?.display_name ?? selectedAccountId} 계정으로 전환했습니다.`);
    } catch (error) {
      setNotice(error.message);
    } finally {
      setBusy(workspaceLive, false);
    }
  });

  accountEditForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const target = event.currentTarget;
    if (!target.checkValidity()) { target.reportValidity(); return; }
    const account = selectedHostedAccount();
    if (!account) return;
    const form = new FormData(target);
    setAccountFeedback(accountEditFeedback, "");
    setBusy(target, true, "계정 설정을 저장하는 중…");
    try {
      await request(`/api/accounts/${encodeURIComponent(account.account_id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_revision: account.revision,
          display_name: String(form.get("display-name") ?? "").trim(),
          timezone: String(form.get("timezone") ?? "").trim(),
          morning_time: String(form.get("morning-time") ?? "").trim(),
          evening_time: String(form.get("evening-time") ?? "").trim(),
          generation_enabled: form.get("generation-enabled") === "on",
        }),
      });
      await loadHostedAccounts();
      setNotice("계정 운영 설정을 저장했습니다.");
    } catch (error) {
      setAccountFeedback(accountEditFeedback, error.message);
      setNotice(error.message);
    } finally {
      setBusy(target, false);
    }
  });

  document.getElementById("new-account-country")?.addEventListener("change", (event) => {
    const timezone = document.getElementById("new-account-timezone");
    if (timezone) timezone.value = COUNTRY_TIMEZONES[event.currentTarget.value] ?? "UTC";
  });

  accountCreateForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const target = event.currentTarget;
    if (!target.checkValidity()) { target.reportValidity(); return; }
    const form = new FormData(target);
    setAccountFeedback(accountCreateFeedback, "");
    setBusy(target, true, "격리 계정을 추가하는 중…");
    try {
      const created = await request("/api/accounts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          account_id: String(form.get("account-id") ?? "").trim(),
          display_name: String(form.get("display-name") ?? "").trim(),
          country: String(form.get("country") ?? "").trim(),
          timezone: String(form.get("timezone") ?? "").trim(),
          morning_time: String(form.get("morning-time") ?? "").trim(),
          evening_time: String(form.get("evening-time") ?? "").trim(),
          generation_enabled: form.get("generation-enabled") === "on",
        }),
      });
      selectedAccountId = created.account_id;
      try { window.localStorage.setItem("trace:hosted-account", selectedAccountId); } catch (error) {
        if (!(error instanceof DOMException)) throw error;
      }
      target.reset();
      document.getElementById("new-account-morning-time").value = "07:30";
      document.getElementById("new-account-evening-time").value = "19:30";
      await loadHostedAccounts();
      await loadContextProfiles();
      await Promise.all([loadCandidates(), loadFeedbackSummary()]);
      setNotice("새 격리 계정을 추가하고 전환했습니다.");
    } catch (error) {
      setAccountFeedback(accountCreateFeedback, error.message);
      setNotice(error.message);
    } finally {
      setBusy(target, false);
    }
  });

  contextSelect?.addEventListener("change", () => {
    selectedContextProfileId = contextSelect.value;
    if (editingCandidate) editingCandidateContextChanged = true;
    renderSelectedContext();
    const profile = selectedContextProfile();
    if (profile) candidateField("candidate-country").value = profile.country;
    void loadFeedbackSummary().catch((error) => setNotice(error.message));
  });

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

  const contextProfileDraft = (form) => ({
    name: String(form.get("name") ?? "").trim(),
    persona_id: String(form.get("persona-id") ?? "").trim(),
    country: String(form.get("country") ?? "").trim().toUpperCase(),
    tone: String(form.get("tone") ?? "").trim(),
    audience: String(form.get("audience") ?? "").trim(),
    situation: String(form.get("situation") ?? "").trim(),
    guidance: String(form.get("guidance") ?? "").trim(),
    reference_ids: commaList(form.get("reference-ids")),
  });

  const contextProblem = (draft) => {
    if (!draft.name) return ["context-name", "표시 이름을 입력해 주세요."];
    if (!/^[a-z0-9][a-z0-9_-]{1,79}$/.test(draft.persona_id)) {
      return ["context-persona-id", "페르소나 ID는 영문 소문자·숫자로 시작하고 -, _만 사용할 수 있습니다."];
    }
    if (!/^[A-Z]{2}$/.test(draft.country)) return ["context-country", "두 자리 국가 코드를 선택해 주세요."];
    if (!draft.tone) return ["context-tone", "문체를 입력해 주세요."];
    if (!draft.audience) return ["context-audience", "대상을 입력해 주세요."];
    if (!draft.situation) return ["context-situation", "사용 상황을 입력해 주세요."];
    if (!draft.guidance) return ["context-guidance", "추가 지침을 입력해 주세요."];
    return null;
  };

  contextCancel?.addEventListener("click", cancelContextEdit);

  contextForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const target = event.currentTarget;
    const draft = contextProfileDraft(new FormData(target));
    ["context-name", "context-persona-id", "context-country", "context-tone", "context-audience",
      "context-situation", "context-guidance"]
      .forEach((id) => document.getElementById(id)?.removeAttribute("aria-invalid"));
    const problem = contextProblem(draft);
    if (problem) {
      const [id, message] = problem;
      const field = document.getElementById(id);
      field?.setAttribute("aria-invalid", "true");
      field?.focus();
      setContextFeedback(message);
      return;
    }
    const editing = editingContextProfile;
    setBusy(target, true, editing ? "컨텍스트 수정 내용을 저장하는 중…" : "컨텍스트를 추가하는 중…");
    setContextFeedback("");
    try {
      const saved = await request(
        editing ? `/api/context-profiles/${encodeURIComponent(editing.profile_id)}` : "/api/context-profiles",
        {
          method: editing ? "PATCH" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(editing ? { ...draft, expected_revision: editing.revision } : draft),
        },
      );
      selectedContextProfileId = saved.profile_id;
      cancelContextEdit();
      await loadContextProfiles();
      setNotice(editing ? "컨텍스트를 수정했습니다." : "컨텍스트를 추가하고 생성 기준으로 선택했습니다.");
    } catch (error) {
      setContextFeedback(error.message);
      setNotice(error.message);
    } finally {
      setBusy(target, false);
    }
  });

  candidateCancel?.addEventListener("click", cancelCandidateEdit);

  candidateForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const target = event.currentTarget;
    const form = new FormData(target);
    const draft = {
      topic: String(form.get("topic") ?? "").trim(),
      country: String(form.get("country") ?? "").trim().toUpperCase(),
      posting_slot: String(form.get("posting-slot") ?? "manual").trim(),
      persona_domain: String(form.get("persona-domain") ?? "").trim() || null,
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
        background_search_query: String(form.get("background-search-query") ?? "").trim() || null,
        language: countryLanguage(String(form.get("country") ?? "").trim().toUpperCase()),
      },
    };
    const profile = selectedContextProfile();
    const contextProfileId = hostedCandidateControls && profile?.country === draft.country
      ? profile.profile_id
      : null;
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
      const editing = editingCandidate;
      const requestBody = editing ? { ...draft, expected_revision: editing.revision } : { ...draft };
      // Omitting the field on ordinary edits keeps the original generation snapshot immutable.
      if (hostedCandidateControls && (!editing || editingCandidateContextChanged)) {
        requestBody.context_profile_id = contextProfileId;
      }
      await request(
        editing ? `/api/candidates/${encodeURIComponent(editing.candidate_id)}` : "/api/candidates",
        {
        method: editing ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
      });
      cancelCandidateEdit();
      await loadCandidates();
      setNotice(editing ? "후보를 수정했습니다. 캡션·주제 검수부터 다시 진행해 주세요." : "후보를 등록했습니다.");
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
