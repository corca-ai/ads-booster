from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Protocol

from trace_capture.candidate_generation import (
    CandidateGenerationError,
    CandidateGeneratorPort,
    CandidateImageRunnerPort,
    CandidateImageStageError,
)
from trace_capture.marketing.inbox import MarketingExecutionError
from trace_capture.marketing.models import (
    ArtifactReference,
    MarketingTask,
    TaskKind,
    TaskResult,
    TaskStatus,
)
from trace_capture.transport.json_types import JsonObject, JsonValue
from trace_capture.workspace import CandidateId, CandidateRecord, CandidateStatus, WorkspaceId

if TYPE_CHECKING:
    from pathlib import Path

_MAX_CANDIDATE_SELECTION = 8


class TaskHandler(Protocol):
    def __call__(self, task: MarketingTask) -> TaskResult: ...


class CandidateReviewStore(Protocol):
    def get_candidate(
        self,
        workspace_id: WorkspaceId,
        candidate_id: CandidateId,
    ) -> CandidateRecord: ...


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
        if task.kind is TaskKind.PUBLISH and task.payload.get("adapter_mode") != "simulation":
            raise MarketingExecutionError("live_adapter_unavailable")
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
                return {**common, "candidate_ids": [f"candidate-{task.run_id[:8]}"]}
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


@dataclass(frozen=True, slots=True)
class CandidatePipelineExecutor:
    """Connect Cloudflare tasks to PR #22's installed candidate journey.

    Only generation and image capture become real local work. The fallback still owns
    research, publication, and metrics, so enabling this executor cannot silently publish.
    """

    generator: CandidateGeneratorPort
    image_runner: CandidateImageRunnerPort
    store: CandidateReviewStore
    artifact_root: Path
    fallback: ArtifactSimulationExecutor

    def execute(self, task: MarketingTask) -> TaskResult:
        match task.kind:
            case TaskKind.GENERATE_CANDIDATES:
                return self._generate(task)
            case TaskKind.CAPTURE:
                return self._capture(task)
            case TaskKind.PUBLISH:
                self._require_submitted(task)
                return self.fallback.execute(task)
            case _:
                return self.fallback.execute(task)

    def _generate(self, task: MarketingTask) -> TaskResult:
        workspace_id = self._workspace_id(task)
        context = {
            "shared_instruction": task.payload.get("shared_instruction"),
            "private_memory": task.payload.get("private_memory", []),
            "research": task.payload.get("research", {}),
        }
        try:
            records = self.generator.generate(
                workspace_id,
                run_context=json.dumps(context, ensure_ascii=False, sort_keys=True),
            )
        except CandidateGenerationError as error:
            raise MarketingExecutionError("candidate_generation_failed") from error
        candidate_ids: list[JsonValue] = [str(record.candidate_id) for record in records]
        return TaskResult(
            status=TaskStatus.SUCCEEDED,
            output={
                "candidate_ids": candidate_ids,
                "candidates": [self._candidate_summary(record) for record in records],
            },
        )

    def _capture(self, task: MarketingTask) -> TaskResult:
        workspace_id = self._workspace_id(task)
        candidate_ids = self._candidate_ids(task)
        try:
            records = [
                self.image_runner.generate(workspace_id, CandidateId(candidate_id))
                for candidate_id in candidate_ids
            ]
        except CandidateImageStageError as error:
            raise MarketingExecutionError("candidate_capture_failed") from error
        artifacts = tuple(self._artifact(record) for record in records)
        candidate_values: list[JsonValue] = list(candidate_ids)
        return TaskResult(
            status=TaskStatus.SUCCEEDED,
            output={
                "candidate_ids": candidate_values,
                "images": [self._candidate_summary(record) for record in records],
                "quality": "awaiting_image_review",
            },
            artifacts=artifacts,
        )

    def _require_submitted(self, task: MarketingTask) -> None:
        workspace_id = self._workspace_id(task)
        for candidate_id in self._candidate_ids(task):
            try:
                record = self.store.get_candidate(workspace_id, CandidateId(candidate_id))
            except Exception as error:
                raise MarketingExecutionError("candidate_review_state_invalid") from error
            if record.status is not CandidateStatus.SUBMITTED:
                raise MarketingExecutionError("candidate_not_image_approved")

    def _artifact(self, record: CandidateRecord) -> ArtifactReference:
        if record.image_path is None or record.image_sha256 is None:
            raise MarketingExecutionError("candidate_artifact_missing")
        root = self.artifact_root.resolve()
        path = (root / record.image_path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise MarketingExecutionError("candidate_artifact_missing")
        return ArtifactReference(uri=str(path), sha256=record.image_sha256)

    @staticmethod
    def _candidate_summary(record: CandidateRecord) -> JsonObject:
        return {
            "candidate_id": str(record.candidate_id),
            "topic": record.topic,
            "caption": record.caption,
            "status": record.status,
            "revision": record.revision,
            "image_sha256": record.image_sha256,
        }

    @staticmethod
    def _workspace_id(task: MarketingTask) -> WorkspaceId:
        value = task.payload.get("workspace_id")
        if not isinstance(value, str) or not value:
            raise MarketingExecutionError("missing_workspace_id")
        return WorkspaceId(value)

    @staticmethod
    def _candidate_ids(task: MarketingTask) -> list[str]:
        value = task.payload.get("candidate_ids")
        if (
            not isinstance(value, list)
            or not value
            or len(value) > _MAX_CANDIDATE_SELECTION
            or any(not isinstance(item, str) or not item for item in value)
        ):
            raise MarketingExecutionError("invalid_candidate_ids")
        candidate_ids = [item for item in value if isinstance(item, str)]
        return list(dict.fromkeys(candidate_ids))
