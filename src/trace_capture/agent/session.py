from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from trace_capture.agent.context import (
    CompactionSummary,
    ContextEvent,
    ContextPhase,
    ContextRuntime,
    ContextTrigger,
    ContextUsage,
)
from trace_capture.agent.memory import MemoryStore, MemoryStoreError, NullMemoryStore
from trace_capture.providers.errors import ProviderError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from trace_capture.contracts.tools import ToolDescriptor
    from trace_capture.providers.codex import FunctionCall, ModelTurn
    from trace_capture.tools.models import ToolContext
    from trace_capture.tools.registry import ToolRegistry
    from trace_capture.transport.json_types import JsonObject


class ModelClient(Protocol):
    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn: ...


class AgentError(RuntimeError):
    pass


class ContextError(AgentError):
    pass


@dataclass(frozen=True, slots=True)
class AgentSession:
    client: ModelClient
    registry: ToolRegistry
    context: ToolContext
    history: list[JsonObject] = field(default_factory=list)
    context_runtime: ContextRuntime = field(default_factory=ContextRuntime)
    memory_store: MemoryStore = field(default_factory=NullMemoryStore)

    def ask(self, prompt: str) -> str:
        self.history.append({"role": "user", "content": prompt})
        overflow_retried = False
        while True:
            decision = self.context_runtime.prepare(self.history)
            if decision.compaction is not None:
                self._flush_memory(decision.compaction, decision.usage)
            try:
                turn = self.client.respond(decision.projection, self.registry.descriptors())
            except ProviderError as error:
                if not error.context_overflow or overflow_retried:
                    raise
                overflow_retried = True
                decision = self.context_runtime.force_compact(
                    self.history,
                    ContextTrigger.PROVIDER_OVERFLOW,
                )
                if decision.compaction is not None:
                    self._flush_memory(decision.compaction, decision.usage)
                self.context_runtime.notify(
                    ContextEvent(
                        phase=ContextPhase.OVERFLOW_RETRY,
                        trigger=ContextTrigger.PROVIDER_OVERFLOW,
                        usage=decision.usage,
                        summary=decision.compaction,
                    )
                )
                continue
            self.context_runtime.last_provider_metadata = turn.metadata
            self.context_runtime.notify(
                ContextEvent(
                    phase=ContextPhase.PROVIDER_RESPONSE,
                    trigger=ContextTrigger.TURN,
                    usage=decision.usage,
                    metadata=turn.metadata,
                )
            )
            if turn.calls:
                self._append_calls(turn)
                continue
            self.history.append({"role": "assistant", "content": turn.text})
            return turn.text

    def reset(self) -> None:
        self.history.clear()
        self.context_runtime.reset()

    def fork(self, history: Sequence[JsonObject] = ()) -> AgentSession:
        return AgentSession(
            client=self.client,
            registry=self.registry,
            context=self.context,
            history=list(history),
            context_runtime=ContextRuntime(self.context_runtime.policy),
            memory_store=self.memory_store,
        )

    def _flush_memory(self, summary: CompactionSummary, usage: ContextUsage) -> None:
        self.context_runtime.notify(
            ContextEvent(ContextPhase.FLUSHING_MEMORY, summary.trigger, usage, summary)
        )
        try:
            self.memory_store.flush(summary)
        except MemoryStoreError as error:
            raise ContextError(str(error)) from error

    def _append_calls(self, turn: ModelTurn) -> None:
        if turn.text:
            self.history.append({"role": "assistant", "content": turn.text})
        for call in turn.calls:
            self.history.append(
                {
                    "type": "function_call",
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": _arguments_json(call),
                }
            )
            result = self.registry.execute(call.name, call.arguments, self.context)
            output = result.output if result.ok else f"[{result.error_code}] {result.output}"
            self.history.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": output,
                }
            )


def _arguments_json(call: FunctionCall) -> str:
    return json.dumps(call.arguments, ensure_ascii=False, separators=(",", ":"))
