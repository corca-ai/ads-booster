from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from ads_booster.contracts.knowledge_preparation import (
    BrandUnresolvedPreparation,
    KnowledgePreparationDegraded,
    KnowledgePreparationReady,
    RequiredContextPreparationError,
    parse_knowledge_preparation,
)
from ads_booster.contracts.knowledge_selection import KnowledgeActionKind
from ads_booster.transport.json_types import JsonObject  # noqa: TC001


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        (
            {
                "schema": "knowledge.preparation.v1",
                "status": "ready",
                "task_ref": "task.1",
                "action_kind": "content_write",
                "brand_ref": "brand.a",
                "context_request_id": "request.1",
                "context_receipt_id": "receipt.1",
                "context_receipt_sha256": "a" * 64,
            },
            KnowledgePreparationReady,
        ),
        (
            {
                "schema": "knowledge.preparation.v1",
                "status": "degraded",
                "task_ref": "task.1",
                "action_kind": "research",
                "brand_ref": None,
                "reason": "index_pending",
                "context_receipt_id": "receipt.1",
                "context_receipt_sha256": "a" * 64,
            },
            KnowledgePreparationDegraded,
        ),
        (
            {
                "schema": "knowledge.preparation.v1",
                "status": "required_context_error",
                "task_ref": "task.1",
                "action_kind": "content_write",
                "brand_ref": "brand.a",
                "error_code": "required_context_unavailable",
            },
            RequiredContextPreparationError,
        ),
        (
            {
                "schema": "knowledge.preparation.v1",
                "status": "brand_unresolved",
                "task_ref": "task.1",
                "action_kind": "content_write",
                "candidate_brand_refs": ["brand.a", "brand.b"],
            },
            BrandUnresolvedPreparation,
        ),
    ],
)
def test_preparation_variants_parse_as_closed_typed_results(
    payload: JsonObject,
    expected_type: type[
        KnowledgePreparationReady
        | KnowledgePreparationDegraded
        | RequiredContextPreparationError
        | BrandUnresolvedPreparation
    ],
) -> None:
    # Given / When
    parsed = parse_knowledge_preparation(payload)

    # Then
    assert type(parsed) is expected_type


def test_unknown_preparation_status_is_rejected() -> None:
    # Given
    payload: JsonObject = {
        "schema": "knowledge.preparation.v1",
        "status": "silently_continue",
        "task_ref": "task.1",
        "action_kind": "content_write",
    }

    # When / Then
    with pytest.raises(ValidationError) as caught:
        _ = parse_knowledge_preparation(payload)
    assert "union_tag_invalid" in {error["type"] for error in caught.value.errors()}


def test_brand_unresolved_requires_candidates_and_write_action() -> None:
    # Given
    adapter = TypeAdapter(BrandUnresolvedPreparation)

    # When / Then
    with pytest.raises(ValidationError) as caught:
        _ = adapter.validate_python(
            {
                "schema": "knowledge.preparation.v1",
                "status": "brand_unresolved",
                "task_ref": "task.1",
                "action_kind": "research",
                "candidate_brand_refs": ["brand.a"],
            }
        )
    assert {error["type"] for error in caught.value.errors()} == {"brand_unresolved_action_invalid"}


def test_brand_unresolved_requires_at_least_one_candidate() -> None:
    # Given / When / Then
    with pytest.raises(ValidationError) as caught:
        _ = BrandUnresolvedPreparation(
            schema="knowledge.preparation.v1",
            status="brand_unresolved",
            task_ref="task.1",
            action_kind=KnowledgeActionKind.CONTENT_WRITE,
            candidate_brand_refs=(),
        )
    assert {error["type"] for error in caught.value.errors()} == {"too_short"}
