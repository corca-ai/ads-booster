"""Load dynamic-research runner inputs and private grader expectations from separate files."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ads_booster.contracts.models import ContractModel
from ads_booster.marketing.dynamic_research_canary import (
    DynamicResearchCanaryCase,
    DynamicResearchCanaryError,
    DynamicResearchCanaryExpectation,
    DynamicResearchCanaryInput,
    dynamic_research_canary_corpus_sha256,
)

if TYPE_CHECKING:
    from pathlib import Path

_INPUTS_FILE = "runner_inputs.json"
_EXPECTATIONS_FILE = "grader_expectations.json"
_INPUTS_SCHEMA = "trace.dynamic-research-canary-inputs.v1"
_EXPECTATIONS_SCHEMA = "trace.dynamic-research-canary-expectations.v1"


def load_private_dynamic_research_canary_cases(
    corpus_root: Path,
) -> tuple[DynamicResearchCanaryCase, ...]:
    """Pair mounted inputs and expectations without returning grader data to a runner."""
    root = _resolve_root(corpus_root)
    inputs = _load_records(
        _resolve_child(root, _INPUTS_FILE),
        schema=_INPUTS_SCHEMA,
        model=DynamicResearchCanaryInput,
    )
    expectations = _load_records(
        _resolve_child(root, _EXPECTATIONS_FILE),
        schema=_EXPECTATIONS_SCHEMA,
        model=DynamicResearchCanaryExpectation,
    )
    _require_unique_ids(inputs, "dynamic_research_duplicate_runner_input")
    _require_unique_ids(expectations, "dynamic_research_duplicate_grader_expectation")
    by_id = {item.case_id: item for item in expectations}
    if {item.case_id for item in inputs} != set(by_id):
        raise DynamicResearchCanaryError("dynamic_research_case_sets_mismatch")
    cases = tuple(DynamicResearchCanaryCase(item, by_id[item.case_id]) for item in inputs)
    _ = dynamic_research_canary_corpus_sha256(cases)
    return cases


def _resolve_root(corpus_root: Path) -> Path:
    try:
        root = corpus_root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise DynamicResearchCanaryError("dynamic_research_corpus_root_invalid") from error
    if not root.is_dir():
        raise DynamicResearchCanaryError("dynamic_research_corpus_root_invalid")
    return root


def _resolve_child(root: Path, filename: str) -> Path:
    try:
        child = (root / filename).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise DynamicResearchCanaryError("dynamic_research_corpus_file_missing") from error
    if child.parent != root or not child.is_file():
        raise DynamicResearchCanaryError("dynamic_research_corpus_file_outside_root")
    return child


def _load_records[T: ContractModel](
    path: Path,
    *,
    schema: str,
    model: type[T],
) -> tuple[T, ...]:
    try:
        raw_value = cast("object", json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DynamicResearchCanaryError("dynamic_research_corpus_file_invalid") from error
    if not isinstance(raw_value, dict):
        raise DynamicResearchCanaryError("dynamic_research_corpus_envelope_invalid")
    raw = cast("dict[str, object]", raw_value)
    if set(raw) != {"schema_version", "cases"}:
        raise DynamicResearchCanaryError("dynamic_research_corpus_envelope_invalid")
    records = raw.get("cases")
    if raw.get("schema_version") != schema or not isinstance(records, list) or not records:
        raise DynamicResearchCanaryError("dynamic_research_corpus_envelope_invalid")
    try:
        return tuple(model.model_validate(item) for item in cast("list[object]", records))
    except ValueError as error:
        raise DynamicResearchCanaryError("dynamic_research_corpus_records_invalid") from error


def _require_unique_ids(
    items: tuple[DynamicResearchCanaryInput | DynamicResearchCanaryExpectation, ...],
    error_code: str,
) -> None:
    identifiers = tuple(item.case_id for item in items)
    if len(set(identifiers)) != len(identifiers):
        raise DynamicResearchCanaryError(error_code)
