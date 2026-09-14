from __future__ import annotations

import io
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from ads_booster.agent.service.completion_evidence import BoundCompletionEvidence
from ads_booster.contracts.agent_run import ToolInvocation, ToolReceiptRecord, contract_sha256
from ads_booster.contracts.tool_capability import EffectClass
from ads_booster.tools import completion_proofs as proofs
from ads_booster.tools.descriptors import notion_daily_descriptor, slack_delivery_descriptor
from ads_booster.tools.github_issues import REPOSITORY, descriptor
from tests.marketing.agent_service.completion_fixtures import NOW
from tests.marketing.agent_service.creative_fixtures import setup_assets
from tests.marketing.agent_service.test_task_progress import make_run

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.agent_run import AgentRun
    from ads_booster.transport.json_types import JsonObject


def issue_evidence() -> BoundCompletionEvidence:
    tool = descriptor(now=NOW)
    arguments: JsonObject = {
        "repository": REPOSITORY,
        "title": "Fixture issue",
        "body": "Verified reproduction",
    }
    invocation = ToolInvocation(
        schema_version="trace.tool-invocation.v1",
        invocation_id="issue",
        run_id="run-one",
        step_id="step-one",
        intent_sha256="a" * 64,
        capability_snapshot_sha256="b" * 64,
        descriptor_sha256=contract_sha256(tool),
        idempotency_key="issue",
        input=arguments,
        input_sha256=contract_sha256(arguments),
    )
    output: JsonObject = {
        "number": 12,
        "repository": REPOSITORY,
        "url": f"https://github.com/{REPOSITORY}/issues/12",
    }
    receipt = ToolReceiptRecord(
        schema_version="trace.tool-receipt.v1",
        receipt_id="receipt",
        invocation_sha256=contract_sha256(invocation),
        disposition="succeeded",
        actual_cost_units=1,
        output_schema_sha256=contract_sha256(tool.output_schema),
        output_sha256=contract_sha256(output),
        executor_id="github.issues",
        occurred_at=NOW,
    )
    return BoundCompletionEvidence(invocation, tool, receipt, output, "e" * 64)


@dataclass(frozen=True, slots=True)
class ExactIssueVerifier:
    def verify(
        self, run: AgentRun, bound: BoundCompletionEvidence, owners: proofs.CompletionArtifactOwners
    ) -> bool:
        return run == make_run() and owners.image_root is None and bound.output.get("number") == 12


def test_host_registration_verifies_a_new_identity_without_builtin_branch() -> None:
    # Given host-owned registration for a separate issue executor.
    bound = issue_evidence()
    bound = replace(
        bound, descriptor=bound.descriptor.model_copy(update={"owner": "fixture.owner"})
    )
    identity = proofs.ProofIdentity(
        capability_id=bound.descriptor.capability_id,
        owner="fixture.owner",
        executor_id="github.issues",
        effect_class=EffectClass.EXTERNAL,
    )
    registry = proofs.CompletionProofRegistry(
        (proofs.ProofRegistration(identity, ExactIssueVerifier()),)
    )
    # When the matching host evidence is verified.
    verified = registry.verify(make_run(), bound, proofs.CompletionArtifactOwners())
    # Then the registered verifier, rather than a built-in capability branch, determines proof.
    assert verified


def test_duplicate_host_identity_is_rejected_at_registry_construction() -> None:
    # Given two registrations that could claim the same canonical identity.
    identity = proofs.ProofIdentity("issue", "owner", "executor", EffectClass.EXTERNAL)
    registration = proofs.ProofRegistration(identity, ExactIssueVerifier())
    # When constructing an ambiguous host registry.
    with pytest.raises(ValueError, match="duplicate_completion_proof_identity"):
        _ = proofs.CompletionProofRegistry((registration, registration))
    # Then no ambiguous registry becomes available.


@pytest.mark.parametrize("field", ["capability_id", "owner", "installation_id", "executor_id"])
def test_builtin_issue_proof_rejects_mismatched_host_identity(field: str) -> None:
    # Given otherwise valid GitHub evidence with one mismatched trusted identity field.
    bound = issue_evidence()
    bound = (
        replace(bound, receipt=bound.receipt.model_copy(update={field: "other"}))
        if field == "executor_id"
        else replace(bound, descriptor=bound.descriptor.model_copy(update={field: "other"}))
    )
    # When the default registered verifiers evaluate it.
    registry = proofs.configured_proof_registry()
    # Then output claims cannot choose or impersonate another registered owner.
    assert not registry.verify(make_run(), bound, proofs.CompletionArtifactOwners())


def test_empty_registry_does_not_fall_back_to_builtin_issue_verification() -> None:
    # Given an explicitly empty host registry and otherwise valid issue receipt.
    registry = proofs.CompletionProofRegistry(())
    # When checking its proof.
    verified = registry.verify(make_run(), issue_evidence(), proofs.CompletionArtifactOwners())
    # Then unsupported proof fails closed.
    assert not verified


@pytest.mark.parametrize("number", [12, 0])
def test_registered_issue_verifier_preserves_owner_output_validation(number: int) -> None:
    # Given an exact registered identity with a valid or invalid issue number.
    bound = issue_evidence()
    bound = replace(bound, output={**bound.output, "number": number})
    # When checking the existing owner's effect proof.
    verified = proofs.configured_proof_registry().verify(
        make_run(), bound, proofs.CompletionArtifactOwners()
    )
    # Then only a positive exact issue identity is accepted.
    assert verified is (number == 12)


@pytest.mark.parametrize("disposition", ["failed", "no_effect", "unknown"])
def test_registered_owner_cannot_certify_an_unsuccessful_receipt(disposition: str) -> None:
    # Given a registered owner whose receipt did not succeed.
    bound = issue_evidence()
    bound = replace(bound, receipt=bound.receipt.model_copy(update={"disposition": disposition}))
    # When the registry checks otherwise valid output.
    verified = proofs.configured_proof_registry().verify(
        make_run(), bound, proofs.CompletionArtifactOwners()
    )
    # Then preparation and uncertain execution are not effect proof.
    assert not verified


@pytest.mark.parametrize("capability", ["creative.image.edit", "creative.image.localize"])
@pytest.mark.parametrize("human_reported", [False, True])
def test_registered_managed_image_keeps_real_asset_owner_checks(
    tmp_path: Path, capability: str, human_reported: bool
) -> None:
    # Given real rooted PNG bytes and a current owner asset with explicit provenance.
    fixture, _ = setup_assets(tmp_path)
    original = fixture.assets.get(fixture.scope, "background", 1)
    assert original is not None
    stream = io.BytesIO()
    Image.new("RGB", (128, 128), "white").save(stream, format="PNG")
    data = stream.getvalue()
    _ = (fixture.assets.artifact_root / "result.png").write_bytes(data)
    asset = original.model_copy(
        update={
            "asset_id": "result",
            "relative_path": "result.png",
            "sha256": sha256(data).hexdigest(),
            "origin": "human_reported" if human_reported else "worker_receipt",
            "receipt_sha256": None if human_reported else "f" * 64,
        }
    )
    fixture.assets.add(asset, actor_scope=fixture.scope)
    bound = issue_evidence()
    bound = replace(
        bound,
        descriptor=bound.descriptor.model_copy(
            update={
                "capability_id": capability,
                "owner": "ads_booster.creative_image_edit",
                "effect_class": EffectClass.LOCAL_ARTIFACT,
            }
        ),
        receipt=bound.receipt.model_copy(update={"executor_id": "codex-image-edit"}),
        output={"asset": asset.model_dump(mode="json")},
    )
    owners = proofs.CompletionArtifactOwners(
        assets=fixture.assets, scope_for_run=lambda _: fixture.scope
    )
    # When the matching registered managed-image verifier evaluates the owner state.
    verified = proofs.configured_proof_registry().verify(make_run(), bound, owners)
    # Then edit and localization retain the worker-receipt requirement.
    assert verified is not human_reported


def _delivery_evidence(
    capability: str,
    *,
    target: str = "valid",
    approval_sha256: str | None = "c" * 64,
) -> tuple[BoundCompletionEvidence, proofs.CompletionArtifactOwners]:
    if capability == "deliver.slack":
        tool = slack_delivery_descriptor(
            installation_id="configured:slack", observed_at=NOW, ready=True
        )
        arguments: JsonObject = {"text": "A sourced brief"}
        output: JsonObject = {
            "ok": True,
            "channel": "C123" if target == "valid" else "C999",
            "ts": "1.2",
            "message": {
                "channel": "C123" if target == "valid" else "C999",
                "ts": "1.2",
                "text": "A sourced brief",
            },
        }
        owners = proofs.CompletionArtifactOwners(slack_channel_id="C123")
        executor_id = "slack.chat_post_message"
    else:
        tool = notion_daily_descriptor(
            installation_id="configured:notion", observed_at=NOW, ready=True
        )
        arguments = {"title": "2026-09-03", "content": "A sourced brief"}
        output = {
            "object": "page",
            "id": "notion-page",
            "url": "https://www.notion.so/notion-page",
            "parent": {
                "type": "page_id",
                "page_id": "parent-page" if target == "valid" else "other-page",
            },
            "properties": {"title": {"title": [{"plain_text": "2026-09-03"}]}},
        }
        owners = proofs.CompletionArtifactOwners(notion_parent_page_id="parent-page")
        executor_id = "notion.pages_create"
    invocation = ToolInvocation(
        schema_version="trace.tool-invocation.v1",
        invocation_id=f"{capability}-invocation",
        run_id="task-run",
        step_id="task-step",
        intent_sha256="a" * 64,
        capability_snapshot_sha256="b" * 64,
        descriptor_sha256=contract_sha256(tool),
        idempotency_key=f"{capability}-idempotency",
        input=arguments,
        input_sha256=contract_sha256(arguments),
    )
    receipt = ToolReceiptRecord(
        schema_version="trace.tool-receipt.v1",
        receipt_id=f"{capability}-receipt",
        invocation_sha256=contract_sha256(invocation),
        approval_sha256=approval_sha256,
        disposition="succeeded",
        actual_cost_units=1,
        output_schema_sha256=contract_sha256(tool.output_schema),
        output_sha256=contract_sha256(output),
        executor_id=executor_id,
        occurred_at=NOW,
    )
    return BoundCompletionEvidence(invocation, tool, receipt, output, "e" * 64), owners


@pytest.mark.parametrize("capability", ["deliver.slack", "store.notion.daily"])
def test_configured_delivery_proof_requires_owner_readback_and_approval(
    capability: str,
) -> None:
    bound, owners = _delivery_evidence(capability)

    assert proofs.configured_proof_registry().verify(make_run(), bound, owners)

    unapproved, _ = _delivery_evidence(capability, approval_sha256=None)
    assert not proofs.configured_proof_registry().verify(make_run(), unapproved, owners)


@pytest.mark.parametrize("capability", ["deliver.slack", "store.notion.daily"])
def test_configured_delivery_proof_rejects_wrong_readback_target(capability: str) -> None:
    bound, owners = _delivery_evidence(capability, target="wrong")

    assert not proofs.configured_proof_registry().verify(make_run(), bound, owners)
