from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter

from ads_booster.agent.factory import AgentSessionConfig, build_agent_session
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from ads_booster.agent.memory import MemoryStore
    from ads_booster.agent.run_runtime import SessionBuildRequest
    from ads_booster.agent.session import AgentSession, ModelClient
    from ads_booster.config.settings import AgentSettings
    from ads_booster.tools.models import ToolContext

AGENT_RUN_CONTEXT_OPEN: Final = "<agent-run-context>"
AGENT_RUN_CONTEXT_CLOSE: Final = "</agent-run-context>"
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


@dataclass(frozen=True, slots=True)
class AgentRunSessionBuilder:
    settings: AgentSettings
    client: ModelClient
    context: ToolContext
    memory_store: MemoryStore

    def build(self, request: SessionBuildRequest) -> AgentSession:
        """Build one AgentSession from a structured durable run snapshot."""
        memory = self.memory_store.latest()
        payload = _JSON_OBJECT.validate_python(
            {
                "objective": request.goal.objective,
                "success_criteria": list(request.goal.success_criteria),
                "context": request.goal.context,
                "connector_instructions": request.connector_instructions,
                "observations": [
                    observation.model_dump(mode="json") for observation in request.observations
                ],
                "memory": None if memory is None else memory.text,
            }
        )
        bootstrap = _JSON_OBJECT.validate_python(
            {
                "role": "developer",
                "content": (
                    f"{AGENT_RUN_CONTEXT_OPEN}"
                    f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
                    f"{AGENT_RUN_CONTEXT_CLOSE}"
                ),
            }
        )
        return build_agent_session(
            AgentSessionConfig(
                settings=self.settings,
                client=self.client,
                registry=request.registry,
                context=self.context,
                history=request.history,
                context_prefix=(bootstrap, *request.connector_context),
                memory_store=self.memory_store,
            )
        )
