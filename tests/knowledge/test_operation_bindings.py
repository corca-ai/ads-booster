from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.knowledge.operation_contracts import KnowledgeOperation
from ads_booster.knowledge.operation_enums import KnowledgeOperationKind
from ads_booster.knowledge.tool_contracts import (
    KnowledgeApplyInput,
    KnowledgeToolName,
    PageRevisionPayload,
    ToolResultStatus,
)
from ads_booster.knowledge.tools import ToolHost
from tests.knowledge.change_test_fixtures import page_snapshot
from tests.knowledge.test_curation_inputs import curation_input as fixture_curation_input

if TYPE_CHECKING:
    from tests.knowledge.test_curation_inputs import CurationInput

curation_input = fixture_curation_input


@pytest.mark.parametrize("malformed", ["extra_revision", "missing_revision", "duplicate_target"])
def test_tool_host_rejects_invalid_page_revision_mapping(
    curation_input: CurationInput, malformed: str
) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    page, revision, body = page_snapshot(claims=())
    operation = KnowledgeOperation(
        operation_id="operation.mapping",
        kind=KnowledgeOperationKind.PAGE_CREATE,
        target_page_ids=(page.page_id,),
        expected_revision_ids=("none",),
        reason="Create a page.",
    )
    targets = (page.page_id,) if malformed == "extra_revision" else (page.page_id, page.page_id)
    revisions = ("none",) if malformed == "missing_revision" else ("none", "none")
    payload = KnowledgeApplyInput(
        schema="knowledge.tool.apply.v1",
        operation_id=operation.operation_id,
        page_operation=operation,
        pages=(
            PageRevisionPayload(
                page=page,
                revision=revision.model_copy(update={"previous_revision_id": None}),
                body=body.decode(),
            ),
        ),
    )
    payload = payload.model_copy(
        update={
            "page_operation": operation.model_copy(
                update={"target_page_ids": targets, "expected_revision_ids": revisions}
            )
        }
    )
    # When
    result = ToolHost(repository).execute(
        KnowledgeToolName.KNOWLEDGE_APPLY.value,
        payload.model_dump(mode="json", by_alias=True),
        processor.build_curation_work(job).trusted_context,
    )
    # Then
    assert result.status is ToolResultStatus.REJECTED
    assert result.error_code == "tool_input_invalid"
    assert repository.read_page(processor.actor, page.page_id) is None
