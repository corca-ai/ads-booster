from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.threads import ThreadsPublicationReceipt
from ads_booster.threads.publications import publication_operation_id

_ITEM_IDS: TypeAdapter[list[str]] = TypeAdapter(list[str])

if TYPE_CHECKING:
    from ads_booster.agent.service.completion_evidence import BoundCompletionEvidence
    from ads_booster.contracts.agent_run import AgentRun
    from ads_booster.tools.completion_registry import CompletionArtifactOwners


@dataclass(frozen=True, slots=True)
class ThreadsPublicationProof:
    def verify(
        self, run: AgentRun, bound: BoundCompletionEvidence, owners: CompletionArtifactOwners
    ) -> bool:
        repository = owners.threads_publications
        if repository is None or run.tenant_id != bound.invocation.tenant_id:
            return False
        payloads = bound.output.get("publications")
        requested = bound.output.get("requested_item_ids")
        unattempted = bound.output.get("unattempted_item_ids")
        if not isinstance(payloads, list) or not isinstance(requested, list):
            return False
        if unattempted != []:
            return False
        requested_ids = _ITEM_IDS.validate_python(requested)
        receipts = tuple(ThreadsPublicationReceipt.model_validate(item) for item in payloads)
        invocation_sha256 = contract_sha256(bound.invocation)
        return (
            len(receipts) == len(requested_ids)
            and {receipt.item_id for receipt in receipts} == set(requested_ids)
            and all(
                repository.get(receipt.operation_id) == receipt
                and receipt.operation_id
                == publication_operation_id(invocation_sha256, receipt.item_id)
                and receipt.workspace_id == run.tenant_id
                and receipt.run_id == run.run_id
                and receipt.invocation_sha256 == invocation_sha256
                and receipt.state == "published"
                and receipt.published_post_id is not None
                and receipt.permalink is not None
                for receipt in receipts
            )
        )


__all__ = ["ThreadsPublicationProof"]
