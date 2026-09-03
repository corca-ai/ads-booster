"""Load private marketing-judgment inputs and grader expectations from separate files."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ads_booster.contracts.models import ContractModel
from ads_booster.marketing.marketing_judgment_canary import (
    MarketingJudgmentCanaryCase,
    MarketingJudgmentCanaryError,
    MarketingJudgmentCanaryExpectation,
    MarketingJudgmentCanaryInput,
    marketing_judgment_canary_corpus_sha256,
)

if TYPE_CHECKING:
    from pathlib import Path

_INPUTS_FILE = "runner_inputs.json"
_EXPECTATIONS_FILE = "grader_expectations.json"
_INPUTS_SCHEMA = "trace.marketing-judgment-canary-inputs.v1"
_EXPECTATIONS_SCHEMA = "trace.marketing-judgment-canary-expectations.v1"


def load_private_marketing_judgment_canary_cases(
    corpus_root: Path,
) -> tuple[MarketingJudgmentCanaryCase, ...]:
    """Pair public runner inputs with separately mounted private grader expectations."""
    root = _resolve_root(corpus_root)
    inputs = _load_records(
        _resolve_child(root, _INPUTS_FILE),
        schema=_INPUTS_SCHEMA,
        model=MarketingJudgmentCanaryInput,
    )
    expectations = _load_records(
        _resolve_child(root, _EXPECTATIONS_FILE),
        schema=_EXPECTATIONS_SCHEMA,
        model=MarketingJudgmentCanaryExpectation,
    )
    _require_unique_ids(inputs, "judgment_canary_duplicate_runner_input")
    _require_unique_ids(expectations, "judgment_canary_duplicate_grader_expectation")
    by_id = {item.case_id: item for item in expectations}
    if {item.case_id for item in inputs} != set(by_id):
        raise MarketingJudgmentCanaryError("judgment_canary_case_sets_mismatch")
    cases = tuple(MarketingJudgmentCanaryCase(item, by_id[item.case_id]) for item in inputs)
    _ = marketing_judgment_canary_corpus_sha256(cases)
    return cases


def _resolve_root(corpus_root: Path) -> Path:
    try:
        root = corpus_root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise MarketingJudgmentCanaryError("judgment_canary_corpus_root_invalid") from error
    if not root.is_dir():
        raise MarketingJudgmentCanaryError("judgment_canary_corpus_root_invalid")
    return root


def _resolve_child(root: Path, filename: str) -> Path:
    try:
        child = (root / filename).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise MarketingJudgmentCanaryError("judgment_canary_corpus_file_missing") from error
    if child.parent != root or not child.is_file():
        raise MarketingJudgmentCanaryError("judgment_canary_corpus_file_outside_root")
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
        raise MarketingJudgmentCanaryError("judgment_canary_corpus_file_invalid") from error
    if not isinstance(raw_value, dict):
        raise MarketingJudgmentCanaryError("judgment_canary_corpus_envelope_invalid")
    raw = cast("dict[str, object]", raw_value)
    if set(raw) != {"schema_version", "cases"}:
        raise MarketingJudgmentCanaryError("judgment_canary_corpus_envelope_invalid")
    records = raw.get("cases")
    if raw.get("schema_version") != schema or not isinstance(records, list) or not records:
        raise MarketingJudgmentCanaryError("judgment_canary_corpus_envelope_invalid")
    typed_records = cast("list[object]", records)
    try:
        return tuple(model.model_validate(item) for item in typed_records)
    except ValueError as error:
        raise MarketingJudgmentCanaryError("judgment_canary_corpus_records_invalid") from error


def _require_unique_ids(
    items: tuple[MarketingJudgmentCanaryInput | MarketingJudgmentCanaryExpectation, ...],
    error_code: str,
) -> None:
    identifiers = tuple(item.case_id for item in items)
    if len(set(identifiers)) != len(identifiers):
        raise MarketingJudgmentCanaryError(error_code)
