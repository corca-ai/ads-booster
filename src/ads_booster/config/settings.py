from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AgentSettings:
    workspace: Path
    model: str
    browser_command: tuple[str, ...]
    reasoning_effort: str | None = "medium"
    web_search_provider: str = "auto"
    web_search_timeout_seconds: float = 30.0
    context_window_tokens: int = 128_000
    context_soft_ratio: float = 0.70
    context_hard_ratio: float = 0.85
    context_recent_tail_tokens: int = 16_000
    context_max_tool_output_chars: int = 6_000
    candidate_timeout_seconds: float = 240.0
    memory_file: Path | None = None
    sessions_dir: Path = field(default_factory=lambda: Path.home() / ".trace-agent" / "sessions")

    @classmethod
    def from_environment(cls, workspace: Path | None = None) -> AgentSettings:
        command = os.environ.get("TRACE_AGENT_BROWSER_COMMAND", "agent-browser")
        configured_home = os.environ.get("TRACE_AGENT_HOME")
        agent_home = (
            Path(configured_home).expanduser()
            if configured_home is not None
            else Path.home() / ".trace-agent"
        )
        memory_file = os.environ.get("TRACE_AGENT_MEMORY_FILE")
        sessions_dir = os.environ.get("TRACE_AGENT_SESSIONS_DIR")
        return cls(
            workspace=(workspace or Path.cwd()).resolve(),
            model=os.environ.get("TRACE_AGENT_MODEL", "gpt-5.6-luna"),
            browser_command=tuple(shlex.split(command)),
            reasoning_effort=os.environ.get("TRACE_AGENT_REASONING_EFFORT", "medium") or None,
            web_search_provider=os.environ.get("TRACE_AGENT_WEB_SEARCH_PROVIDER", "auto"),
            web_search_timeout_seconds=float(
                os.environ.get("TRACE_AGENT_WEB_SEARCH_TIMEOUT_SECONDS", "30")
            ),
            context_window_tokens=int(
                os.environ.get("TRACE_AGENT_CONTEXT_WINDOW_TOKENS", "128000")
            ),
            context_soft_ratio=float(os.environ.get("TRACE_AGENT_CONTEXT_SOFT_RATIO", "0.70")),
            context_hard_ratio=float(os.environ.get("TRACE_AGENT_CONTEXT_HARD_RATIO", "0.85")),
            context_recent_tail_tokens=int(
                os.environ.get("TRACE_AGENT_CONTEXT_RECENT_TAIL_TOKENS", "16000")
            ),
            context_max_tool_output_chars=int(
                os.environ.get("TRACE_AGENT_CONTEXT_MAX_TOOL_OUTPUT_CHARS", "6000")
            ),
            candidate_timeout_seconds=float(
                os.environ.get("TRACE_AGENT_CANDIDATE_TIMEOUT_SECONDS", "240")
            ),
            memory_file=(
                Path(memory_file).expanduser()
                if memory_file is not None
                else agent_home / "memory.jsonl"
            ),
            sessions_dir=(
                Path(sessions_dir).expanduser()
                if sessions_dir is not None
                else agent_home / "sessions"
            ),
        )
