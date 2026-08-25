from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Protocol

from trace_capture.marketing.inbox import MarketingExecutionError
from trace_capture.marketing.models import (
    ArtifactReference,
    MarketingTask,
    TaskKind,
    TaskResult,
    TaskStatus,
)
from trace_capture.transport.json_types import JsonObject

if TYPE_CHECKING:
    from pathlib import Path


class TaskHandler(Protocol):
    def __call__(self, task: MarketingTask) -> TaskResult: ...


@dataclass(frozen=True, slots=True)
class DispatchingTaskExecutor:
    handlers: dict[TaskKind, TaskHandler]

    def execute(self, task: MarketingTask) -> TaskResult:
        handler = self.handlers.get(task.kind)
        if handler is None:
            raise MarketingExecutionError(f"unsupported_task_kind:{task.kind}")
        return handler(task)


@dataclass(frozen=True, slots=True)
class ArtifactSimulationExecutor:
    """Closes the transport loop without claiming a real channel side effect.

    Production handlers can replace one task kind at a time through
    ``DispatchingTaskExecutor``. Every simulated result is labeled and digest-backed.
    """

    artifact_root: Path

    def execute(self, task: MarketingTask) -> TaskResult:
        directory = self.artifact_root / task.account_id / task.run_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{task.kind}.json"
        content = json.dumps(
            {
                "simulation": True,
                "task": task.model_dump(mode="json"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        _ = path.write_bytes(content)
        digest = sha256(content).hexdigest()
        return TaskResult(
            status=TaskStatus.SUCCEEDED,
            output=self._output(task),
            artifacts=(ArtifactReference(uri=str(path.resolve()), sha256=digest),),
        )

    @staticmethod
    def _output(task: MarketingTask) -> JsonObject:
        common: JsonObject = {
            "simulation": True,
            "kind": task.kind,
        }
        match task.kind:
            case TaskKind.RESEARCH:
                return {**common, "signals": ["customer language", "organic conversation"]}
            case TaskKind.GENERATE_CANDIDATES:
                return {**common, "candidate_id": f"candidate-{task.run_id[:8]}"}
            case TaskKind.CAPTURE:
                return {**common, "quality": "pass"}
            case TaskKind.PUBLISH:
                return {
                    **common,
                    "publication_id": f"sim://threads/{task.account_id}/{task.run_id}",
                }
            case TaskKind.SAMPLE_METRICS:
                minute_value = task.payload.get("minute", 0)
                minute = minute_value if isinstance(minute_value, int) else 0
                seed = int(sha256(task.account_id.encode()).hexdigest()[:8], 16)
                return {
                    **common,
                    "minute": minute,
                    "views": seed % 100 + minute * 7,
                    "likes": seed % 11 + minute // 5,
                }
