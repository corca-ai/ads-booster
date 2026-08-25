from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from trace_capture.agent.context import ContextPolicy, ContextRuntime
from trace_capture.agent.memory import NullMemoryStore
from trace_capture.agent.session import AgentSession
from trace_capture.search.image.providers import create_image_search_provider
from trace_capture.search.text.providers import create_web_search_provider
from trace_capture.tools.models import ToolContext
from trace_capture.tools.registry import default_registry

if TYPE_CHECKING:
    from trace_capture.agent.memory import MemoryStore
    from trace_capture.agent.session import ModelClient
    from trace_capture.config.settings import AgentSettings
    from trace_capture.tools.models import ApprovalPort
    from trace_capture.tools.registry import ToolRegistry
    from trace_capture.transport.http import HttpClient
    from trace_capture.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class AgentSessionConfig:
    """Typed inputs required to construct one agent session."""

    settings: AgentSettings
    client: ModelClient
    context: ToolContext
    history: tuple[JsonObject, ...] = ()
    registry: ToolRegistry = field(default_factory=default_registry)
    memory_store: MemoryStore = field(default_factory=NullMemoryStore)


def build_tool_context(
    settings: AgentSettings,
    approval: ApprovalPort,
    http: HttpClient,
) -> ToolContext:
    """Build the shared tool boundary for CLI, TUI, REPL, and Web callers."""
    return ToolContext(
        workspace=settings.workspace,
        approval=approval,
        browser_command=settings.browser_command,
        web_search=create_web_search_provider(
            http,
            settings.web_search_provider,
            settings.web_search_timeout_seconds,
        ),
        image_search=create_image_search_provider(
            http,
            settings.web_search_provider,
            settings.web_search_timeout_seconds,
        ),
    )


def build_agent_session(config: AgentSessionConfig) -> AgentSession:
    """Construct an agent session with the shared context and compaction policy."""
    settings = config.settings
    return AgentSession(
        client=config.client,
        registry=config.registry,
        context=config.context,
        history=list(config.history),
        context_runtime=ContextRuntime(
            ContextPolicy(
                context_window_tokens=settings.context_window_tokens,
                soft_ratio=settings.context_soft_ratio,
                hard_ratio=settings.context_hard_ratio,
                recent_tail_tokens=settings.context_recent_tail_tokens,
                max_tool_output_chars=settings.context_max_tool_output_chars,
            )
        ),
        memory_store=config.memory_store,
    )
