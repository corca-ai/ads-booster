from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pytest
from pydantic import TypeAdapter

from ads_booster.knowledge.curation_contracts import (
    CurationBatchDecision,
    CurationBatchJobContext,
    CurationBatchJobDecision,
    CurationDecision,
    CurationDecisionAction,
    CurationMemoryDestination,
    CurationMemoryIntent,
    CurationRequest,
)
from ads_booster.providers.codex_knowledge import CodexKnowledgeProvider
from ads_booster.transport.json_types import JsonObject, JsonValue

if TYPE_CHECKING:
    from pathlib import Path

_JSON_OBJECT: Final[TypeAdapter[JsonObject]] = TypeAdapter(JsonObject)


@dataclass(frozen=True, slots=True)
class SchemaInspectingRunner:
    response: JsonObject

    def run_marketing_judgment_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        assert prompt
        assert workspace.is_dir()
        assert timeout_seconds > 0
        definitions = _JSON_OBJECT.validate_python(schema["$defs"])
        intent = _JSON_OBJECT.validate_python(definitions["CurationMemoryIntent"])
        properties = _JSON_OBJECT.validate_python(intent["properties"])
        destination = _JSON_OBJECT.validate_python(properties["destination"])
        assert destination == {"$ref": "#/$defs/CurationMemoryDestination"}
        required = TypeAdapter(list[str]).validate_python(intent["required"])
        assert "destination" in required
        enum = _JSON_OBJECT.validate_python(definitions["CurationMemoryDestination"])
        assert enum["enum"] == ["channel", "user"]
        _assert_reference_defaults_removed(schema)
        return self.response


def _assert_reference_defaults_removed(value: JsonValue) -> None:
    match value:
        case dict() as item:
            if "$ref" in item:
                assert "default" not in item
            for nested in item.values():
                _assert_reference_defaults_removed(nested)
        case list() as items:
            for nested in items:
                _assert_reference_defaults_removed(nested)
        case str() | int() | float() | None:
            return


@pytest.mark.parametrize("batch", [False, True])
def test_provider_wire_schema_preserves_destination_and_input_defaults(
    batch: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    model = CurationBatchDecision if batch else CurationDecision
    original = _JSON_OBJECT.validate_python(model.model_json_schema())
    before = deepcopy(original)
    monkeypatch.setattr(model, "model_json_schema", lambda: original)
    request = CurationRequest(
        schema="knowledge.curation-request.v1",
        job_id="job.schema",
        event_id="event.schema",
        event_revision=1,
        policy_version="policy.v1",
        objective="Inspect the wire contract",
        started_at=datetime.now(UTC),
    )
    decision = CurationDecision(
        schema="knowledge.curation-decision.v1",
        action=CurationDecisionAction.FINISH,
        finish_summary="Complete",
    )
    response = (
        CurationBatchDecision(
            schema="knowledge.curation-batch-decision.v1",
            batch_id="batch.schema",
            decisions=(CurationBatchJobDecision(job_id=request.job_id, decision=decision),),
        )
        if batch
        else decision
    )
    provider = CodexKnowledgeProvider(
        SchemaInspectingRunner(
            _JSON_OBJECT.validate_python(response.model_dump(mode="json", by_alias=True))
        ),
        tmp_path,
        "test-model",
    )
    # When
    if batch:
        result = provider.decide_batch(
            "batch.schema", (CurationBatchJobContext(request=request),), timeout_seconds=1
        )
        assert result.decisions[0].decision == decision
    else:
        assert provider.decide(request, (), timeout_seconds=1) == decision
    # Then
    assert original == before
    assert (
        CurationMemoryIntent(
            subject_key="project", text="A fact", evidence_ids=("message.1",)
        ).destination
        is CurationMemoryDestination.CHANNEL
    )
