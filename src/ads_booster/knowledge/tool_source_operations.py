from __future__ import annotations

# ruff: noqa: EM101
from hashlib import sha256
from typing import TYPE_CHECKING, assert_never

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.contract_types import SourceDisposition
from ads_booster.knowledge.source_contracts import (
    AttachmentCapability,
    IngestEnvelope,
    SourceSegment,
)
from ads_booster.knowledge.tool_contracts import (
    SourceDiscoveryCandidateData,
    SourceExcerpt,
    SourceFetchData,
    SourceFetchInput,
    SourceReadData,
    SourceReadInput,
    SourceSearchData,
    SourceSearchInput,
    ToolResult,
    ToolResultStatus,
    TrustedInvocationContext,
)
from ads_booster.knowledge.tool_support import KnowledgeToolError, error_result, success
from ads_booster.knowledge.web_search import SourceSearchRequest, SourceSearchStatus

if TYPE_CHECKING:
    from ads_booster.knowledge.tool_dependencies import ToolDependencies


def source_read(
    dependencies: ToolDependencies,
    request: SourceReadInput,
    context: TrustedInvocationContext,
) -> ToolResult:
    stored = dependencies.state.read_source_extract(
        context.actor,
        request.source_id,
        request.revision_id,
    )
    if stored is None:
        return error_result(
            context.invocation_id,
            "source_not_found",
            status=ToolResultStatus.NOT_FOUND,
        )
    require_source_capability(
        request,
        stored.source.revision_id,
        stored.source.disposition,
        context,
    )
    body = stored.body.decode()
    segments = {item.segment_id: item for item in stored.segments}
    if request.segment_ids:
        if any(segment_id not in segments for segment_id in request.segment_ids):
            raise KnowledgeToolError("source_segment_not_found")
        excerpts = tuple(
            segment_excerpt(body, segments[segment_id]) for segment_id in request.segment_ids
        )
    else:
        text_range = request.text_range
        if text_range is None:
            raise KnowledgeToolError("source_read_selector_invalid")
        if text_range.end > len(body):
            raise KnowledgeToolError("source_range_out_of_bounds")
        excerpts = (
            SourceExcerpt(
                start=text_range.start,
                end=text_range.end,
                text=body[text_range.start : text_range.end],
            ),
        )
    return success(
        context.invocation_id,
        ToolResultStatus.SUCCEEDED,
        SourceReadData(
            source_id=stored.source.source_id,
            revision_id=stored.source.revision_id,
            disposition=stored.source.disposition,
            completeness=stored.source.completeness,
            excerpts=excerpts,
        ),
    )


def source_search(
    dependencies: ToolDependencies,
    request: SourceSearchInput,
    context: TrustedInvocationContext,
) -> ToolResult:
    search = dependencies.source_search
    if search is None or context.search_policy is None or context.search_budget is None:
        return error_result(
            context.invocation_id,
            "source_search_unavailable",
            status=ToolResultStatus.UNAVAILABLE,
        )
    result = search.search(
        SourceSearchRequest(
            query=request.query,
            locale=request.locale,
            domains=request.domains,
            limit=request.limit,
        ),
        policy=context.search_policy,
        budget=context.search_budget,
    )
    match result.status:
        case SourceSearchStatus.RESULTS:
            if result.discovery is None:
                raise KnowledgeToolError("source_search_result_invalid")
            discovery = result.discovery
            return success(
                context.invocation_id,
                ToolResultStatus.SUCCEEDED,
                SourceSearchData(
                    discovery_id=discovery.discovery_id,
                    provider=discovery.provider,
                    fetched_at=discovery.fetched_at,
                    candidates=tuple(
                        SourceDiscoveryCandidateData(
                            url=item.url,
                            title=item.title,
                            snippet=item.snippet,
                        )
                        for item in discovery.candidates
                    ),
                ),
            )
        case SourceSearchStatus.NO_RESULTS:
            return success(
                context.invocation_id,
                ToolResultStatus.NO_RESULTS,
                SourceSearchData(),
            )
        case SourceSearchStatus.SEARCH_UNAVAILABLE:
            return error_result(
                context.invocation_id,
                result.error_code or "source_search_unavailable",
                status=ToolResultStatus.UNAVAILABLE,
                retryable=result.retryable,
            )
        case SourceSearchStatus.BUDGET_EXHAUSTED:
            return error_result(
                context.invocation_id,
                result.error_code or "search_budget_exhausted",
                status=ToolResultStatus.REJECTED,
            )
    assert_never(result.status)


def source_fetch(
    dependencies: ToolDependencies,
    request: SourceFetchInput,
    context: TrustedInvocationContext,
) -> ToolResult:
    event = context.source_fetch_event
    if event is None:
        raise KnowledgeToolError("source_fetch_event_missing")
    if event.event_kind.value != "attachment_received":
        raise KnowledgeToolError("source_fetch_event_invalid")
    logical_ref = request.source_id or f"url.{contract_sha256({'url': request.url})[:32]}"
    if request.source_id is not None:
        existing = dependencies.repository.read_source(context.actor, request.source_id)
        if existing is None or existing.source.source_kind.value != "url":
            raise KnowledgeToolError("source_fetch_target_invalid")
        logical_ref = existing.source.source_identity.removeprefix("url:")
    envelope = IngestEnvelope(
        schema="knowledge.ingest-envelope.v1",
        delivery_id=f"delivery.{context.invocation_id}",
        event_kind=event.event_kind,
        request_text=event.text,
        attachments=(
            AttachmentCapability(
                ordinal=0,
                logical_source_ref=logical_ref,
                logical_revision_ref=f"revision.{context.invocation_id}",
                mime_type="application/octet-stream",
                capability_ref=context.invocation_id,
                source_url=request.url,
            ),
        ),
        timestamp=context.invoked_at,
    )
    delivery = dependencies.ingestion.ingest(context.actor, event, envelope)
    if len(delivery.unit_receipts) != 1:
        raise KnowledgeToolError("source_fetch_receipt_invalid")
    receipt = delivery.unit_receipts[0].receipt
    if request.source_id is not None and receipt.source_id != request.source_id:
        raise KnowledgeToolError("source_fetch_target_mismatch")
    return success(
        context.invocation_id,
        ToolResultStatus.REPLAYED if receipt.replayed else ToolResultStatus.APPLIED,
        SourceFetchData(
            source_id=receipt.source_id,
            revision_id=receipt.source_revision_id,
            curation_job_id=receipt.curation_job_id,
            index_operation_id=receipt.index_operation_id,
            replayed=receipt.replayed,
        ),
    )


def require_source_capability(
    request: SourceReadInput,
    revision_id: str,
    disposition: SourceDisposition,
    context: TrustedInvocationContext,
) -> None:
    if disposition in {
        SourceDisposition.REFERENCE,
        SourceDisposition.ADMIT,
        SourceDisposition.UPDATE,
    }:
        return
    if disposition is SourceDisposition.IGNORE:
        raise KnowledgeToolError("source_not_readable")
    capability = next(
        (
            item
            for item in context.source_capabilities
            if item.source_id == request.source_id
            and item.revision_id == revision_id
            and item.allows_unadmitted_read
            and (item.expires_at is None or item.expires_at > context.invoked_at)
        ),
        None,
    )
    if capability is None:
        raise KnowledgeToolError("source_capability_required")
    if request.segment_ids and not set(request.segment_ids).issubset(capability.segment_ids):
        raise KnowledgeToolError("source_capability_scope_mismatch")
    if request.text_range is not None and request.text_range not in capability.text_ranges:
        raise KnowledgeToolError("source_capability_scope_mismatch")


def segment_excerpt(body: str, segment: SourceSegment) -> SourceExcerpt:
    text = body[segment.quote_range.start : segment.quote_range.end]
    if sha256(text.encode()).hexdigest() != segment.content_sha256:
        raise KnowledgeToolError("source_segment_digest_mismatch")
    return SourceExcerpt(
        segment=segment,
        start=segment.quote_range.start,
        end=segment.quote_range.end,
        text=text,
    )


__all__ = ["source_fetch", "source_read", "source_search"]
