from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from trace_capture.agent.session import AgentError, AgentSession
from trace_capture.agent.session_store import (
    NullSessionStore,
    SessionManager,
    SessionNotFoundError,
    SessionStore,
    SessionStoreError,
)
from trace_capture.auth.codex import OAuthError
from trace_capture.providers.errors import ProviderError

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, slots=True)
class Repl:
    session: AgentSession
    input_fn: Callable[[str], str] = input
    output_fn: Callable[[str], None] = print
    session_store: SessionStore = field(default_factory=NullSessionStore)

    def run(self) -> None:
        self.output_fn("trace-agent standalone REPL. /help for commands. EOF or Ctrl-C to quit.")
        manager = SessionManager(self.session, self.session_store)
        while True:
            try:
                line = self.input_fn("trace-agent> ")
            except EOFError, KeyboardInterrupt:
                self.output_fn("")
                return
            command = line.strip()
            if not command:
                continue
            if self._handle_command(manager, command):
                continue
            try:
                response = manager.session.ask(command)
            except (AgentError, OAuthError, ProviderError) as error:
                self.output_fn(f"error: {error}")
                continue
            self.output_fn(response)
            try:
                manager.save()
            except SessionStoreError as error:
                self.output_fn(f"error: {error}")

    def _handle_command(self, manager: SessionManager, command: str) -> bool:
        if command == "/help":
            help_prefix = "Enter a request. /new keeps this session, /clear deletes it,"
            session_help = "/session lists resumable sessions."
            help_text = f"{help_prefix} {session_help}"
            self.output_fn(help_text)
            return True
        if command == "/new":
            self._new_session(manager)
            return True
        if command == "/clear":
            self._clear_session(manager)
            return True
        if command == "/session":
            self._list_sessions(manager)
            return True
        if command.startswith("/session "):
            self._resume_session(manager, command.removeprefix("/session ").strip())
            return True
        return False

    def _new_session(self, manager: SessionManager) -> None:
        try:
            info = manager.new()
        except SessionStoreError as error:
            self.output_fn(f"error: {error}")
            return
        self.output_fn(f"New session started; previous session saved: {info.session_id}")

    def _clear_session(self, manager: SessionManager) -> None:
        try:
            info = manager.clear()
        except SessionStoreError as error:
            self.output_fn(f"error: {error}")
            return
        self.output_fn(f"Session cleared; new session started: {info.session_id}")

    def _list_sessions(self, manager: SessionManager) -> None:
        try:
            sessions = manager.available()
        except SessionStoreError as error:
            self.output_fn(f"error: {error}")
            return
        if not sessions:
            self.output_fn("No previous sessions saved yet.")
            return
        for info in sessions:
            self.output_fn(f"{info.session_id} · {info.title} · {info.message_count} msgs")

    def _resume_session(self, manager: SessionManager, session_id: str) -> None:
        try:
            info = manager.resume(session_id)
        except (SessionNotFoundError, SessionStoreError) as error:
            self.output_fn(f"error: {error}")
            return
        self.output_fn(f"Resumed session {info.session_id} · {info.title}")
