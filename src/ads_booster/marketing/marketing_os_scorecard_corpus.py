"""Load one mounted corpus without invoking a runner or serializing expectations to one."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ads_booster.contracts.models import ContractModel
from ads_booster.marketing.marketing_os_scorecard import (
    MarketingOsEvalCase,
    MarketingOsEvalExpectation,
    MarketingOsEvalInput,
    MarketingOsScorecardError,
    marketing_os_corpus_sha256,
)

if TYPE_CHECKING:
    from pathlib import Path

_RUNNER_INPUTS_FILE = "runner_inputs.json"
_EXPECTATIONS_FILE = "grader_expectations.json"
_RUNNER_INPUTS_SCHEMA = "trace.marketing-os-private-runner-inputs.v1"
_EXPECTATIONS_SCHEMA = "trace.marketing-os-private-grader-expectations.v1"


def load_private_marketing_os_scorecard_cases(
    corpus_root: Path,
) -> tuple[MarketingOsEvalCase, ...]:
    """Load one fixed-layout corpus at the private grader boundary.

    The caller owns process and mount isolation. This loader intentionally receives no runner,
    verifier, provider, tool environment, selector, or fallback corpus.
    """
    root = _resolve_root(corpus_root)
    inputs = _load_records(
        _resolve_child(root, _RUNNER_INPUTS_FILE),
        expected_schema=_RUNNER_INPUTS_SCHEMA,
        model=MarketingOsEvalInput,
    )
    expectations = _load_records(
        _resolve_child(root, _EXPECTATIONS_FILE),
        expected_schema=_EXPECTATIONS_SCHEMA,
        model=MarketingOsEvalExpectation,
    )
    _require_unique_case_ids(inputs, "scorecard_corpus_duplicate_runner_input")
    _require_unique_case_ids(expectations, "scorecard_corpus_duplicate_grader_expectation")
    expectations_by_id = {item.case_id: item for item in expectations}
    if {item.case_id for item in inputs} != set(expectations_by_id):
        raise MarketingOsScorecardError("scorecard_corpus_case_sets_mismatch")
    cases = tuple(MarketingOsEvalCase(item, expectations_by_id[item.case_id]) for item in inputs)
    _ = marketing_os_corpus_sha256(cases)
    return cases


def _resolve_root(corpus_root: Path) -> Path:
    try:
        root = corpus_root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise MarketingOsScorecardError("scorecard_corpus_root_invalid") from error
    if not root.is_dir():
        raise MarketingOsScorecardError("scorecard_corpus_root_invalid")
    return root


def _resolve_child(root: Path, filename: str) -> Path:
    try:
        child = (root / filename).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise MarketingOsScorecardError("scorecard_corpus_file_missing") from error
    if child.parent != root or not child.is_file():
        raise MarketingOsScorecardError("scorecard_corpus_file_outside_root")
    return child


def _load_records[T: ContractModel](
    path: Path,
    *,
    expected_schema: str,
    model: type[T],
) -> tuple[T, ...]:
    try:
        raw_value = cast("object", json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MarketingOsScorecardError("scorecard_corpus_file_invalid") from error
    if not isinstance(raw_value, dict):
        raise MarketingOsScorecardError("scorecard_corpus_envelope_invalid")
    raw = cast("dict[str, object]", raw_value)
    if set(raw) != {"schema_version", "cases"}:
        raise MarketingOsScorecardError("scorecard_corpus_envelope_invalid")
    if raw.get("schema_version") != expected_schema or not isinstance(raw.get("cases"), list):
        raise MarketingOsScorecardError("scorecard_corpus_envelope_invalid")
    cases = cast("list[object]", raw["cases"])
    if not cases or any(not isinstance(item, dict) for item in cases):
        raise MarketingOsScorecardError("scorecard_corpus_records_invalid")
    try:
        return tuple(model.model_validate(item) for item in cases)
    except ValueError as error:
        raise MarketingOsScorecardError("scorecard_corpus_records_invalid") from error


def _require_unique_case_ids[T: MarketingOsEvalInput | MarketingOsEvalExpectation](
    cases: tuple[T, ...],
    error_code: str,
) -> None:
    case_ids = tuple(item.case_id for item in cases)
    if len(set(case_ids)) != len(case_ids):
        raise MarketingOsScorecardError(error_code)
