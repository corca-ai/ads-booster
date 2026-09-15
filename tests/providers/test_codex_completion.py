from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

import pytest
from jsonschema import Draft202012Validator

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.task_completion import SemanticAssessmentRequest, SemanticObligation
from ads_booster.providers.codex_completion import CodexCompletionAssessor, CodexCompletionError
from tests.marketing.agent_service.completion_fixtures import response_case

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject, JsonValue


def assert_strict_schema(value: JsonValue) -> None:
    match value:
        case dict() as mapping:
            assert "$ref" not in mapping
            assert "$defs" not in mapping
            assert "default" not in mapping
            assert "oneOf" not in mapping
            assert "discriminator" not in mapping
            properties = mapping.get("properties")
            if isinstance(properties, dict):
                required = mapping.get("required")
                assert isinstance(required, list)
                assert mapping.get("additionalProperties") is False
                assert set(required) == set(properties)
            for nested in mapping.values():
                assert_strict_schema(nested)
        case list() as sequence:
            for item in sequence:
                assert_strict_schema(item)
        case str() | int() | float() | None:
            return
        case _:
            assert_never(value)


class SemanticRunner:
    def __init__(self, result: JsonObject) -> None:
        self.result: JsonObject = result
        self.requests: list[SemanticAssessmentRequest] = []
        self.schema: JsonObject = {}

    def run_marketing_judgment_job(
        self, prompt: str, schema: JsonObject, *, workspace: Path, timeout_seconds: float
    ) -> JsonObject:
        assert workspace.is_dir()
        assert timeout_seconds == 30
        assert_strict_schema(schema)
        self.schema = schema
        self.requests.append(
            SemanticAssessmentRequest.model_validate_json(
                prompt.split("Canonical assessment request:\n")[1]
            )
        )
        return self.result


def assessment_request(database: Path) -> SemanticAssessmentRequest:
    case = response_case(database)
    return SemanticAssessmentRequest(
        task_spec_sha256=contract_sha256(case.task),
        original_objective=case.task.original_objective,
        original_criteria=case.task.original_criteria,
        objective=case.task.objective,
        constraints=case.task.constraints,
        candidate=case.candidate,
        obligations=(
            SemanticObligation(
                obligation_id="tip",
                kind="response",
                description="One sentence of advice",
                required=True,
            ),
        ),
    )


def test_assessment_schema_binds_obligation_identifiers(tmp_path: Path) -> None:
    output = semantic_output()
    runner = SemanticRunner(output)
    _ = CodexCompletionAssessor(runner, tmp_path, "fixture", 30).assess(
        assessment_request(tmp_path / "state.db")
    )
    obligations = output["obligations"]
    assert isinstance(obligations, list)
    assert isinstance(obligations[0], dict)
    obligations[0]["obligation_id"] = "mistyped-tip"
    assert not Draft202012Validator(runner.schema).is_valid(output)  # pyright: ignore[reportUnknownMemberType]


def semantic_output() -> JsonObject:
    return {
        "schema_version": "trace.semantic-assessment-result.v1",
        "obligations": [
            {
                "obligation_id": "tip",
                "status": "satisfied",
                "mechanism": "semantic",
                "reason": "One sentence contains actionable launch advice",
                "evidence_sha256s": [],
                "superseded_by_event_id": None,
            }
        ],
        "uncovered_requirements": [],
        "required_evidence_kinds": [],
        "requested_deliverables_supported": True,
    }


@pytest.mark.parametrize("handles", [(), ("a" * 64,)])
def test_assessor_can_only_cite_host_evidence_handles(
    tmp_path: Path, handles: tuple[str, ...]
) -> None:
    request = assessment_request(tmp_path / "state.db").model_copy(
        update={"evidence_sha256s": handles}
    )
    output = semantic_output()
    runner = SemanticRunner(output)
    _ = CodexCompletionAssessor(runner, tmp_path, "fixture", 30).assess(request)
    obligations = output["obligations"]
    assert isinstance(obligations, list)
    assert isinstance(obligations[0], dict)
    # A receipt/output digest is also a SHA256, but is not a host evidence handle.
    obligations[0]["evidence_sha256s"] = ["b" * 64]
    assert not Draft202012Validator(runner.schema).is_valid(output)  # pyright: ignore[reportUnknownMemberType]


def test_semantic_adapter_binds_exact_request_and_candidate(tmp_path: Path) -> None:
    request = assessment_request(tmp_path / "state.db")
    runner = SemanticRunner(semantic_output())

    result = CodexCompletionAssessor(runner, tmp_path, "fixture", 30).assess(request)

    assert result.request_sha256 == contract_sha256(request)
    assert result.candidate_sha256 == contract_sha256(request.candidate)
    assert runner.requests == [request]
    assert result.requested_deliverables_supported


def test_codex_completion_assessor_identity_binds_model_and_prompt_schema(tmp_path: Path) -> None:
    runner = SemanticRunner(semantic_output())
    first = CodexCompletionAssessor(runner, tmp_path, "model-a", 30)
    same = CodexCompletionAssessor(runner, tmp_path, "model-a", 30)
    changed = CodexCompletionAssessor(runner, tmp_path, "model-b", 30)

    assert first.assessment_identity == same.assessment_identity
    assert first.assessment_identity != changed.assessment_identity


@pytest.mark.parametrize(
    "change",
    [
        {"requested_deliverables_supported": "true"},
        {"obligations": []},
        {"request_sha256": "a" * 64},
        {"uncovered_requirements": ["Missing image"]},
    ],
)
def test_invalid_semantic_wire_fails_closed(tmp_path: Path, change: JsonObject) -> None:
    request = assessment_request(tmp_path / "state.db")
    runner = SemanticRunner({**semantic_output(), **change})

    with pytest.raises(CodexCompletionError):
        _ = CodexCompletionAssessor(runner, tmp_path, "fixture", 30).assess(request)
