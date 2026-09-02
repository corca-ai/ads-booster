"""Tests for the private-grader Marketing OS corpus loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from ads_booster.marketing.marketing_os_scorecard import (
    MarketingOsEvalCase,
    MarketingOsEvalExpectation,
    MarketingOsEvalInput,
    MarketingOsScorecardError,
    marketing_os_corpus_sha256,
)
from ads_booster.marketing.marketing_os_scorecard_corpus import (
    load_private_marketing_os_scorecard_cases,
)

_FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "marketing_os_scorecard" / "v1"
_INPUTS_SCHEMA = "trace.marketing-os-private-runner-inputs.v1"
_EXPECTATIONS_SCHEMA = "trace.marketing-os-private-grader-expectations.v1"


def test_private_corpus_loader_preserves_ordered_semantic_digest(tmp_path: Path) -> None:
    root = _write_valid_corpus(tmp_path / "normal")
    reordered_root = _write_valid_corpus(tmp_path / "reordered")
    reordered_path = reordered_root / "runner_inputs.json"
    reordered_payload = cast(
        "dict[str, object]", json.loads(reordered_path.read_text(encoding="utf-8"))
    )
    reordered_cases = cast("list[object]", reordered_payload["cases"])
    reordered_cases.reverse()
    _ = reordered_path.write_text(json.dumps(reordered_payload), encoding="utf-8")

    loaded = load_private_marketing_os_scorecard_cases(root)
    reordered = load_private_marketing_os_scorecard_cases(reordered_root)

    assert [item.input.case_id for item in loaded] == [
        "trace.marketing-os.v2.case-001",
        "trace.marketing-os.v2.case-002",
        "trace.marketing-os.v2.case-003",
        "trace.marketing-os.v2.case-004",
        "trace.marketing-os.v2.case-005",
    ]
    assert marketing_os_corpus_sha256(loaded) == marketing_os_corpus_sha256(_fixture_cases())
    assert [item.input.case_id for item in reordered] == list(
        reversed([item.input.case_id for item in loaded])
    )
    assert marketing_os_corpus_sha256(reordered) != marketing_os_corpus_sha256(loaded)


@pytest.mark.parametrize(
    ("prepare", "error_code"),
    [
        ("missing_root", "scorecard_corpus_root_invalid"),
        ("file_root", "scorecard_corpus_root_invalid"),
        ("missing_inputs", "scorecard_corpus_file_missing"),
        ("invalid_json", "scorecard_corpus_file_invalid"),
        ("wrong_envelope", "scorecard_corpus_envelope_invalid"),
    ],
)
def test_private_corpus_loader_fails_closed_for_root_and_envelope_errors(
    tmp_path: Path,
    prepare: str,
    error_code: str,
) -> None:
    root = tmp_path / "corpus"
    if prepare == "file_root":
        _ = root.write_text("not a directory", encoding="utf-8")
    elif prepare != "missing_root":
        root = _write_valid_corpus(tmp_path)
    if prepare == "missing_inputs":
        (root / "runner_inputs.json").unlink()
    if prepare == "invalid_json":
        _ = (root / "runner_inputs.json").write_text("[", encoding="utf-8")
    if prepare == "wrong_envelope":
        _ = (root / "runner_inputs.json").write_text(
            json.dumps({"schema_version": _INPUTS_SCHEMA, "cases": [], "extra": True}),
            encoding="utf-8",
        )

    with pytest.raises(MarketingOsScorecardError, match=error_code):
        _ = load_private_marketing_os_scorecard_cases(root)


def test_private_corpus_loader_rejects_symlink_escape(tmp_path: Path) -> None:
    root = _write_valid_corpus(tmp_path)
    escaped = tmp_path / "escaped-inputs.json"
    _ = escaped.write_text(
        (root / "runner_inputs.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (root / "runner_inputs.json").unlink()
    (root / "runner_inputs.json").symlink_to(escaped)

    with pytest.raises(MarketingOsScorecardError, match="scorecard_corpus_file_outside_root"):
        _ = load_private_marketing_os_scorecard_cases(root)


@pytest.mark.parametrize(
    ("target", "mutation", "error_code"),
    [
        ("runner_inputs.json", "duplicate", "scorecard_corpus_duplicate_runner_input"),
        (
            "grader_expectations.json",
            "duplicate",
            "scorecard_corpus_duplicate_grader_expectation",
        ),
        ("grader_expectations.json", "remove", "scorecard_corpus_case_sets_mismatch"),
    ],
)
def test_private_corpus_loader_rejects_unpaired_or_duplicate_cases(
    tmp_path: Path,
    target: str,
    mutation: str,
    error_code: str,
) -> None:
    root = _write_valid_corpus(tmp_path)
    path = root / target
    payload = cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))
    cases = cast("list[object]", payload["cases"])
    if mutation == "duplicate":
        cases.append(cases[0])
    else:
        _ = cases.pop()
    _ = path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MarketingOsScorecardError, match=error_code):
        _ = load_private_marketing_os_scorecard_cases(root)


def _write_valid_corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    root.mkdir(parents=True)
    runner_inputs = _fixture_json("inputs.json")
    grader_expectations = _fixture_json("grader_expectations.json")
    _ = (root / "runner_inputs.json").write_text(
        json.dumps({"schema_version": _INPUTS_SCHEMA, "cases": runner_inputs["cases"]}),
        encoding="utf-8",
    )
    _ = (root / "grader_expectations.json").write_text(
        json.dumps(
            {
                "schema_version": _EXPECTATIONS_SCHEMA,
                "cases": grader_expectations["cases"],
            }
        ),
        encoding="utf-8",
    )
    return root


def _fixture_cases() -> tuple[MarketingOsEvalCase, ...]:
    inputs = cast("list[dict[str, object]]", _fixture_json("inputs.json")["cases"])
    expectations = cast(
        "list[dict[str, object]]", _fixture_json("grader_expectations.json")["cases"]
    )
    expectations_by_id = {
        expectation["case_id"]: MarketingOsEvalExpectation.model_validate(expectation)
        for expectation in expectations
    }
    return tuple(
        MarketingOsEvalCase(
            MarketingOsEvalInput.model_validate(item), expectations_by_id[item["case_id"]]
        )
        for item in inputs
    )


def _fixture_json(name: str) -> dict[str, object]:
    return cast("dict[str, object]", json.loads((_FIXTURE_ROOT / name).read_text(encoding="utf-8")))
