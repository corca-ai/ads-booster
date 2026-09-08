from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.knowledge.repository_tool_state import RepositoryToolState
from ads_booster.knowledge.tool_contracts import SourceReadData, SourceReadInput, ToolResultStatus
from ads_booster.knowledge.tools import ToolHost

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject
    from tests.knowledge.test_curation_inputs import CurationInput

from tests.knowledge.test_curation_inputs import curation_input as fixture_curation_input

curation_input = fixture_curation_input


def test_source_read_accepts_json_segment_array(curation_input: CurationInput) -> None:
    # Given
    repository, processor, job, event, receipt = curation_input
    work = processor.build_curation_work(job)
    stored = RepositoryToolState(repository).read_source_extract(
        processor.actor, receipt.source_id, receipt.source_revision_id
    )
    assert stored is not None
    arguments = SourceReadInput(
        schema="knowledge.tool.source-read.v1",
        source_id=receipt.source_id,
        revision_id=receipt.source_revision_id,
        segment_ids=tuple(segment.segment_id for segment in stored.segments),
    ).model_dump(mode="json", by_alias=True)
    # When
    result = ToolHost(repository).execute("source_read", arguments, work.trusted_context)
    # Then
    assert result.status is ToolResultStatus.SUCCEEDED
    assert isinstance(result.data, SourceReadData)
    assert result.data.excerpts[0].text == event.text


@pytest.mark.parametrize(
    "extra", [{"actor_id": "forged"}, {"segment_ids": [123]}, {"segment_ids": "segment"}]
)
def test_source_read_rejects_invalid_json_fields(
    curation_input: CurationInput, extra: JsonObject
) -> None:
    # Given
    repository, processor, job, _, receipt = curation_input
    work = processor.build_curation_work(job)
    arguments: JsonObject = {
        "schema": "knowledge.tool.source-read.v1",
        "source_id": receipt.source_id,
        "segment_ids": [work.request.excerpts[0].segment_id],
        **extra,
    }
    # When
    result = ToolHost(repository).execute("source_read", arguments, work.trusted_context)
    # Then
    assert result.error_code == "tool_input_invalid"
