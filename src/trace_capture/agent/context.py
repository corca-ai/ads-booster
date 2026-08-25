from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from trace_capture.providers.models import ProviderResponseMetadata
    from trace_capture.transport.json_types import JsonObject


class ContextTrigger(StrEnum):
    TURN = "turn"
    SOFT_LIMIT = "soft_limit"
    HARD_LIMIT = "hard_limit"
    PROVIDER_OVERFLOW = "provider_overflow"
    MANUAL = "manual"


class ContextPhase(StrEnum):
    PROVIDER_RESPONSE = "provider_response"
    COMPACTION_STARTED = "compaction_started"
    COMPACTION_COMPLETED = "compaction_completed"
    FLUSHING_MEMORY = "flushing_memory"
    OVERFLOW_RETRY = "overflow_retry"


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    context_window_tokens: int = 128_000
    reserved_output_tokens: int = 16_000
    soft_ratio: float = 0.70
    hard_ratio: float = 0.85
    recent_tail_tokens: int = 16_000
    max_tool_output_chars: int = 6_000
    summary_max_chars: int = 6_000

    @property
    def soft_limit(self) -> int:
        return min(
            int(self.context_window_tokens * self.soft_ratio),
            self.context_window_tokens - self.reserved_output_tokens,
        )

    @property
    def hard_limit(self) -> int:
        return min(
            int(self.context_window_tokens * self.hard_ratio),
            self.context_window_tokens - self.reserved_output_tokens,
        )


@dataclass(frozen=True, slots=True)
class ContextUsage:
    estimated_input_tokens: int
    soft_limit: int
    hard_limit: int
    pruned_tool_outputs: int
    projection_version: int


@dataclass(frozen=True, slots=True)
class CompactionSummary:
    summary_id: str
    source_start: int
    source_end: int
    source_digest: str
    text: str
    trigger: ContextTrigger


@dataclass(frozen=True, slots=True)
class ContextDecision:
    projection: tuple[JsonObject, ...]
    usage: ContextUsage
    compaction: CompactionSummary | None = None
    pruned_tool_outputs: int = 0


@dataclass(frozen=True, slots=True)
class ContextEvent:
    phase: ContextPhase
    trigger: ContextTrigger
    usage: ContextUsage | None = None
    summary: CompactionSummary | None = None
    metadata: ProviderResponseMetadata | None = None


class ContextObserver(Protocol):
    def on_context_event(self, event: ContextEvent) -> None: ...


class ContextStateError(RuntimeError):
    pass


@dataclass(slots=True)  # noqa: MUTABLE_OK
class ContextRuntime:
    policy: ContextPolicy = ContextPolicy()
    summary: CompactionSummary | None = None
    boundary: int = 0
    projection_version: int = 0
    observer: ContextObserver | None = None
    last_usage: ContextUsage | None = None
    last_provider_metadata: ProviderResponseMetadata | None = None

    def prepare(
        self,
        history: Sequence[JsonObject],
        trigger: ContextTrigger = ContextTrigger.SOFT_LIMIT,
    ) -> ContextDecision:
        projection, pruned = self._project(history)
        usage = self._usage(projection, pruned)
        if usage.estimated_input_tokens <= self.policy.soft_limit:
            self.last_usage = usage
            return ContextDecision(projection, usage, None, pruned)
        pressure_trigger = (
            ContextTrigger.HARD_LIMIT
            if usage.estimated_input_tokens > self.policy.hard_limit
            else trigger
        )
        return self._compact(history, pressure_trigger)

    def force_compact(
        self,
        history: Sequence[JsonObject],
        trigger: ContextTrigger = ContextTrigger.MANUAL,
    ) -> ContextDecision:
        return self._compact(history, trigger)

    def reset(self) -> None:
        self.summary = None
        self.boundary = 0
        self.projection_version = 0
        self.last_usage = None
        self.last_provider_metadata = None

    def notify(self, event: ContextEvent) -> None:
        self._emit(event)

    def _project(self, history: Sequence[JsonObject]) -> tuple[tuple[JsonObject, ...], int]:
        entries: tuple[JsonObject, ...]
        if self.summary is None:
            entries = tuple(history)
        else:
            entries = (self._summary_entry(), *history[self.boundary :])
        return _prune_tool_outputs(entries, self.policy.max_tool_output_chars)

    def _compact(
        self,
        history: Sequence[JsonObject],
        trigger: ContextTrigger,
    ) -> ContextDecision:
        before, pruned = self._project(history)
        before_usage = self._usage(before, pruned)
        self._emit(ContextEvent(ContextPhase.COMPACTION_STARTED, trigger, before_usage))
        boundary = self._choose_boundary(history)
        if boundary <= self.boundary:
            self.last_usage = before_usage
            return ContextDecision(before, before_usage, None, pruned)
        source_start = self.boundary if self.summary is not None else 0
        source = tuple(history[source_start:boundary])
        text = _summary_text(self.summary, source, self.policy.summary_max_chars)
        summary = CompactionSummary(
            summary_id=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
            source_start=source_start,
            source_end=boundary,
            source_digest=_digest(source),
            text=text,
            trigger=trigger,
        )
        self.summary = summary
        self.boundary = boundary
        self.projection_version += 1
        projection, pruned = self._project(history)
        usage = self._usage(projection, pruned)
        self.last_usage = usage
        self._emit(ContextEvent(ContextPhase.COMPACTION_COMPLETED, trigger, usage, summary))
        return ContextDecision(projection, usage, summary, pruned)

    def _choose_boundary(self, history: Sequence[JsonObject]) -> int:
        candidates = [
            index
            for index, entry in enumerate(history)
            if index > self.boundary and entry.get("role") == "user"
        ]
        for candidate in candidates:
            if estimate_tokens(history[candidate:]) <= self.policy.recent_tail_tokens:
                return candidate
        return candidates[-1] if candidates else self.boundary

    def _summary_entry(self) -> JsonObject:
        summary = self.summary
        if summary is None:
            raise ContextStateError
        return {
            "role": "assistant",
            "content": f"[COMPACTED CONTEXT]\n{summary.text}",
        }

    def _usage(self, projection: Sequence[JsonObject], pruned: int) -> ContextUsage:
        return ContextUsage(
            estimated_input_tokens=estimate_tokens(projection),
            soft_limit=self.policy.soft_limit,
            hard_limit=self.policy.hard_limit,
            pruned_tool_outputs=pruned,
            projection_version=self.projection_version,
        )

    def _emit(self, event: ContextEvent) -> None:
        if self.observer is not None:
            self.observer.on_context_event(event)


def estimate_tokens(entries: Sequence[JsonObject]) -> int:
    encoded = json.dumps(entries, ensure_ascii=False, separators=(",", ":"))
    return max(1, (len(encoded) + 3) // 4)


def _prune_tool_outputs(
    entries: Sequence[JsonObject],
    max_chars: int,
) -> tuple[tuple[JsonObject, ...], int]:
    output: list[JsonObject] = []
    pruned = 0
    marker: Final = "[tool output pruned: "
    for entry in entries:
        if entry.get("type") != "function_call_output":
            output.append(entry)
            continue
        raw = entry.get("output")
        if not isinstance(raw, str) or len(raw) <= max_chars:
            output.append(entry)
            continue
        replacement = dict(entry)
        replacement["output"] = f"{marker}{len(raw)} chars]\n{raw[:max_chars]}"
        output.append(replacement)
        pruned += 1
    return tuple(output), pruned


def _summary_text(
    previous: CompactionSummary | None,
    source: Sequence[JsonObject],
    max_chars: int,
) -> str:
    lines = [
        (
            "Preserve the marketing goal, country/persona/caption decisions, artifact "
            "references, and unresolved validation gaps."
        ),
    ]
    if previous is not None:
        lines.append(f"Previous summary:\n{previous.text}")
    lines.extend(_entry_text(entry) for entry in source)
    return "\n".join(lines)[:max_chars]


def _entry_text(entry: JsonObject) -> str:
    kind = entry.get("role") or entry.get("type") or "event"
    content = entry.get("content")
    if isinstance(content, str):
        return f"- {kind}: {content[:1_200]}"
    name = entry.get("name")
    output = entry.get("output")
    if isinstance(output, str):
        return f"- {kind} {name or ''}: {output[:600]}"
    return f"- {kind}: {json.dumps(entry, ensure_ascii=False, separators=(',', ':'))[:600]}"


def _digest(entries: Sequence[JsonObject]) -> str:
    raw = json.dumps(entries, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
