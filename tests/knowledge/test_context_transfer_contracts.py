from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel, ValidationError

from ads_booster.contracts.knowledge_context import (
    EditorialContextBlock,
    EditorialContextRole,
    EvidenceExcerpt,
    KnowledgeContextTransfer,
    knowledge_context_sha256,
)
from ads_booster.contracts.knowledge_context_validation import (
    KnowledgeContextAbsent,
    KnowledgeContextContractError,
    KnowledgeContextErrorCode,
    TrustedKnowledgeContextBinding,
    TrustedKnowledgeDisabled,
    TrustedKnowledgeRequired,
    parse_knowledge_context_fields,
)
from ads_booster.contracts.knowledge_selection import (
    KnowledgeActionKind,
)
from ads_booster.transport.json_types import JsonObject  # noqa: TC001
from tests.knowledge.transfer_contract_fixtures import (
    DIGEST_B,
    NOW,
    binding_fixture,
    transfer_fixture,
)

if TYPE_CHECKING:
    from collections.abc import Callable

FIXTURE_ROOT = Path(__file__).parent / "fixtures"


class _MissingContextFixture(BaseModel):
    context_envelope: JsonObject | None
    context_receipt: str | None


def _assert_code[T](expected: KnowledgeContextErrorCode, action: Callable[[], T]) -> None:
    with pytest.raises(KnowledgeContextContractError) as caught:
        _ = action()
    assert caught.value.code is expected


def test_transfer_round_trips_nested_request_and_selection_receipt() -> None:
    # Given
    transfer = transfer_fixture()
    serialized = transfer.model_dump_json()

    # When
    restored = KnowledgeContextTransfer.model_validate_json(serialized)

    # Then
    assert restored == transfer
    assert restored.receipt.selected_source_revisions[0].revision_id == "source.launch.rev2"
    assert '"schema":"trace.knowledge-context.v1"' in serialized


@pytest.mark.parametrize(
    ("binding", "code"),
    [
        (
            binding_fixture(workspace_id="workspace.team-b"),
            KnowledgeContextErrorCode.WORKSPACE_MISMATCH,
        ),
        (binding_fixture(account_id="account.b"), KnowledgeContextErrorCode.ACCOUNT_MISMATCH),
        (binding_fixture(scoped_actor_ref="member.a2"), KnowledgeContextErrorCode.ACTOR_MISMATCH),
        (binding_fixture(brand_ref="brand.b"), KnowledgeContextErrorCode.BRAND_MISMATCH),
        (
            binding_fixture(action_kind=KnowledgeActionKind.RESEARCH),
            KnowledgeContextErrorCode.ACTION_MISMATCH,
        ),
        (binding_fixture(run_ref="run.2"), KnowledgeContextErrorCode.RUN_MISMATCH),
        (binding_fixture(task_ref="task.2"), KnowledgeContextErrorCode.TASK_MISMATCH),
        (
            binding_fixture(invocation_ref="invocation.2"),
            KnowledgeContextErrorCode.INVOCATION_MISMATCH,
        ),
    ],
)
def test_trusted_outer_binding_rejects_declared_identity_substitution(
    binding: TrustedKnowledgeContextBinding,
    code: KnowledgeContextErrorCode,
) -> None:
    # Given
    transfer = transfer_fixture()
    payload = transfer.model_dump(mode="json")
    digest = knowledge_context_sha256(transfer)
    required = TrustedKnowledgeRequired(binding=binding)

    # When / Then
    _assert_code(
        code,
        lambda: parse_knowledge_context_fields(payload, digest, required, now=NOW),
    )


def test_digest_substitution_is_rejected() -> None:
    # Given
    transfer = transfer_fixture()

    # When / Then
    _assert_code(
        KnowledgeContextErrorCode.DIGEST_MISMATCH,
        lambda: parse_knowledge_context_fields(
            transfer.model_dump(mode="json"),
            DIGEST_B,
            TrustedKnowledgeRequired(binding_fixture()),
            now=NOW,
        ),
    )


@pytest.mark.parametrize("present_envelope", [True, False])
def test_partial_context_field_pair_is_always_rejected(present_envelope: bool) -> None:
    # Given
    transfer = transfer_fixture()
    envelope = transfer.model_dump(mode="json") if present_envelope else None
    digest = None if present_envelope else knowledge_context_sha256(transfer)

    # When / Then
    _assert_code(
        KnowledgeContextErrorCode.FIELD_PAIR_INCOMPLETE,
        lambda: parse_knowledge_context_fields(
            envelope, digest, TrustedKnowledgeRequired(binding_fixture()), now=NOW
        ),
    )


def test_frozen_missing_both_cases_use_only_trusted_required_or_disabled_state() -> None:
    # Given
    required = _MissingContextFixture.model_validate_json(
        (FIXTURE_ROOT / "context-required-missing-both.json").read_text()
    )
    disabled = _MissingContextFixture.model_validate_json(
        (FIXTURE_ROOT / "context-legacy-missing-both.json").read_text()
    )

    # When / Then
    _assert_code(
        KnowledgeContextErrorCode.REQUIRED_CONTEXT_MISSING,
        lambda: parse_knowledge_context_fields(
            required.context_envelope,
            required.context_receipt,
            TrustedKnowledgeRequired(binding_fixture()),
            now=NOW,
        ),
    )
    parsed = parse_knowledge_context_fields(
        disabled.context_envelope,
        disabled.context_receipt,
        TrustedKnowledgeDisabled(),
        now=NOW,
    )
    assert parsed == KnowledgeContextAbsent()


def test_naive_or_expired_transfer_is_rejected_at_its_boundary() -> None:
    # Given
    payload = transfer_fixture().model_dump(mode="json")
    payload["expires_at"] = "2026-09-07T10:15:00"

    # When / Then
    with pytest.raises(ValidationError) as caught:
        _ = KnowledgeContextTransfer.model_validate(payload)
    assert {error["type"] for error in caught.value.errors()} == {"knowledge_timestamp_not_utc"}

    transfer = transfer_fixture()
    _assert_code(
        KnowledgeContextErrorCode.EXPIRED,
        lambda: parse_knowledge_context_fields(
            transfer.model_dump(mode="json"),
            knowledge_context_sha256(transfer),
            TrustedKnowledgeRequired(binding_fixture()),
            now=transfer.expires_at,
        ),
    )


def test_editorial_text_and_evidence_excerpt_are_bounded() -> None:
    # Given / When / Then
    with pytest.raises(ValidationError) as editorial_error:
        _ = EditorialContextBlock(
            block_id="block.1",
            role=EditorialContextRole.REFERENCE,
            text="x" * 4_001,
            revision_refs=("source.1.rev1",),
        )
    assert {error["type"] for error in editorial_error.value.errors()} == {"string_too_long"}

    with pytest.raises(ValidationError) as excerpt_error:
        _ = EvidenceExcerpt(
            source_id="source.1",
            revision_id="source.1.rev1",
            segment_id="segment.1",
            text="x" * 1_001,
        )
    assert {error["type"] for error in excerpt_error.value.errors()} == {"string_too_long"}
