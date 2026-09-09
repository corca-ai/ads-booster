from __future__ import annotations

# pyright: reportPrivateUsage=false
import json
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.transport.json_types import JsonObject

# ruff: noqa: S106
from tests.operations.installed_learning_model_canary import (
    BoundaryFacts,
    ClearCorrectionFacts,
    EvidenceRecorder,
    LearningFacts,
    ReceiptFacts,
    _matching_selected_targets,
    _projected_search_results,
    evaluate_canary,
)

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)

if TYPE_CHECKING:
    from pathlib import Path


def test_canary_evaluation_uses_observed_learning_and_receipt_outcomes() -> None:
    result = evaluate_canary(
        learning=LearningFacts(
            baseline_response="승인 상태를 확인할 수 없습니다.",
            after_response="LANTERN-AMBER",
            expected_token="LANTERN-AMBER",
            baseline_query_sha256="a" * 64,
            after_query_sha256="a" * 64,
            selected_source_revisions=("source.r1",),
            selected_skill_revisions=("skill.r1",),
            selected_skill_source_revisions=("source@r1",),
            applied_operation_ids=("operation.learn",),
            pre_terminal_receipt_count=9,
            head_changed_after_terminal_seal=True,
            experience_source_binding_count=10,
            selected_operation_target_count=1,
        ),
        clear_correction=ClearCorrectionFacts(
            baseline_response="unknown",
            after_response="LANTERN-AMBER",
            expected_token="LANTERN-AMBER",
            baseline_query_sha256="b" * 64,
            after_query_sha256="b" * 64,
            source_linked_operation_ids=("operation.correction",),
        ),
        receipts=ReceiptFacts(
            outcomes=("observed",) * 8 + ("failed",) * 2,
            admitted_outcomes=("observed",) * 8 + ("failed",) * 2,
            curation_result_projection_count=10,
            canonical_evidence_binding_count=10,
        ),
        boundaries=BoundaryFacts(
            private_counter_delta=0,
            private_head_changed=False,
            task_overlay_applied=True,
            same_task_selected=True,
            new_member_selected=False,
            task_same_response="TASK-EMBER",
            task_new_member_response="unknown",
            task_expected_token="TASK-EMBER",
            task_overlay_in_core=False,
            task_overlay_in_skill_heads=False,
            builtin_model_attempted=False,
            builtin_model_attempt_rejected=None,
            builtin_host_rejected=True,
            builtin_heads_changed=False,
            conflict_question_id="question.dynamic.1",
            conflict_status="pending",
            conflict_delivery_state="unknown",
            conflict_retry_count=0,
            conflict_approval_records=0,
        ),
    )

    assert result["learning-before-after"]["status"] == "passed"
    assert result["clear-correction"]["status"] == "passed"
    assert result["terminal-experiences"]["status"] == "passed"
    assert result["task-only-boundary"]["status"] == "passed"
    assert result["private-boundary"]["status"] == "passed"
    assert result["builtin-protection"]["status"] == "passed"
    assert result["conflict-unknown-send"]["status"] == "passed"


def test_canary_evaluation_reports_each_failed_observable_without_blanket_pass() -> None:
    result = evaluate_canary(
        learning=LearningFacts(
            baseline_response="LANTERN-AMBER",
            after_response="LANTERN-AMBER",
            expected_token="LANTERN-AMBER",
            baseline_query_sha256="a" * 64,
            after_query_sha256="b" * 64,
            selected_source_revisions=(),
            selected_skill_revisions=(),
            selected_skill_source_revisions=(),
            applied_operation_ids=(),
            pre_terminal_receipt_count=8,
            head_changed_after_terminal_seal=False,
            experience_source_binding_count=0,
            selected_operation_target_count=0,
        ),
        clear_correction=ClearCorrectionFacts(
            baseline_response="LANTERN-AMBER",
            after_response="LANTERN-AMBER",
            expected_token="LANTERN-AMBER",
            baseline_query_sha256="b" * 64,
            after_query_sha256="c" * 64,
            source_linked_operation_ids=(),
        ),
        receipts=ReceiptFacts(
            outcomes=("observed",) * 10,
            admitted_outcomes=("observed",) * 10,
            curation_result_projection_count=0,
            canonical_evidence_binding_count=0,
        ),
        boundaries=BoundaryFacts(
            private_counter_delta=1,
            private_head_changed=True,
            task_overlay_applied=False,
            same_task_selected=False,
            new_member_selected=True,
            task_same_response="unknown",
            task_new_member_response="TASK-EMBER",
            task_expected_token="TASK-EMBER",
            task_overlay_in_core=True,
            task_overlay_in_skill_heads=True,
            builtin_model_attempted=True,
            builtin_model_attempt_rejected=False,
            builtin_host_rejected=False,
            builtin_heads_changed=True,
            conflict_question_id=None,
            conflict_status=None,
            conflict_delivery_state=None,
            conflict_retry_count=1,
            conflict_approval_records=1,
        ),
    )

    assert set(result) == {
        "learning-before-after",
        "clear-correction",
        "terminal-experiences",
        "task-only-boundary",
        "private-boundary",
        "builtin-protection",
        "conflict-unknown-send",
    }
    assert {case["status"] for case in result.values()} == {"failed"}


def test_result_projection_requires_canonical_output_not_only_matching_query_text(
    tmp_path: Path,
) -> None:
    recorder = EvidenceRecorder(
        output=tmp_path / "partial.json",
        invocation="fixture",
        model_id="fixture",
        started_at="2026-09-08T00:00:00+00:00",
    )
    output = _JSON_OBJECT.validate_python(
        {
            "fixture": "synthetic-procedure-evidence",
            "query": "canary-success-1",
            "procedure_code": "ORBIT-SILVER",
            "check_outcome": "completed",
            "error_code": None,
        }
    )
    recorder.search_calls.append(
        _JSON_OBJECT.validate_python({"run_id": "run.1", "output": output})
    )
    recorder.curation_calls.append(
        _JSON_OBJECT.validate_python({"request": {"objective": "Run query canary-success-1"}})
    )

    assert _projected_search_results(recorder, ("run.1",)) == 0

    recorder.curation_calls.append(
        _JSON_OBJECT.validate_python(
            {"observations": [{"output_json_excerpt": json.dumps(output)}]}
        )
    )

    assert _projected_search_results(recorder, ("run.1",)) == 1


def test_selected_target_must_match_terminal_operation_and_skill_source_lineage() -> None:
    operation = _JSON_OBJECT.validate_python(
        {
            "operation_id": "operation.1",
            "target_kind": "skill",
            "target_id": "skill.learned",
            "revision_id": "skill.learned.r1",
            "content_sha256": "a" * 64,
            "source_refs": [{"evidence_id": "event.1"}],
        }
    )
    selected = _JSON_OBJECT.validate_python(
        {
            "target_kind": "skill",
            "target_id": "skill.learned",
            "revision_id": "skill.learned.r1",
            "content_sha256": "a" * 64,
            "source_revisions": [
                {
                    "source_id": "source.1",
                    "revision_id": "revision.1",
                    "content_sha256": "b" * 64,
                }
            ],
        }
    )
    lineage = _JSON_OBJECT.validate_python(
        {
            "source_event_id": "event.1",
            "source_id": "source.1",
            "source_revision_id": "revision.1",
            "source_content_sha256": "b" * 64,
        }
    )

    assert _matching_selected_targets((operation,), (selected,), (lineage,)) == (operation,)

    wrong_revision = _JSON_OBJECT.validate_python(
        {**selected, "revision_id": "skill.learned.unrelated"}
    )

    assert _matching_selected_targets((operation,), (wrong_revision,), (lineage,)) == ()
