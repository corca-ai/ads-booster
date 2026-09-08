"""Small creative jobs and their evidence, independent of campaign planning."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ads_booster.contracts.models import ContractModel, Identifier, Sha256Digest

Text = Annotated[str, Field(min_length=1, max_length=2000)]


class CreativeScope(ContractModel):
    workspace_id: Identifier
    product_id: Identifier
    campaign_id: Identifier | None = None
    member_id: Identifier | None = None
    session_id: Identifier | None = None

    @model_validator(mode="after")
    def private_scope(self) -> Self:
        if (self.member_id is None) != (self.session_id is None):
            msg = "creative_private_scope_incomplete"
            raise ValueError(msg)
        return self

    def can_read(self, target: CreativeScope) -> bool:
        return (
            self.workspace_id == target.workspace_id
            and self.product_id == target.product_id
            and self.campaign_id == target.campaign_id
            and (target.member_id is None or self == target)
        )


class AssetParent(ContractModel):
    asset_id: Identifier
    revision: Annotated[int, Field(ge=1)]
    sha256: Sha256Digest


class CreativeQA(ContractModel):
    locale: Annotated[str, Field(min_length=2, max_length=35)] | None = None
    method: Literal["deterministic", "model_visual", "human_review"]
    status: Literal["pending", "passed", "failed"]
    checks: Annotated[tuple[Text, ...], Field(max_length=32)] = ()
    evidence: Text
    reviewer: Identifier


class CreativeAsset(ContractModel):
    schema_version: Literal["trace.creative-asset.v1"] = "trace.creative-asset.v1"
    asset_id: Identifier
    revision: Annotated[int, Field(ge=1)]
    scope: CreativeScope
    kind: Literal["native_trace_capture", "edited_promotion", "background_asset", "phone_mockup"]
    relative_path: Text
    sha256: Sha256Digest
    parents: Annotated[tuple[AssetParent, ...], Field(max_length=16)] = ()
    source: Text
    use_terms: Text
    data_permission: Literal["synthetic", "explicitly_permitted"]
    permission_evidence: Text
    origin: Literal["human_reported", "worker_receipt"]
    receipt_sha256: Sha256Digest | None = None
    preserve: Annotated[tuple[Text, ...], Field(max_length=32)] = ()
    change: Annotated[tuple[Text, ...], Field(max_length=32)] = ()
    locale: Annotated[str, Field(min_length=2, max_length=35)] | None = None
    qa: Annotated[tuple[CreativeQA, ...], Field(max_length=32)] = ()

    @model_validator(mode="after")
    def evidence_boundaries(self) -> Self:
        if self.origin == "worker_receipt" and self.receipt_sha256 is None:
            msg = "creative_worker_receipt_required"
            raise ValueError(msg)
        if self.origin == "human_reported" and self.receipt_sha256 is not None:
            msg = "creative_human_report_is_not_worker_receipt"
            raise ValueError(msg)
        if self.origin == "human_reported" and any(q.method != "human_review" for q in self.qa):
            msg = "creative_human_report_is_not_system_verification"
            raise ValueError(msg)
        if any(q.locale != self.locale for q in self.qa):
            msg = "creative_qa_locale_mismatch"
            raise ValueError(msg)
        if len({(p.asset_id, p.revision) for p in self.parents}) != len(self.parents):
            msg = "creative_duplicate_parent"
            raise ValueError(msg)
        return self

    @property
    def product_proof_verified(self) -> bool:
        """File hashes and claimed worker receipts do not attest product functionality."""
        return False
