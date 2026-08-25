from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from trace_capture.agent.context import CompactionSummary, ContextPolicy, ContextRuntime
from trace_capture.agent.memory import JsonlMemoryStore
from trace_capture.agent.session import AgentSession
from trace_capture.providers.codex import FunctionCall, ModelTurn
from trace_capture.providers.errors import ProviderError
from trace_capture.tools.approval import DenyApproval
from trace_capture.tools.models import ToolContext
from trace_capture.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from pathlib import Path

    from trace_capture.contracts.tools import ToolDescriptor
    from trace_capture.transport.json_types import JsonObject


@dataclass(slots=True)  # noqa: MUTABLE_OK
class RecordingModel:
    turns: list[ModelTurn]
    histories: list[tuple[JsonObject, ...]]
    overflow_once: bool = False

    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn:
        _ = tools
        self.histories.append(history)
        if self.overflow_once:
            self.overflow_once = False
            code = "provider_context_overflow"
            detail = "context length exceeded"
            raise ProviderError(
                code,
                detail,
                context_overflow=True,
            )
        return self.turns.pop(0)


@dataclass(slots=True)  # noqa: MUTABLE_OK
class RecordingMemory:
    summaries: list[str]

    def flush(self, summary: CompactionSummary) -> None:
        self.summaries.append(summary.text)

    def latest(self) -> CompactionSummary | None:
        return None


def small_policy() -> ContextPolicy:
    return ContextPolicy(
        context_window_tokens=120,
        reserved_output_tokens=20,
        soft_ratio=0.5,
        hard_ratio=0.75,
        recent_tail_tokens=20,
        max_tool_output_chars=20,
    )


def test_context_runtime_prunes_tool_output_without_mutating_history() -> None:
    history: tuple[JsonObject, ...] = (
        {"role": "user", "content": "brief"},
        {"type": "function_call_output", "call_id": "call-1", "output": "x" * 400},
    )
    runtime = ContextRuntime(small_policy())

    decision = runtime.prepare(history)

    assert "x" * 400 in str(history)
    assert "tool output pruned" in str(decision.projection)
    assert decision.pruned_tool_outputs == 1


def test_context_runtime_compacts_at_turn_boundary_and_keeps_recent_tail() -> None:
    history: tuple[JsonObject, ...] = (
        {"role": "user", "content": "marketing brief: JP calendar"},
        {"role": "assistant", "content": "old decision " + "x" * 160},
        {"role": "user", "content": "latest caption"},
        {"role": "assistant", "content": "latest answer"},
    )
    runtime = ContextRuntime(small_policy())

    decision = runtime.prepare(history)

    assert decision.compaction is not None
    assert "marketing brief" in decision.compaction.text
    assert "latest caption" in str(decision.projection)
    assert history[0]["content"] == "marketing brief: JP calendar"


def test_agent_session_flushes_compaction_before_model_request(tmp_path: Path) -> None:
    model = RecordingModel([ModelTurn("done", ())], [])
    memory = RecordingMemory([])
    session = AgentSession(
        model,
        ToolRegistry(()),
        ToolContext(tmp_path, DenyApproval(), ()),
        context_runtime=ContextRuntime(small_policy()),
        memory_store=memory,
    )
    session.history.extend(
        [
            {"role": "user", "content": "marketing brief " + "x" * 160},
            {"role": "assistant", "content": "old answer " + "y" * 160},
        ]
    )

    assert session.ask("latest request") == "done"
    assert memory.summaries
    assert "COMPACTED CONTEXT" in str(model.histories[0])


def test_agent_session_retries_once_after_provider_context_overflow(tmp_path: Path) -> None:
    model = RecordingModel([ModelTurn("recovered", ())], [], overflow_once=True)
    session = AgentSession(
        model,
        ToolRegistry(()),
        ToolContext(tmp_path, DenyApproval(), ()),
        context_runtime=ContextRuntime(small_policy()),
    )
    session.history.extend(
        [
            {"role": "user", "content": "old brief " + "x" * 160},
            {"role": "assistant", "content": "old answer " + "y" * 160},
        ]
    )

    assert session.ask("retry request") == "recovered"
    assert len(model.histories) == 2
    assert "COMPACTED CONTEXT" in str(model.histories[1])


def test_agent_session_continues_until_final_text_after_eight_tool_rounds(
    tmp_path: Path,
) -> None:
    turns = [
        ModelTurn("", (FunctionCall(f"call-{index}", "missing_tool", {}),)) for index in range(9)
    ]
    turns.append(ModelTurn("done", ()))
    model = RecordingModel(turns, [])
    session = AgentSession(
        model,
        ToolRegistry(()),
        ToolContext(tmp_path, DenyApproval(), ()),
    )

    assert session.ask("long request") == "done"
    assert len(model.histories) == 10


def test_jsonl_memory_store_round_trips_compaction_summary(tmp_path: Path) -> None:
    history: tuple[JsonObject, ...] = (
        {"role": "user", "content": "brief " + "x" * 200},
        {"role": "assistant", "content": "answer " + "y" * 200},
        {"role": "user", "content": "latest"},
    )
    runtime = ContextRuntime(small_policy())
    decision = runtime.prepare(history)
    assert decision.compaction is not None
    store = JsonlMemoryStore(tmp_path / "memory.jsonl")

    store.flush(decision.compaction)

    loaded = store.latest()
    assert loaded is not None
    assert loaded.source_digest == decision.compaction.source_digest


def test_tool_turns_are_never_compacted_between_call_and_result() -> None:
    history: tuple[JsonObject, ...] = (
        {"role": "user", "content": "run"},
        {"type": "function_call", "call_id": "call-1", "name": "trace_run"},
        {"type": "function_call_output", "call_id": "call-1", "output": "result"},
        {"role": "user", "content": "final"},
    )
    runtime = ContextRuntime(small_policy())

    decision = runtime.force_compact(history)

    assert decision.compaction is not None
    projection = str(decision.projection)
    assert "function_call" not in projection or "function_call_output" in projection
