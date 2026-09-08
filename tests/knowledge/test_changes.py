from __future__ import annotations

# noqa: SIZE_OK
# ruff: noqa: PLR0913
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from ads_booster.contracts.knowledge_selection import KnowledgeActionKind
from ads_booster.knowledge.change_validation import (
    ChangeValidationError,
    claim_semantic_fingerprint,
    require_acyclic_ancestry,
)
from ads_booster.knowledge.changes import (
    ChangeGroup,
    ChangePublisher,
    MemoryPublication,
    classify_claim_changes,
    resolve_constraints,
)
from ads_booster.knowledge.contracts import (
    AppliesTo,
    AuthorityClass,
    ChangeImpactKind,
    ClaimKind,
    ConstraintBinding,
    ConstraintCompatibility,
    DependencyState,
    EvidenceEdge,
    EvidenceKind,
    EvidenceRef,
    GrantCapability,
    InstructionAuthority,
    KnowledgeOperation,
    KnowledgeOperationKind,
    MemoryEntryKind,
    MemoryKind,
    MemoryOperation,
    MemoryOperationKind,
    MemoryOrigin,
    MemoryRevision,
    Provenance,
    SoulSection,
)
from ads_booster.knowledge.memory import MemorySnapshot
from ads_booster.knowledge.pages import (
    PageChangeSet,
    PageSnapshot,
    merge_pages,
    read_current_page,
)
from ads_booster.knowledge.repository import (
    MembershipRole,
    RepositoryConflictError,
    SqliteKnowledgeRepository,
)
from tests.knowledge.change_test_fixtures import (
    NOW,
    AdoptionResolver,
    actor,
    adoption_receipt,
    authority,
    claim,
    digest,
    memory_document,
    memory_entry,
    memory_snapshot_parts,
    page_snapshot,
    register_brand,
    register_evidence_source,
    wiki_summary_ref,
)


def _constraint(
    *,
    constraint_id: str,
    authority_class: AuthorityClass = AuthorityClass.DELEGATED_TEAM_RULE,
    compatibility: ConstraintCompatibility = ConstraintCompatibility.COMPATIBLE,
    supersedes_ids: tuple[str, ...] = (),
    overrides_ids: tuple[str, ...] = (),
    task_ref: str | None = None,
) -> ConstraintBinding:
    return ConstraintBinding(
        constraint_id=constraint_id,
        workspace_id="workspace.alpha",
        entry_id=f"entry.{constraint_id}",
        revision_id=f"revision.{constraint_id}",
        applies_to=AppliesTo(
            action_kinds=(KnowledgeActionKind.CONTENT_WRITE,),
            task_ref=task_ref,
            subject_key="headline.promise",
        ),
        authority_ref=authority().model_copy(update={"authority_class": authority_class}),
        authority_class=authority_class,
        compatibility=compatibility,
        supersedes_ids=supersedes_ids,
        overrides_ids=overrides_ids,
    )


def test_semantic_fingerprint_ignores_page_display_fields() -> None:
    # Given
    original = claim()
    unchanged = original.model_copy()

    # When
    impact = classify_claim_changes(
        previous=(original,),
        current=(unchanged,),
        dependency_ids={original.claim_id: ("summary.price",)},
    )

    # Then
    assert claim_semantic_fingerprint(original) == claim_semantic_fingerprint(unchanged)
    assert impact.kind is ChangeImpactKind.DISPLAY_ONLY
    assert impact.affected_dependency_ids == ()


def test_semantic_change_marks_only_affected_dependency_despite_display_only_claim() -> None:
    # Given
    price = claim()
    history = claim(claim_id="claim.history", statement="The old price was 19 dollars.")
    changed_price = price.model_copy(update={"statement": "The launch price is 39 dollars."})

    # When
    impact = classify_claim_changes(
        previous=(price, history),
        current=(changed_price, history),
        dependency_ids={price.claim_id: ("summary.price",), history.claim_id: ("summary.history",)},
    )

    # Then
    assert impact.kind is ChangeImpactKind.SEMANTIC_OR_UNKNOWN
    assert impact.affected_dependency_ids == ("summary.price",)


def test_ancestry_cycle_is_rejected_while_related_page_cycles_are_outside_evidence() -> None:
    # Given
    edges = (
        EvidenceEdge(derived_evidence_id="claim.a", upstream_evidence_id="claim.b"),
        EvidenceEdge(derived_evidence_id="claim.b", upstream_evidence_id="claim.a"),
    )

    # When / Then
    with pytest.raises(ChangeValidationError, match="evidence_ancestry_cycle"):
        require_acyclic_ancestry(edges)


def test_explicit_equal_authority_supersession_resolves_conflict() -> None:
    # Given
    old = _constraint(
        constraint_id="constraint.old",
        compatibility=ConstraintCompatibility.CONFLICT,
    )
    replacement = _constraint(
        constraint_id="constraint.new",
        supersedes_ids=(old.constraint_id,),
    )

    # When
    active = resolve_constraints((old, replacement))

    # Then
    assert tuple(item.constraint_id for item in active) == (replacement.constraint_id,)


def test_lower_authority_override_is_rejected() -> None:
    # Given
    runtime = _constraint(
        constraint_id="constraint.runtime",
        authority_class=AuthorityClass.RUNTIME_POLICY,
    )
    task = _constraint(
        constraint_id="constraint.task",
        authority_class=AuthorityClass.AUTHORIZED_TASK_INSTRUCTION,
        supersedes_ids=(runtime.constraint_id,),
        task_ref="task.1",
    )

    # When / Then
    with pytest.raises(ChangeValidationError, match="constraint_authority_insufficient"):
        _ = resolve_constraints((runtime, task))


def test_unresolved_overlapping_constraint_conflict_blocks_affected_action() -> None:
    # Given
    first = _constraint(constraint_id="constraint.first")
    second = _constraint(
        constraint_id="constraint.second",
        compatibility=ConstraintCompatibility.CONFLICT,
    )

    # When / Then
    with pytest.raises(ChangeValidationError, match="constraint_conflict"):
        _ = resolve_constraints((first, second))


def test_unrelated_action_constraint_does_not_block() -> None:
    # Given
    writing = _constraint(constraint_id="constraint.writing")
    research = _constraint(
        constraint_id="constraint.research",
        compatibility=ConstraintCompatibility.CONFLICT,
    ).model_copy(
        update={
            "applies_to": AppliesTo(
                action_kinds=(KnowledgeActionKind.RESEARCH,), subject_key="source.selection"
            )
        }
    )

    # When
    active = resolve_constraints((writing, research))

    # Then
    assert {item.constraint_id for item in active} == {
        writing.constraint_id,
        research.constraint_id,
    }


def test_override_requires_existing_target() -> None:
    override = _constraint(
        constraint_id="constraint.override",
        task_ref="task.one",
        overrides_ids=("constraint.missing",),
    )

    with pytest.raises(ChangeValidationError, match="constraint_override_target_missing"):
        _ = resolve_constraints((override,))


def _memory_operation(
    *, operation_id: str, document_id: str, expected_revision_id: str
) -> MemoryOperation:
    return MemoryOperation(
        operation_id=operation_id,
        kind=MemoryOperationKind.UPDATE,
        document_id=document_id,
        entry_id=f"entry.{document_id}",
        expected_revision_id=expected_revision_id,
        reason="Publish the validated memory head.",
        evidence_refs=("event.seed",),
    )


def _empty_memory_snapshot(
    *, document_id: str, revision_id: str, previous_revision_id: str | None
) -> MemorySnapshot:
    kind = MemoryKind.TEAM if document_id == "memory.team" else MemoryKind.CORE
    document = memory_document(kind=kind, document_id=document_id).model_copy(
        update={"head_revision_id": revision_id}
    )
    revision = MemoryRevision(
        document_id=document_id,
        revision_id=revision_id,
        previous_revision_id=previous_revision_id,
        body_sha256=digest(""),
        entry_ids=(),
        created_at=NOW,
    )
    return MemorySnapshot(document, revision, (), b"")


def test_publisher_writes_page_revision_and_reads_it_from_real_store(tmp_path: Path) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = actor()
    repository.register_actor(editor, MembershipRole.ADMIN)
    page, revision, body = page_snapshot(claims=())
    initial = PageSnapshot(page, revision.model_copy(update={"claims": ()}), body)
    operation = KnowledgeOperation(
        operation_id="operation.page.create",
        kind=KnowledgeOperationKind.PAGE_CREATE,
        target_page_ids=(page.page_id,),
        expected_revision_ids=("none",),
        reason="Create the pricing page.",
    )
    initial = PageSnapshot(
        initial.page,
        initial.revision.model_copy(update={"previous_revision_id": None}),
        initial.body,
    )

    # When
    receipt = ChangePublisher(repository).publish(
        actor=editor,
        group=ChangeGroup(operation_id=operation.operation_id, page_operation=operation),
        pages=PageChangeSet(current={page.page_id: initial}, redirects={}),
        memories=(),
        at=NOW,
    )
    stored = repository.read_page(editor, page.page_id)

    # Then
    assert receipt.resulting_revision_ids == (revision.revision_id,)
    assert stored is not None
    assert stored.revision == initial.revision
    assert stored.body == body


def test_stale_multi_memory_publish_has_no_partial_commit(tmp_path: Path) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = actor()
    repository.register_actor(editor, MembershipRole.ADMIN)
    publisher = ChangePublisher(repository)
    team_r1 = _empty_memory_snapshot(
        document_id="memory.team", revision_id="memory.team.r1", previous_revision_id=None
    )
    core_r1 = _empty_memory_snapshot(
        document_id="memory.core", revision_id="memory.core.r1", previous_revision_id=None
    )
    create_id = "operation.memory.create"
    create_operations = tuple(
        _memory_operation(
            operation_id=create_id,
            document_id=item.document.document_id,
            expected_revision_id="none",
        )
        for item in (team_r1, core_r1)
    )
    _ = publisher.publish(
        actor=editor,
        group=ChangeGroup(operation_id=create_id, memory_operations=create_operations),
        pages=None,
        memories=(MemoryPublication(team_r1), MemoryPublication(core_r1)),
        at=NOW,
    )
    team_winner = _empty_memory_snapshot(
        document_id="memory.team",
        revision_id="memory.team.r2.winner",
        previous_revision_id="memory.team.r1",
    )
    winner_id = "operation.memory.winner"
    _ = publisher.publish(
        actor=editor,
        group=ChangeGroup(
            operation_id=winner_id,
            memory_operations=(
                _memory_operation(
                    operation_id=winner_id,
                    document_id="memory.team",
                    expected_revision_id="memory.team.r1",
                ),
            ),
        ),
        pages=None,
        memories=(MemoryPublication(team_winner),),
        at=NOW,
    )
    stale_team = _empty_memory_snapshot(
        document_id="memory.team",
        revision_id="memory.team.r2.stale",
        previous_revision_id="memory.team.r1",
    )
    core_candidate = _empty_memory_snapshot(
        document_id="memory.core",
        revision_id="memory.core.r2.candidate",
        previous_revision_id="memory.core.r1",
    )
    stale_id = "operation.memory.stale"
    stale_operations = tuple(
        _memory_operation(
            operation_id=stale_id,
            document_id=item.document.document_id,
            expected_revision_id=item.revision.previous_revision_id or "none",
        )
        for item in (stale_team, core_candidate)
    )

    # When / Then
    with pytest.raises(RepositoryConflictError, match="memory_head_conflict"):
        _ = publisher.publish(
            actor=editor,
            group=ChangeGroup(operation_id=stale_id, memory_operations=stale_operations),
            pages=None,
            memories=(MemoryPublication(stale_team), MemoryPublication(core_candidate)),
            at=NOW,
        )
    stored_team = repository.read_memory(editor, "memory.team")
    stored_core = repository.read_memory(editor, "memory.core")
    assert stored_team is not None
    assert stored_team.revision.revision_id == "memory.team.r2.winner"
    assert stored_core is not None
    assert stored_core.revision.revision_id == "memory.core.r1"
    assert repository.read_memory(editor, "memory.team", "memory.team.r2.stale") is None
    assert repository.read_memory(editor, "memory.core", "memory.core.r2.candidate") is None
    assert repository.operation_receipt(editor, stale_id) is None


def test_publisher_accepts_external_fact_and_inference_with_resolved_quote(
    tmp_path: Path,
) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = actor()
    repository.register_actor(editor, MembershipRole.ADMIN)
    statement = "The launch price is 29 dollars."
    source_ref, _ = register_evidence_source(repository, body=statement.encode())
    verified_claim = claim(evidence_ref=source_ref)
    inferred_claim = claim(
        claim_id="claim.price-positioning",
        statement="The offer is positioned as an accessible entry price.",
        kind=ClaimKind.INFERENCE,
        evidence_ref=source_ref,
    )
    page, revision, body = page_snapshot(claims=(verified_claim, inferred_claim))
    snapshot = PageSnapshot(page, revision, body)
    operation = KnowledgeOperation(
        operation_id="operation.page.fact",
        kind=KnowledgeOperationKind.PAGE_CREATE,
        target_page_ids=(page.page_id,),
        expected_revision_ids=("none",),
        claim_ids=(verified_claim.claim_id, inferred_claim.claim_id),
        evidence_refs=(source_ref.evidence_id,),
        reason="Publish the externally supported fact.",
    )

    # When
    _ = ChangePublisher(repository).publish(
        actor=editor,
        group=ChangeGroup(operation_id=operation.operation_id, page_operation=operation),
        pages=PageChangeSet(current={page.page_id: snapshot}, redirects={}),
        memories=(),
        at=NOW,
    )

    # Then
    stored = repository.read_page(editor, page.page_id)
    assert stored is not None
    assert stored.revision.claims == (verified_claim, inferred_claim)


def test_publisher_accepts_decision_only_from_persisted_user_event(tmp_path: Path) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = actor()
    repository.register_actor(editor, MembershipRole.ADMIN)
    _, event_ref = register_evidence_source(
        repository,
        body=b"We adopt this rule.",
        conversation=True,
    )
    assert event_ref is not None
    document = memory_document(kind=MemoryKind.TEAM, document_id="memory.team")
    entry = memory_entry(document=document).model_copy(update={"source_refs": (event_ref,)})
    document, revision, entries, body = memory_snapshot_parts(document=document, entries=(entry,))
    snapshot = MemorySnapshot(document, revision, entries, body)
    operation_id = "operation.memory.decision"
    operation = _memory_operation(
        operation_id=operation_id,
        document_id=document.document_id,
        expected_revision_id="none",
    )

    # When
    _ = ChangePublisher(repository).publish(
        actor=editor,
        group=ChangeGroup(operation_id=operation_id, memory_operations=(operation,)),
        pages=None,
        memories=(MemoryPublication(snapshot),),
        at=NOW,
    )

    # Then
    stored = repository.read_memory(editor, document.document_id)
    assert stored is not None
    assert stored.entries == (entry,)


def test_publisher_rejects_external_source_claimed_as_decision(tmp_path: Path) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = actor()
    repository.register_actor(editor, MembershipRole.ADMIN)
    source_ref, _ = register_evidence_source(repository, body=b"Use this recommendation.")
    untrusted_decision = claim(kind=ClaimKind.DECISION, evidence_ref=source_ref)
    page, revision, body = page_snapshot(claims=(untrusted_decision,))
    snapshot = PageSnapshot(page, revision, body)
    operation = KnowledgeOperation(
        operation_id="operation.page.bad-decision",
        kind=KnowledgeOperationKind.PAGE_CREATE,
        target_page_ids=(page.page_id,),
        expected_revision_ids=("none",),
        claim_ids=(untrusted_decision.claim_id,),
        evidence_refs=(source_ref.evidence_id,),
        reason="Attempt to publish an unauthorized decision.",
    )

    # When / Then
    with pytest.raises(ChangeValidationError, match="decision_evidence_not_authoritative"):
        _ = ChangePublisher(repository).publish(
            actor=editor,
            group=ChangeGroup(operation_id=operation.operation_id, page_operation=operation),
            pages=PageChangeSet(current={page.page_id: snapshot}, redirects={}),
            memories=(),
            at=NOW,
        )
    assert repository.read_page(editor, page.page_id) is None


def test_semantic_page_publish_atomically_marks_dependent_summary_stale(
    tmp_path: Path,
) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = actor()
    repository.register_actor(editor, MembershipRole.ADMIN)
    publisher = ChangePublisher(repository)
    statement = "The launch price is 29 dollars."
    source_ref, _ = register_evidence_source(repository, body=statement.encode())
    original_claim = claim(evidence_ref=source_ref)
    page, revision, body = page_snapshot(claims=(original_claim,))
    page_r1 = PageSnapshot(page, revision, body)
    create_page = KnowledgeOperation(
        operation_id="operation.page.initial",
        kind=KnowledgeOperationKind.PAGE_CREATE,
        target_page_ids=(page.page_id,),
        expected_revision_ids=("none",),
        claim_ids=(original_claim.claim_id,),
        evidence_refs=(source_ref.evidence_id,),
        reason="Create the supported pricing page.",
    )
    _ = publisher.publish(
        actor=editor,
        group=ChangeGroup(operation_id=create_page.operation_id, page_operation=create_page),
        pages=PageChangeSet(current={page.page_id: page_r1}, redirects={}),
        memories=(),
        at=NOW,
    )
    document = memory_document()
    claim_ref = EvidenceRef(
        evidence_kind=EvidenceKind.CLAIM,
        evidence_id=original_claim.claim_id,
        revision_id=revision.revision_id,
        scope=page.scope,
        instruction_authority=InstructionAuthority.DATA,
        provenance=Provenance.AGENT_DERIVED,
    )
    summary = memory_entry(
        document=document,
        origin=MemoryOrigin.WIKI_SUMMARY,
        kind=MemoryEntryKind.FACT,
        wiki_ref=wiki_summary_ref(source_claim=original_claim),
    ).model_copy(update={"source_refs": (claim_ref,), "authority_ref": None})
    memory_document_value, memory_revision, entries, memory_body = memory_snapshot_parts(
        document=document,
        entries=(summary,),
    )
    memory_r1 = MemorySnapshot(
        memory_document_value,
        memory_revision,
        entries,
        memory_body,
    )
    create_memory_id = "operation.memory.summary"
    _ = publisher.publish(
        actor=editor,
        group=ChangeGroup(
            operation_id=create_memory_id,
            memory_operations=(
                _memory_operation(
                    operation_id=create_memory_id,
                    document_id=document.document_id,
                    expected_revision_id="none",
                ),
            ),
        ),
        pages=None,
        memories=(MemoryPublication(memory_r1),),
        at=NOW,
    )
    changed_claim = original_claim.model_copy(
        update={"statement": "The launch price is 39 dollars."}
    )
    page_r2 = PageSnapshot(
        page.model_copy(update={"current_revision_id": "page.pricing.r2"}),
        revision.model_copy(
            update={
                "revision_id": "page.pricing.r2",
                "previous_revision_id": revision.revision_id,
                "claims": (changed_claim,),
            }
        ),
        body,
    )
    edit_page = KnowledgeOperation(
        operation_id="operation.page.semantic-edit",
        kind=KnowledgeOperationKind.PAGE_EDIT,
        target_page_ids=(page.page_id,),
        expected_revision_ids=(revision.revision_id,),
        claim_ids=(original_claim.claim_id,),
        evidence_refs=(source_ref.evidence_id,),
        reason="Update the price statement.",
    )

    # When
    _ = publisher.publish(
        actor=editor,
        group=ChangeGroup(operation_id=edit_page.operation_id, page_operation=edit_page),
        pages=PageChangeSet(current={page.page_id: page_r2}, redirects={}),
        memories=(),
        at=NOW,
    )

    # Then
    stored_memory = repository.read_memory(editor, document.document_id)
    assert stored_memory is not None
    assert stored_memory.revision.revision_id != memory_revision.revision_id
    assert stored_memory.entries[0].dependency_state is DependencyState.STALE


def test_team_to_soul_move_uses_two_head_cas_and_preserves_event_citation(
    tmp_path: Path,
) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    basic_editor = actor()
    repository.register_actor(basic_editor, MembershipRole.ADMIN)
    soul_document = register_brand(repository)
    capabilities = (
        GrantCapability.READ,
        GrantCapability.WRITE,
        GrantCapability.BRAND_VOICE_EDIT,
    )
    editor = actor(capabilities=capabilities, brand_id="brand.a")
    repository.register_actor(editor, MembershipRole.ADMIN)
    _, event_ref = register_evidence_source(
        repository,
        body=b"We adopt this rule.",
        conversation=True,
    )
    assert event_ref is not None
    team_document = memory_document(kind=MemoryKind.TEAM, document_id="memory.team")
    team_entry = memory_entry(document=team_document).model_copy(
        update={"source_refs": (event_ref,)}
    )
    team_document, team_revision, team_entries, team_body = memory_snapshot_parts(
        document=team_document,
        entries=(team_entry,),
    )
    team_r1 = MemorySnapshot(team_document, team_revision, team_entries, team_body)
    create_team_id = "operation.team.create"
    _ = ChangePublisher(repository).publish(
        actor=editor,
        group=ChangeGroup(
            operation_id=create_team_id,
            memory_operations=(
                _memory_operation(
                    operation_id=create_team_id,
                    document_id=team_document.document_id,
                    expected_revision_id="none",
                ),
            ),
        ),
        pages=None,
        memories=(MemoryPublication(team_r1),),
        at=NOW,
    )
    stored_soul_r1 = repository.read_memory(editor, soul_document.document_id)
    assert stored_soul_r1 is not None
    team_r2_revision = MemoryRevision(
        document_id=team_document.document_id,
        revision_id="memory.team.r2",
        previous_revision_id=team_revision.revision_id,
        body_sha256=digest(""),
        entry_ids=(),
        created_at=NOW,
    )
    team_r2 = MemorySnapshot(
        team_document.model_copy(update={"head_revision_id": team_r2_revision.revision_id}),
        team_r2_revision,
        (),
        b"",
    )
    soul_entry = team_entry.model_copy(
        update={
            "document_id": soul_document.document_id,
            "document_kind": MemoryKind.SOUL,
            "soul_section": SoulSection.VOICE,
        }
    )
    soul_body = b"# Soul\nWe adopt this rule.\n"
    soul_r2_revision = MemoryRevision(
        document_id=soul_document.document_id,
        revision_id="memory.soul.a.r2",
        previous_revision_id=stored_soul_r1.revision.revision_id,
        body_sha256=digest(soul_body.decode()),
        entry_ids=(soul_entry.entry_id,),
        created_at=NOW,
    )
    soul_r2 = MemorySnapshot(
        soul_document.model_copy(update={"head_revision_id": soul_r2_revision.revision_id}),
        soul_r2_revision,
        (soul_entry,),
        soul_body,
    )
    move_id = "operation.team-to-soul"
    operations = (
        _memory_operation(
            operation_id=move_id,
            document_id=team_document.document_id,
            expected_revision_id=team_revision.revision_id,
        ),
        _memory_operation(
            operation_id=move_id,
            document_id=soul_document.document_id,
            expected_revision_id=stored_soul_r1.revision.revision_id,
        ),
    )

    # When
    receipt = adoption_receipt(expected_revision_id=stored_soul_r1.revision.revision_id)
    _ = ChangePublisher(repository, adoption_resolver=AdoptionResolver(receipt)).publish(
        actor=editor,
        group=ChangeGroup(
            operation_id=move_id,
            memory_operations=operations,
            adoption_receipt_ids=(receipt.receipt_id,),
        ),
        pages=None,
        memories=(MemoryPublication(team_r2), MemoryPublication(soul_r2)),
        at=NOW,
    )

    # Then
    stored_team = repository.read_memory(editor, team_document.document_id)
    stored_soul = repository.read_memory(editor, soul_document.document_id)
    historical_team = repository.read_memory(
        editor,
        team_document.document_id,
        team_revision.revision_id,
    )
    assert stored_team is not None
    assert stored_team.entries == ()
    assert stored_soul is not None
    assert stored_soul.entries == (soul_entry,)
    assert historical_team is not None
    assert historical_team.entries == (team_entry,)
    assert stored_soul.entries[0].source_refs == historical_team.entries[0].source_refs


def test_merge_publication_resolves_old_id_and_keeps_historical_revision(
    tmp_path: Path,
) -> None:
    # Given
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = actor()
    repository.register_actor(editor, MembershipRole.ADMIN)
    target_page, target_revision, target_body = page_snapshot(claims=())
    source_page, source_revision, source_body = page_snapshot(
        page_id="page.price-history",
        title="Price history",
        revision_id="page.price-history.r1",
        claims=(),
    )
    target = PageSnapshot(
        target_page,
        target_revision.model_copy(update={"claims": ()}),
        target_body,
    )
    source = PageSnapshot(
        source_page,
        source_revision.model_copy(update={"claims": ()}),
        source_body,
    )
    create = KnowledgeOperation(
        operation_id="operation.pages.create",
        kind=KnowledgeOperationKind.PAGE_CREATE,
        target_page_ids=(target.page.page_id, source.page.page_id),
        expected_revision_ids=("none", "none"),
        reason="Create both page identities.",
    )
    publisher = ChangePublisher(repository)
    _ = publisher.publish(
        actor=editor,
        group=ChangeGroup(operation_id=create.operation_id, page_operation=create),
        pages=PageChangeSet(
            current={target.page.page_id: target, source.page.page_id: source},
            redirects={},
        ),
        memories=(),
        at=NOW,
    )
    merged = merge_pages(
        target=target,
        sources=(source,),
        claim_target_map={},
        revision_ids={
            target.page.page_id: "page.pricing.r2",
            source.page.page_id: "page.price-history.r2",
        },
        reason="The pages describe one pricing topic.",
    )
    merge = KnowledgeOperation(
        operation_id="operation.pages.merge",
        kind=KnowledgeOperationKind.PAGE_MERGE,
        target_page_ids=(target.page.page_id, source.page.page_id),
        expected_revision_ids=(target.revision.revision_id, source.revision.revision_id),
        reason="Merge the duplicate pricing pages.",
    )

    # When
    _ = publisher.publish(
        actor=editor,
        group=ChangeGroup(operation_id=merge.operation_id, page_operation=merge),
        pages=merged,
        memories=(),
        at=NOW,
    )

    # Then
    resolved = read_current_page(repository, editor, source.page.page_id)
    historical = repository.read_page(
        editor,
        source.page.page_id,
        source.revision.revision_id,
    )
    assert resolved is not None
    assert resolved.page.page_id == target.page.page_id
    assert historical is not None
    assert historical.revision.title == source.revision.title
