from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.knowledge_context_validation import (
    KnowledgeContextContractError,
    KnowledgeContextErrorCode,
)
from ads_booster.knowledge.change_validation import ChangeValidationError
from ads_booster.knowledge.errors import (
    AccessDeniedError,
    AuthorityViolationError,
    EvidenceResolutionError,
    KnowledgePolicyError,
    PolicyEpochStaleError,
    ScopeIntersectionError,
)
from ads_booster.knowledge.file_paths import KnowledgeFileStoreError
from ads_booster.knowledge.ingest_receipts import IngestDeliveryReceiptValidationError
from ads_booster.knowledge.messages import MessageValidationError
from ads_booster.knowledge.migrations import KnowledgeSchemaError
from ads_booster.knowledge.web_search import ProviderSearchError

if TYPE_CHECKING:
    from collections.abc import Generator


@contextmanager
def _yielding_context() -> Generator[None]:
    yield


type RuntimeTypedError = (
    KnowledgePolicyError
    | AccessDeniedError
    | PolicyEpochStaleError
    | ScopeIntersectionError
    | AuthorityViolationError
    | EvidenceResolutionError
    | KnowledgeFileStoreError
    | KnowledgeSchemaError
    | ChangeValidationError
    | MessageValidationError
    | ProviderSearchError
    | IngestDeliveryReceiptValidationError
    | KnowledgeContextContractError
)


_ERRORS: tuple[tuple[str, RuntimeTypedError], ...] = (
    ("policy_base", KnowledgePolicyError(code="policy_error")),
    (
        "access_denied",
        AccessDeniedError(
            code="access_denied",
            actor_id="actor.a",
            target_workspace_id="workspace.b",
        ),
    ),
    (
        "policy_epoch_stale",
        PolicyEpochStaleError(
            code="policy_epoch_stale",
            actor_epoch=1,
            current_epoch=2,
        ),
    ),
    ("scope_intersection", ScopeIntersectionError(code="scope_empty", workspace_ids=())),
    ("authority_violation", AuthorityViolationError(code="authority_denied", target_id="page.a")),
    ("evidence_resolution", EvidenceResolutionError(evidence_id="evidence.a", revision_id="rev.a")),
    ("file_store", KnowledgeFileStoreError(code="file_missing", target="page.a")),
    ("schema", KnowledgeSchemaError(code="schema_invalid", detail="schema mismatch")),
    ("change_validation", ChangeValidationError(code="change_invalid", target_id="page.a")),
    ("message_validation", MessageValidationError(code="message_invalid")),
    ("provider_search", ProviderSearchError(code="provider_failed", retryable=True)),
    ("ingest_delivery", IngestDeliveryReceiptValidationError(code="delivery_invalid")),
    (
        "context_contract",
        KnowledgeContextContractError(KnowledgeContextErrorCode.REQUIRED_CONTEXT_MISSING),
    ),
)


@pytest.mark.parametrize(("_name", "error"), _ERRORS, ids=[name for name, _ in _ERRORS])
def test_typed_error_survives_contextmanager_traceback_assignment(
    _name: str,
    error: RuntimeTypedError,
) -> None:
    expected_code = error.code
    expected_repr = repr(error)
    expected_text = str(error)
    cause = RuntimeError("causal probe")

    with pytest.raises(type(error)) as raised, _yielding_context():
        raise error from cause

    assert raised.value is error
    assert error.code == expected_code
    assert repr(error) == expected_repr
    assert str(error) == expected_text
    assert error.__cause__ is cause
    assert error.__traceback__ is not None
