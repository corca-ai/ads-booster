from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol

from trace_capture.agent.control import AgentControlError
from trace_capture.agent.tui_approval import PermissionMode
from trace_capture.agent.tui_styles import TUI_COLORS
from trace_capture.auth.store import AuthStoreError

if TYPE_CHECKING:
    from trace_capture.agent.control import AgentControlPort
    from trace_capture.agent.session import AgentSession

ROOT_COMMANDS: Final[tuple[str, ...]] = (
    "/auth",
    "/model",
    "/permission",
    "/new",
    "/clear",
    "/session",
    "/help",
)
AUTH_COMMANDS: Final[tuple[str, ...]] = (
    "/auth login",
    "/auth status",
    "/auth logout",
)
PERMISSION_COMMANDS: Final[tuple[str, ...]] = ("/permission ask", "/permission yolo")
ALL_COMMANDS: Final[tuple[str, ...]] = (
    *ROOT_COMMANDS,
    *AUTH_COMMANDS,
    *PERMISSION_COMMANDS,
)

COMMAND_DESCRIPTIONS: Final[dict[str, str]] = {
    "/auth": "로그인, 상태 확인, 로그아웃",
    "/auth login": "OpenAI ChatGPT / Codex 로그인",
    "/auth status": "로그인 상태 확인",
    "/auth logout": "저장된 로그인 정보 삭제",
    "/model": "사용할 모델 선택",
    "/permission": "승인 방식 확인 또는 변경",
    "/permission ask": "변경 작업마다 확인",
    "/permission yolo": "변경 작업 자동 허용",
    "/new": "현재 세션을 보존하고 새 세션 시작",
    "/clear": "현재 세션을 삭제하고 새 세션 시작",
    "/session": "이전 세션 목록과 복귀",
    "/help": "사용 가능한 명령어 보기",
}


PREFIX_COMMANDS: Final[frozenset[str]] = frozenset({"/auth", "/permission"})
PREVIEW_COMMAND_LIMIT: Final[int] = 2


def command_suggestions(value: str) -> tuple[str, ...]:
    query = value.casefold()
    if not query.startswith("/"):
        return ()
    if query.startswith("/auth"):
        candidates = AUTH_COMMANDS
    elif query.startswith("/permission"):
        candidates = PERMISSION_COMMANDS
    else:
        candidates = ROOT_COMMANDS
    return tuple(command for command in candidates if command.startswith(query))


def command_preview(value: str, selected_index: int = 0) -> str | None:
    suggestions = command_suggestions(value)
    if not suggestions:
        return None
    total = len(suggestions)
    selected = selected_index % total
    lines = ["명령어 · ↑↓ 이동 · Tab/Enter 입력"]
    visible_start = min(
        max(0, selected - PREVIEW_COMMAND_LIMIT + 1),
        max(0, total - PREVIEW_COMMAND_LIMIT),
    )
    visible_end = min(total, visible_start + PREVIEW_COMMAND_LIMIT)
    if visible_start > 0:
        lines.append("  ↑ 더 보기")
    for index in range(visible_start, visible_end):
        cmd = suggestions[index]
        marker = ">" if index == selected else " "
        desc = COMMAND_DESCRIPTIONS.get(cmd, "")
        desc_str = f"  {desc}" if desc else ""
        lines.append(f"{marker} {cmd:<20}{desc_str}")
    if visible_end < total:
        lines.append("  ↓ 더 보기")
    return "\n".join(lines)


def command_completion(value: str, selected_index: int = 0) -> str | None:
    suggestions = command_suggestions(value)
    if not suggestions:
        return None
    normalized = value.casefold()
    if normalized == "/permission":
        return None
    # Prefix commands (e.g. /auth) always complete to selected sub-command
    if normalized in PREFIX_COMMANDS:
        completed = suggestions[selected_index % len(suggestions)]
        return f"{completed} " if completed == "/model" else completed
    # Leaf commands that are already fully typed don't need completion
    if normalized in ALL_COMMANDS:
        return None
    completed = suggestions[selected_index % len(suggestions)]
    return f"{completed} " if completed == "/model" else completed


def is_known_command(value: str) -> bool:
    prompt = value.strip()
    if prompt in ALL_COMMANDS or prompt == "/logout":
        return True
    if prompt.startswith("/model "):
        return bool(prompt.removeprefix("/model ").strip())
    return prompt.startswith("/session ") and bool(prompt.removeprefix("/session ").strip())


class TuiCommandHost(Protocol):
    session: AgentSession
    runtime: AgentControlPort | None
    oauth_account_id: str | None

    @property
    def busy(self) -> bool: ...

    def new_session(self) -> None: ...

    def clear_session(self) -> None: ...

    def show_session_picker(self, session_id: str | None = None) -> None: ...

    def start_oauth(self) -> None: ...

    def show_model_picker(self) -> None: ...

    def show_permission_mode(self) -> None: ...

    def set_permission_mode(self, mode: PermissionMode) -> None: ...

    def write_system(self, message: str) -> None: ...

    def write_error(self, message: str) -> None: ...

    def set_status(self, value: str, color: str) -> None: ...

    def refresh_settings(self) -> None: ...


def handle_tui_command(host: TuiCommandHost, prompt: str) -> bool:
    if _handle_session_command(host, prompt):
        return True
    if _handle_auth_command(host, prompt):
        return True
    return _handle_config_command(host, prompt)


def _handle_session_command(host: TuiCommandHost, prompt: str) -> bool:
    match prompt:  # noqa: MATCH_OK
        case "/auth":
            host.write_system("로그인: /auth login · /auth status · /auth logout")
        case "/new":
            host.new_session()
        case "/clear":
            host.clear_session()
        case "/session":
            host.show_session_picker()
        case value if value.startswith("/session "):
            host.show_session_picker(value.removeprefix("/session ").strip())
        case "/help":
            host.write_system("로그인: /auth login · /auth status · /auth logout")
            host.write_system("모델: /model [name]")
            host.write_system("승인 방식: /permission [ask|yolo]")
            host.write_system("세션: /new · /clear · /session [id] · /help")
        case _:
            return False
    return True


def _handle_auth_command(host: TuiCommandHost, prompt: str) -> bool:
    clean = prompt.strip()
    if clean == "/auth status":
        _show_auth_status(host)
        return True
    if clean in {"/auth logout", "/logout"}:
        _logout(host)
        return True
    if clean == "/auth login":
        host.start_oauth()
        return True
    return False


def _handle_config_command(host: TuiCommandHost, prompt: str) -> bool:
    match prompt:  # noqa: MATCH_OK
        case "/model":
            host.show_model_picker()
        case value if value.startswith("/model "):
            _set_model(host, value.removeprefix("/model "))
        case "/permission":
            host.show_permission_mode()
        case "/permission ask":
            host.set_permission_mode(PermissionMode.ASK)
        case "/permission yolo":
            host.set_permission_mode(PermissionMode.YOLO)
        case _:
            return False
    return True


def _show_auth_status(host: TuiCommandHost) -> None:
    runtime = host.runtime
    if runtime is None:
        host.write_error("로그인 기능을 사용할 수 없습니다")
        return
    try:
        status = runtime.auth_status()
    except (AgentControlError, AuthStoreError) as error:
        host.write_error(str(error))
        return
    host.write_system(f"로그인 상태: {status}")
    host.refresh_settings()


def _logout(host: TuiCommandHost) -> None:
    runtime = host.runtime
    if runtime is None:
        host.write_error("로그인 기능을 사용할 수 없습니다")
        return
    try:
        runtime.auth_logout()
    except (AgentControlError, AuthStoreError) as error:
        host.write_error(str(error))
        host.set_status("ERROR", TUI_COLORS["danger"])
        return
    host.oauth_account_id = None
    host.write_system("저장된 로그인 정보를 삭제했습니다")
    host.set_status("READY", TUI_COLORS["success"])
    host.refresh_settings()


def _set_model(host: TuiCommandHost, value: str) -> None:
    runtime = host.runtime
    if runtime is None:
        host.write_error("런타임 설정을 사용할 수 없습니다")
        return
    try:
        model = runtime.set_model(value)
    except AgentControlError as error:
        host.write_error(str(error))
        return
    host.write_system(f"모델을 {model}(으)로 설정했습니다")
    host.refresh_settings()
