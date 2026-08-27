from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.agent.memory import NullMemoryStore
from ads_booster.agent.runs import AgentGoal, AgentRunSessionBuilder, SessionBuildRequest
from ads_booster.config.settings import AgentSettings
from ads_booster.tools.models import ToolContext
from ads_booster.tools.registry import ToolRegistry
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.tools import ToolDescriptor
from ads_booster.providers.codex import ModelTurn

_OPEN = "<agent-run-context>"
_CLOSE = "</agent-run-context>"
_MODEL_NOT_EXPECTED = "builder test does not call the model"
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class ImmediateModel:
    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn:
        del history, tools
        raise AssertionError(_MODEL_NOT_EXPECTED)


class RecordingModel:
    def __init__(self) -> None:
        self.history: tuple[JsonObject, ...] = ()

    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn:
        del tools
        self.history = history
        return ModelTurn(text="done", calls=())


class AllowApproval:
    def request(self, action: str, detail: str) -> bool:
        del action, detail
        return True


def test_builder_injects_structured_goal_context_without_changing_canonical_history(
    tmp_path: Path,
) -> None:
    # Given a structured run request and pre-existing canonical history
    settings = AgentSettings.from_environment(tmp_path)
    model = RecordingModel()
    builder = AgentRunSessionBuilder(
        settings=settings,
        client=model,
        context=ToolContext(tmp_path, AllowApproval(), ()),
        memory_store=NullMemoryStore(),
    )
    request = SessionBuildRequest(
        goal=AgentGoal(
            objective="Create one image",
            success_criteria=("awaits review",),
            context={"persona_id": "student"},
        ),
        connector_instructions="Use the connector tools.",
        connector_context=({"role": "developer", "content": {"reference": "ref-a"}},),
        observations=(),
        history=({"role": "assistant", "content": "previous observation"},),
        registry=ToolRegistry(()),
    )

    # When the Core session is built and executes one turn
    session = builder.build(request)
    _ = session.ask("continue")

    # Then bootstrap reaches the provider but never enters canonical run history
    assert session.history == [
        {"role": "assistant", "content": "previous observation"},
        {"role": "user", "content": "continue"},
        {"role": "assistant", "content": "done"},
    ]
    developer = model.history[0]
    assert developer["role"] == "developer"
    content = developer["content"]
    assert isinstance(content, str)
    assert content.startswith(_OPEN)
    assert content.endswith(_CLOSE)
    payload = _JSON_OBJECT.validate_json(content.removeprefix(_OPEN).removesuffix(_CLOSE))
    assert payload == {
        "connector_instructions": "Use the connector tools.",
        "context": {"persona_id": "student"},
        "memory": None,
        "objective": "Create one image",
        "observations": [],
        "success_criteria": ["awaits review"],
    }
    assert model.history[1] == request.connector_context[0]
    assert model.history[2] == {"role": "assistant", "content": "previous observation"}
