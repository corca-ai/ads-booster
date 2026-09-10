# pyright: reportPrivateUsage=false
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import pytest

from ads_booster.agent.service.application import _AdapterBackend, _runtime_bound
from ads_booster.agent.service.knowledge import KnowledgeToolAdapter, knowledge_descriptors
from ads_booster.contracts.agent_run import ToolInvocation, contract_sha256
from ads_booster.knowledge.tool_contracts import KnowledgeToolName, TrustedInvocationContext
from ads_booster.knowledge.tools import ToolHost
from tests.knowledge.change_test_fixtures import NOW
from tests.knowledge.test_curation_inputs import curation_input as fixture_curation_input

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject
    from tests.knowledge.test_curation_inputs import CurationInput

curation_input = fixture_curation_input
EMPTY_ARGUMENTS: Final[JsonObject] = {}


@dataclass(frozen=True, slots=True)
class Resolver:
    context: TrustedInvocationContext

    def resolve_invocation(self, run_id: str, invocation_id: str) -> TrustedInvocationContext:
        _ = run_id, invocation_id
        return self.context


@pytest.mark.parametrize(
    ("name", "arguments", "expected_status"),
    [(name, EMPTY_ARGUMENTS, "rejected") for name in KnowledgeToolName]
    + [
        (
            KnowledgeToolName.KNOWLEDGE_SEARCH,
            {"schema": "knowledge.tool.search.v1", "query": "unknown campaign"},
            "no_results",
        )
    ],
)
def test_knowledge_results_pass_registered_backend_receipt_validation(
    curation_input: CurationInput,
    name: KnowledgeToolName,
    arguments: JsonObject,
    expected_status: str,
) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    host = ToolHost(repository)
    context = processor.build_curation_work(job).trusted_context
    descriptor = next(
        item
        for item in knowledge_descriptors(host.catalog(), host.schemas(), now=NOW)
        if item.capability_id == name.value
    )
    invocation = ToolInvocation(
        schema_version="trace.tool-invocation.v1",
        invocation_id="invocation.receipt",
        run_id="run.receipt",
        step_id="step.receipt",
        intent_sha256="0" * 64,
        capability_snapshot_sha256="0" * 64,
        descriptor_sha256=contract_sha256(descriptor),
        idempotency_key="receipt-test",
        input=arguments,
        input_sha256=contract_sha256(arguments),
    )
    backend = _AdapterBackend(
        KnowledgeToolAdapter(name, host, Resolver(context)), invocation, descriptor, None
    )
    # When
    receipt = backend.execute(_runtime_bound(invocation, descriptor))
    # Then
    assert receipt.call_id == invocation.invocation_id
    assert backend.result is not None
    assert backend.result.output["status"] == expected_status
