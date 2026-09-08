from __future__ import annotations

# noqa: SIZE_OK
# ruff: noqa: PLR0913
from datetime import UTC, date, datetime
from hashlib import sha256

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.adoption_contracts import ExplicitAdoptionReceipt
from ads_booster.knowledge.change_validation import claim_semantic_fingerprint
from ads_booster.knowledge.contracts import (
    AccessScope,
    ActorContext,
    AuthenticatedEvent,
    AuthorityClass,
    AuthorityRef,
    Brand,
    BrandEvent,
    BrandEventKind,
    BrandState,
    Claim,
    ClaimKind,
    ClaimStatus,
    ConversationEvent,
    ConversationEventKind,
    ConversationRole,
    EvidenceKind,
    EvidenceRef,
    GrantCapability,
    InstructionAuthority,
    KnowledgeJob,
    KnowledgeRevision,
    MemoryDocument,
    MemoryEntry,
    MemoryEntryKind,
    MemoryKind,
    MemoryOrigin,
    MemoryRevision,
    MemoryStatus,
    OperationReceipt,
    OperationStatus,
    Provenance,
    ScopeGrant,
    ScopeKind,
    SegmentLocator,
    SoulSection,
    Source,
    SourceCompleteness,
    SourceDisposition,
    SourceExtractionStatus,
    SourceKind,
    SourceSegment,
    UsageRole,
    WikiPage,
    WikiPageStatus,
    WikiSummaryRef,
)
from ads_booster.knowledge.file_store import (
    MemoryRevisionTarget,
    RevisionFileDraft,
    SourceFileKind,
    SourceRevisionTarget,
)
from ads_booster.knowledge.operation_enums import JobKind, JobPriority, JobState
from ads_booster.knowledge.repository import (
    BrandRegistration,
    IndexOutboxItem,
    JobRegistration,
    SourceRegistration,
    SqliteKnowledgeRepository,
)
from ads_booster.knowledge.source_contracts import IngestReceipt

NOW = datetime(2026, 9, 7, 1, tzinfo=UTC)
WORKSPACE_SCOPE = AccessScope(kind=ScopeKind.WORKSPACE, workspace_id="workspace.alpha")
PRIVATE_SCOPE = AccessScope(
    kind=ScopeKind.MEMBER,
    workspace_id="workspace.alpha",
    member_id="member.private",
    session_id="session.private",
)


def adoption_receipt(
    *,
    expected_revision_id: str,
    authenticated_event_id: str = "event.decision",
) -> ExplicitAdoptionReceipt:
    return ExplicitAdoptionReceipt(
        receipt_id=f"receipt.adoption.{expected_revision_id}",
        proposal_id=f"proposal.adoption.{expected_revision_id}",
        question_id=f"question.adoption.{expected_revision_id}",
        workspace_id="workspace.alpha",
        actor_ref="actor.editor",
        brand_id="brand.a",
        expected_revision_id=expected_revision_id,
        authenticated_event_id=authenticated_event_id,
        answered_at=NOW,
    )


class AdoptionResolver:
    def __init__(self, receipt: ExplicitAdoptionReceipt) -> None:
        self.receipt = receipt

    def adoption_receipt(
        self,
        actor: ActorContext,
        receipt_id: str,
    ) -> ExplicitAdoptionReceipt | None:
        if actor.workspace_id != self.receipt.workspace_id or receipt_id != self.receipt.receipt_id:
            return None
        return self.receipt


def digest(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def evidence(
    *,
    evidence_id: str = "event.decision",
    revision_id: str = "event.decision.r1",
    scope: AccessScope = WORKSPACE_SCOPE,
    kind: EvidenceKind = EvidenceKind.CONVERSATION_EVENT,
    quote: str = "We adopt this rule.",
) -> EvidenceRef:
    return EvidenceRef(
        evidence_kind=kind,
        evidence_id=evidence_id,
        revision_id=revision_id,
        segment_id="segment.1",
        quote_sha256=digest(quote),
        scope=scope,
        instruction_authority=InstructionAuthority.AUTHORIZED_USER,
        provenance=Provenance.HUMAN_DIRECT,
    )


def authority(*, scope: AccessScope = WORKSPACE_SCOPE) -> AuthorityRef:
    return AuthorityRef(
        event_id="event.decision",
        authority_class=AuthorityClass.DELEGATED_TEAM_RULE,
        actor_ref="actor.editor",
        workspace_id="workspace.alpha",
        scope=scope,
        policy_epoch=3,
    )


def authenticated_event(
    *,
    scope: AccessScope = WORKSPACE_SCOPE,
    capabilities: tuple[GrantCapability, ...] = (GrantCapability.WRITE,),
) -> AuthenticatedEvent:
    return AuthenticatedEvent(
        event_id="event.decision",
        actor_ref="actor.editor",
        workspace_id="workspace.alpha",
        scope=scope,
        provenance=Provenance.HUMAN_DIRECT,
        authority_class=AuthorityClass.DELEGATED_TEAM_RULE,
        capabilities=capabilities,
        policy_epoch=3,
        occurred_at=NOW,
    )


def actor(
    *,
    scope: AccessScope = WORKSPACE_SCOPE,
    capabilities: tuple[GrantCapability, ...] = (GrantCapability.READ, GrantCapability.WRITE),
    brand_id: str | None = None,
) -> ActorContext:
    grants = tuple(
        ScopeGrant(
            grant_id=f"grant.{capability}.{index}",
            capability=capability,
            workspace_id="workspace.alpha",
            scope=scope,
            brand_id=brand_id if capability is GrantCapability.BRAND_VOICE_EDIT else None,
            policy_epoch=3,
            effective_at=NOW,
        )
        for index, capability in enumerate(capabilities)
    )
    return ActorContext(
        actor_id="actor.editor",
        workspace_id="workspace.alpha",
        member_id="member.editor",
        session_id="session.editor",
        conversation_scope=scope,
        grants=grants,
        policy_epoch=3,
        authenticated_at=NOW,
    )


def claim(
    *,
    claim_id: str = "claim.primary",
    statement: str = "The launch price is 29 dollars.",
    kind: ClaimKind = ClaimKind.FACT,
    evidence_ref: EvidenceRef | None = None,
) -> Claim:
    selected = evidence_ref or evidence(
        evidence_id="source.price",
        revision_id="source.price.r1",
        kind=EvidenceKind.SOURCE_SEGMENT,
        quote="The launch price is 29 dollars.",
    )
    return Claim(
        claim_id=claim_id,
        kind=kind,
        statement=statement,
        status=ClaimStatus.ACTIVE,
        evidence_refs=(selected,),
        authority_ref=authority() if kind is ClaimKind.DECISION else None,
        admission_reason="Verified from the cited immutable source.",
        observed_at=NOW,
    )


def page_snapshot(
    *,
    page_id: str = "page.pricing",
    title: str = "Pricing",
    revision_id: str = "page.pricing.r1",
    claims: tuple[Claim, ...] | None = None,
) -> tuple[WikiPage, KnowledgeRevision, bytes]:
    body = b"# Pricing\n"
    page = WikiPage(
        page_id=page_id,
        title=title,
        scope=WORKSPACE_SCOPE,
        current_revision_id=revision_id,
        status=WikiPageStatus.ACTIVE,
    )
    revision = KnowledgeRevision(
        page_id=page_id,
        revision_id=revision_id,
        previous_revision_id=None,
        body_sha256=sha256(body).hexdigest(),
        title=title,
        claims=claims or (claim(),),
        scope=WORKSPACE_SCOPE,
    )
    return page, revision, body


def memory_document(
    *,
    kind: MemoryKind = MemoryKind.CORE,
    document_id: str = "memory.core",
    brand_id: str | None = None,
    local_date: date | None = None,
) -> MemoryDocument:
    return MemoryDocument(
        document_id=document_id,
        workspace_id="workspace.alpha",
        kind=kind,
        brand_id=brand_id,
        local_date=local_date,
        timezone="UTC",
        head_revision_id=f"{document_id}.r1",
    )


def memory_entry(
    *,
    document: MemoryDocument | None = None,
    entry_id: str = "entry.primary",
    scope: AccessScope = WORKSPACE_SCOPE,
    origin: MemoryOrigin = MemoryOrigin.DIRECT,
    kind: MemoryEntryKind = MemoryEntryKind.DECISION,
    usage_role: UsageRole = UsageRole.REFERENCE,
    wiki_ref: WikiSummaryRef | None = None,
    soul_section: SoulSection | None = None,
) -> MemoryEntry:
    selected_document = document or memory_document()
    return MemoryEntry(
        entry_id=entry_id,
        document_id=selected_document.document_id,
        document_kind=selected_document.kind,
        text="Use concise reporting in team updates.",
        kind=kind,
        status=MemoryStatus.ACTIVE,
        dependency_state="current",
        origin=origin,
        usage_role=usage_role,
        scope=scope,
        source_refs=(evidence(scope=scope),),
        wiki_ref=wiki_ref,
        admission_reason="A team member explicitly adopted the reporting rule.",
        authority_ref=authority(scope=scope) if kind is MemoryEntryKind.DECISION else None,
        soul_section=soul_section,
    )


def memory_snapshot_parts(
    *, document: MemoryDocument | None = None, entries: tuple[MemoryEntry, ...] | None = None
) -> tuple[MemoryDocument, MemoryRevision, tuple[MemoryEntry, ...], bytes]:
    selected_document = document or memory_document()
    selected_entries = entries or (memory_entry(document=selected_document),)
    body = b"# Memory\n"
    revision = MemoryRevision(
        document_id=selected_document.document_id,
        revision_id=selected_document.head_revision_id,
        previous_revision_id=None,
        body_sha256=sha256(body).hexdigest(),
        entry_ids=tuple(entry.entry_id for entry in selected_entries),
        created_at=NOW,
    )
    return selected_document, revision, selected_entries, body


def wiki_summary_ref(*, source_claim: Claim | None = None) -> WikiSummaryRef:
    selected_claim = source_claim or claim()
    return WikiSummaryRef(
        page_id="page.pricing",
        claim_id=selected_claim.claim_id,
        revision_id="page.pricing.r1",
        semantic_fingerprint=claim_semantic_fingerprint(selected_claim),
    )


def register_evidence_source(
    repository: SqliteKnowledgeRepository,
    *,
    body: bytes,
    conversation: bool = False,
) -> tuple[EvidenceRef, EvidenceRef | None]:
    source_id = "source.decision" if conversation else "source.fact"
    revision_id = f"{source_id}.r1"
    segment_id = f"segment.{source_id}"
    source = Source(
        source_id=source_id,
        workspace_id="workspace.alpha",
        scope=WORKSPACE_SCOPE,
        owner_ref="member.editor",
        source_kind=SourceKind.MESSAGE if conversation else SourceKind.FILE,
        sanitized_locator="conversation" if conversation else "fact.txt",
        source_identity=f"fixture:{source_id}",
        revision_id=revision_id,
        revision=1,
        fetched_at=NOW,
        sha256=sha256(body).hexdigest(),
        mime_type="text/plain",
        byte_length=len(body),
        extraction_status=SourceExtractionStatus.COMPLETE,
        completeness=SourceCompleteness.FULL,
        extractor_version="extractor.test.v1",
        disposition=SourceDisposition.ADMIT,
        admission_revision=1,
    )
    segment = SourceSegment(
        source_id=source_id,
        revision_id=revision_id,
        segment_id=segment_id,
        content_sha256=source.sha256,
        extraction_version="extraction.test.v1",
        locator=SegmentLocator(line_start=1, line_end=1),
        quote_range={"start": 0, "end": len(body)},
        completeness=SourceCompleteness.FULL,
    )
    receipt = IngestReceipt(
        schema="knowledge.ingest-receipt.v1",
        delivery_id=f"delivery.{source_id}",
        source_id=source_id,
        source_revision_id=revision_id,
        curation_job_id=f"job.{source_id}",
        index_operation_id=f"index.{source_id}",
        replayed=False,
    )
    job = KnowledgeJob(
        schema="knowledge.job.v1",
        job_id=receipt.curation_job_id,
        workspace_id="workspace.alpha",
        scope=WORKSPACE_SCOPE,
        kind=JobKind.CURATION,
        state=JobState.QUEUED,
        priority=JobPriority.ROUTINE,
        root_event_id="event.decision" if conversation else "event.fact",
        policy_version="policy.test.v1",
        due_at=NOW,
        created_at=NOW,
    )
    event = (
        ConversationEvent(
            conversation_id="conversation.test",
            message_id="event.decision",
            revision=1,
            sequence=1,
            role=ConversationRole.USER,
            speaker_ref="actor.editor",
            created_at=NOW,
            text=body.decode(),
            event_kind=ConversationEventKind.MESSAGE_FINALIZED,
            scope=WORKSPACE_SCOPE,
        )
        if conversation
        else None
    )
    prepared = repository.files.prepare(
        RevisionFileDraft(
            operation_id=f"operation.{source_id}",
            target=SourceRevisionTarget(
                source_id=source_id,
                revision_id=revision_id,
                file_kind=SourceFileKind.ORIGINAL,
            ),
            content=body,
            sha256=source.sha256,
        )
    )
    _ = repository.register_source(
        SourceRegistration(
            operation_id=f"operation.{source_id}",
            payload_sha256=contract_sha256(source),
            source=source,
            segments=(segment,),
            receipt=receipt,
            job=JobRegistration(job=job, unique_key=f"{source_id}:curation"),
            index_item=IndexOutboxItem(
                item_id=receipt.index_operation_id,
                workspace_id="workspace.alpha",
                entity_kind="source",
                entity_id=source_id,
                revision_id=revision_id,
                extraction_version=segment.extraction_version,
                admission_revision=1,
            ),
            prepared_files=(
                prepared,
                repository.files.prepare(
                    RevisionFileDraft(
                        operation_id=f"operation.{source_id}",
                        target=SourceRevisionTarget(
                            source_id=source_id,
                            revision_id=revision_id,
                            file_kind=SourceFileKind.EXTRACTED,
                        ),
                        content=body,
                        sha256=source.sha256,
                    )
                ),
            ),
            conversation_event=event,
        )
    )
    source_ref = EvidenceRef(
        evidence_kind=EvidenceKind.SOURCE_SEGMENT,
        evidence_id=segment_id,
        revision_id=revision_id,
        segment_id=segment_id,
        quote_sha256=source.sha256,
        scope=WORKSPACE_SCOPE,
        instruction_authority=InstructionAuthority.DATA,
        provenance=Provenance.EXTERNAL,
    )
    event_ref = (
        EvidenceRef(
            evidence_kind=EvidenceKind.CONVERSATION_EVENT,
            evidence_id="event.decision",
            revision_id="1",
            quote_sha256=source.sha256,
            scope=WORKSPACE_SCOPE,
            instruction_authority=InstructionAuthority.AUTHORIZED_USER,
            provenance=Provenance.HUMAN_DIRECT,
        )
        if conversation
        else None
    )
    return source_ref, event_ref


def register_brand(repository: SqliteKnowledgeRepository) -> MemoryDocument:
    brand = Brand(
        brand_id="brand.a",
        workspace_id="workspace.alpha",
        name="Brand A",
        revision=1,
        state=BrandState.ACTIVE,
    )
    event = BrandEvent(
        event_id="event.brand.register",
        kind=BrandEventKind.REGISTERED,
        brand_id=brand.brand_id,
        workspace_id=brand.workspace_id,
        authority_ref=AuthorityRef(
            event_id="event.brand.register",
            authority_class=AuthorityClass.RUNTIME_POLICY,
            actor_ref="actor.editor",
            workspace_id="workspace.alpha",
            scope=WORKSPACE_SCOPE,
            policy_epoch=3,
        ),
        occurred_at=NOW,
    )
    body = b""
    revision = MemoryRevision(
        document_id="memory.soul.a",
        revision_id="memory.soul.a.r1",
        previous_revision_id=None,
        body_sha256=sha256(body).hexdigest(),
        created_at=NOW,
    )
    document = MemoryDocument(
        document_id=revision.document_id,
        workspace_id="workspace.alpha",
        kind=MemoryKind.SOUL,
        brand_id=brand.brand_id,
        timezone="UTC",
        head_revision_id=revision.revision_id,
    )
    operation_id = "operation.brand.register"
    prepared = repository.files.prepare(
        RevisionFileDraft(
            operation_id=operation_id,
            target=MemoryRevisionTarget(
                workspace_id="workspace.alpha",
                document_id=document.document_id,
                revision_id=revision.revision_id,
            ),
            content=body,
            sha256=revision.body_sha256,
        )
    )
    receipt = OperationReceipt(
        schema="knowledge.operation-receipt.v1",
        operation_id=operation_id,
        status=OperationStatus.APPLIED,
        resulting_revision_ids=(revision.revision_id,),
        retryable=False,
        occurred_at=NOW,
    )
    _ = repository.register_brand(
        BrandRegistration(
            brand=brand,
            event=event,
            document=document,
            revision=revision,
            prepared_file=prepared,
            receipt=receipt,
            payload_sha256=contract_sha256(event),
        )
    )
    return document
