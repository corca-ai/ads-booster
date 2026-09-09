from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo
from hashlib import sha256
from typing import TYPE_CHECKING, override

import pytest

from ads_booster.contracts.knowledge_context import (
    ContextTransferValidationAccepted,
    ContextTransferValidationRejected,
    ContextTransferValidationRequest,
    ValidationRejectionCode,
    ValidationStage,
    knowledge_context_sha256,
)
from ads_booster.contracts.knowledge_context_validation import TrustedKnowledgeContextBinding
from ads_booster.contracts.knowledge_selection import (
    KnowledgeActionKind,
    SelectedSourceRevision,
    VoiceStatus,
)
from ads_booster.knowledge.contracts import SourceDisposition, TaskBinding, TaskBindingState
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.repository import MembershipRole
from ads_booster.knowledge.repository_types import IndexOutboxItem, SourceAdmissionChange
from ads_booster.channels.http.knowledge_ingress_api import (
    ApiIngressRequest,
    build_api_ingress,
)
from ads_booster.agent.service.knowledge_transfer import TransferContextMaterial
from ads_booster.channels.http.oauth import OAuthIdentity
from tests.knowledge.test_transfer_material import transfer_adapter
from tests.knowledge.transfer_contract_fixtures import transfer_fixture

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.agent.service.knowledge import KnowledgeServiceAdapter


def accepted_transfer(
    root: Path, stage: ValidationStage
) -> tuple[
    KnowledgeServiceAdapter,
    ContextTransferValidationRequest,
    ContextTransferValidationAccepted,
    str,
]:
    adapter = transfer_adapter(root)
    now = datetime.now(UTC)
    ingress = build_api_ingress(
        ApiIngressRequest(
            request_id="ingress.1",
            run_id="run.1",
            action="create",
            text="Launch is approved.",
            identity=OAuthIdentity(tenant_id="trace", principal_id="member.1"),
            revision=1,
            occurred_at=now,
        )
    )
    actor = ingress.binding.actor
    adapter.repository.register_actor(actor, MembershipRole.EDITOR)
    assert adapter.ingress.admit_standalone(ingress)
    delivery = KnowledgeIngestion(adapter.repository).ingest(actor, ingress.event, ingress.envelope)
    source_receipt = delivery.unit_receipts[0].receipt
    stored = adapter.repository.read_source(actor, source_receipt.source_id)
    assert stored is not None
    _ = adapter.repository.change_source_admission(
        SourceAdmissionChange(
            operation_id="admit.1",
            payload_sha256=sha256(b"admit.1").hexdigest(),
            workspace_id=actor.workspace_id,
            source_id=stored.source.source_id,
            expected_admission_revision=0,
            disposition=SourceDisposition.REFERENCE,
            index_item=IndexOutboxItem(
                item_id="index.reference",
                workspace_id=actor.workspace_id,
                entity_kind="source",
                entity_id=stored.source.source_id,
                revision_id=stored.source.revision_id,
                extraction_version=stored.source.extractor_version,
                admission_revision=1,
            ),
            occurred_at=now,
        )
    )
    _ = adapter.host.open_task(
        actor,
        TaskBinding(
            task_id="task.1",
            workspace_id=actor.workspace_id,
            actor_ref=actor.actor_id,
            member_id=actor.member_id,
            session_id=actor.session_id,
            action_kind=KnowledgeActionKind.TEAM_CHAT,
            capability_epoch=actor.policy_epoch,
            state=TaskBindingState.ACTIVE,
            opened_at=now,
        ),
    )
    fixture = transfer_fixture()
    request = fixture.request.model_copy(
        update={
            "action_kind": KnowledgeActionKind.TEAM_CHAT,
            "brand_ref": None,
        }
    )
    receipt = fixture.receipt.model_copy(
        update={
            "team_id": actor.workspace_id,
            "scoped_actor_ref": actor.actor_id,
            "action_kind": request.action_kind,
            "resolved_brand_ref": None,
            "voice_status": VoiceStatus.VOICE_UNCONFIGURED,
            "soul_revision_id": None,
            "selected_source_revisions": (
                SelectedSourceRevision(
                    source_id=stored.source.source_id,
                    revision_id=stored.source.revision_id,
                    segment_ids=tuple(segment.segment_id for segment in stored.segments),
                    content_sha256=sha256(stored.body).hexdigest(),
                ),
            ),
        }
    )
    transfer = adapter.create_transfer(
        binding=TrustedKnowledgeContextBinding(
            workspace_id=actor.workspace_id,
            account_id="account.1",
            scoped_actor_ref=actor.actor_id,
            brand_ref=None,
            action_kind=request.action_kind,
            run_ref="run.1",
            task_ref="task.1",
            invocation_ref="invocation.1",
        ),
        material=TransferContextMaterial(request=request, receipt=receipt),
        external_sharing_authority_ref=receipt.policy_version,
    )
    validation = ContextTransferValidationRequest(
        schema="trace.knowledge-context-validation-request.v1",
        request_id="validation.1",
        principal_id="worker.1",
        stage=stage,
        transfer_id=transfer.transfer_id,
        workspace_id=actor.workspace_id,
        account_id="account.1",
        knowledge_context_sha256=knowledge_context_sha256(transfer),
    )
    result = adapter.validate_transfer(
        validation, authenticated_tenant_id="trace", authenticated_principal_id="worker.1"
    )
    assert isinstance(result, ContextTransferValidationAccepted)
    return adapter, validation, result, stored.source.source_id


@pytest.mark.parametrize("stage", list(ValidationStage))
@pytest.mark.parametrize(
    "change", ["blocked", "source_retracted", "binding_changed", "principal_changed", "expired"]
)
def test_cached_acceptance_does_not_bypass_current_fence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: ValidationStage,
    change: str,
) -> None:
    # Given: a real SQLite transfer has an accepted immutable validation receipt.
    adapter, request, accepted, source_id = accepted_transfer(tmp_path, stage)
    with adapter.repository.connection() as db:
        if change == "blocked":
            _ = db.execute(
                "UPDATE context_transfers SET state='blocked' WHERE transfer_id=?",
                (request.transfer_id,),
            )
        elif change == "source_retracted":
            _ = db.execute(
                "UPDATE sources SET visibility='blocked' WHERE source_id=?", (source_id,)
            )
    if change == "binding_changed":
        request = request.model_copy(update={"account_id": "account.forged"})
    if change == "principal_changed":
        request = request.model_copy(update={"principal_id": "worker.other"})
    if change == "expired":

        class ExpiredClock(datetime):
            @classmethod
            @override
            def now(cls, tz: tzinfo | None = None) -> datetime:
                _ = tz
                return accepted.valid_until + timedelta(seconds=1)

        monkeypatch.setattr("ads_booster.agent.service.knowledge.datetime", ExpiredClock)
    # When: the worker retries the same validation request ID after state changed.
    result = adapter.validate_transfer(
        request, authenticated_tenant_id="trace", authenticated_principal_id=request.principal_id
    )
    # Then: the current fence rejects while historical acceptance stays unchanged.
    assert isinstance(result, ContextTransferValidationRejected)
    assert (
        result.rejection_code
        is {
            "blocked": ValidationRejectionCode.TRANSFER_BLOCKED,
            "source_retracted": ValidationRejectionCode.DEPENDENCY_CHANGED,
            "binding_changed": ValidationRejectionCode.TRANSFER_BLOCKED,
            "principal_changed": ValidationRejectionCode.TRANSFER_BLOCKED,
            "expired": ValidationRejectionCode.EXPIRED,
        }[change]
    )
    assert (
        adapter.repository.transfer_validation(request.transfer_id, stage.value, request.request_id)
        == accepted
    )


def test_cached_acceptance_expiry_cannot_outlive_its_own_validity(tmp_path: Path) -> None:
    # Given: a still-live transfer with a historical validation's narrower validity window.
    adapter, request, accepted, _ = accepted_transfer(tmp_path, ValidationStage.PRE_DISPATCH)
    expired = accepted.model_copy(
        update={
            "checked_at": accepted.checked_at - timedelta(minutes=2),
            "valid_until": accepted.checked_at - timedelta(minutes=1),
        }
    )
    with adapter.repository.connection() as db:
        _ = db.execute(
            """UPDATE transfer_validations SET checked_at=?,valid_until=?,validation_json=?
            WHERE request_id=?""",
            (
                expired.checked_at.isoformat(),
                expired.valid_until.isoformat(),
                expired.model_dump_json(),
                request.request_id,
            ),
        )
    # When: the same cache key is checked while the transfer itself is still unexpired.
    result = adapter.validate_transfer(
        request, authenticated_tenant_id="trace", authenticated_principal_id="worker.1"
    )
    # Then: its own expired authorization cannot be reused, and the audit row is preserved.
    assert isinstance(result, ContextTransferValidationRejected)
    assert result.rejection_code is ValidationRejectionCode.EXPIRED
    assert (
        adapter.repository.transfer_validation(
            request.transfer_id, request.stage.value, request.request_id
        )
        == expired
    )
