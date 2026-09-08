from __future__ import annotations

# ruff: noqa: D105, EM101
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from ads_booster.knowledge.change_page_lookup import page_candidates, read_current_page
from ads_booster.knowledge.change_validation import (
    ChangeValidationError,
    require_acyclic_ancestry,
    require_exact_keys,
    require_scope_not_wider,
)
from ads_booster.knowledge.contract_types import PageRelationKind, WikiPageStatus
from ads_booster.knowledge.wiki_contracts import (
    KnowledgeRevision,
    PageRelation,
    WikiPage,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ads_booster.knowledge.scope_contracts import AccessScope


@dataclass(frozen=True, slots=True)
class PageSnapshot:
    page: WikiPage
    revision: KnowledgeRevision
    body: bytes

    def __post_init__(self) -> None:
        if (
            self.page.page_id != self.revision.page_id
            or self.page.current_revision_id != self.revision.revision_id
        ):
            raise ChangeValidationError("page_head_mismatch", self.page.page_id)
        if sha256(self.body).hexdigest() != self.revision.body_sha256:
            raise ChangeValidationError("page_body_digest_mismatch", self.page.page_id)
        if (
            self.page.title != self.revision.title
            or self.page.aliases != self.revision.aliases
            or self.page.attributes != self.revision.attributes
            or self.page.scope != self.revision.scope
        ):
            raise ChangeValidationError("page_revision_metadata_mismatch", self.page.page_id)
        require_acyclic_ancestry(self.revision.evidence_ancestry)


@dataclass(frozen=True, slots=True)
class PageChangeSet:
    current: Mapping[str, PageSnapshot]
    redirects: Mapping[str, str]


def rename_page(snapshot: PageSnapshot, *, title: str, revision_id: str) -> PageSnapshot:
    aliases = tuple(dict.fromkeys((*snapshot.page.aliases, snapshot.page.title)))
    revision = snapshot.revision.model_copy(
        update={
            "revision_id": revision_id,
            "previous_revision_id": snapshot.revision.revision_id,
            "title": title,
            "aliases": aliases,
        }
    )
    page = snapshot.page.model_copy(
        update={"title": title, "aliases": aliases, "current_revision_id": revision_id}
    )
    return PageSnapshot(page=page, revision=revision, body=snapshot.body)


def unlink_page(snapshot: PageSnapshot, *, relation_id: str, revision_id: str) -> PageSnapshot:
    relations = tuple(
        relation for relation in snapshot.revision.relations if relation.relation_id != relation_id
    )
    if len(relations) == len(snapshot.revision.relations):
        raise ChangeValidationError("relation_not_found", relation_id)
    revision = snapshot.revision.model_copy(
        update={
            "revision_id": revision_id,
            "previous_revision_id": snapshot.revision.revision_id,
            "relations": relations,
        }
    )
    page = snapshot.page.model_copy(update={"current_revision_id": revision_id})
    return PageSnapshot(page=page, revision=revision, body=snapshot.body)


def link_page(snapshot: PageSnapshot, *, relation: PageRelation, revision_id: str) -> PageSnapshot:
    if relation.from_page_id != snapshot.page.page_id:
        raise ChangeValidationError("relation_page_mismatch", relation.relation_id)
    if any(item.relation_id == relation.relation_id for item in snapshot.revision.relations):
        raise ChangeValidationError("relation_id_conflict", relation.relation_id)
    revision = snapshot.revision.model_copy(
        update={
            "revision_id": revision_id,
            "previous_revision_id": snapshot.revision.revision_id,
            "relations": (*snapshot.revision.relations, relation),
        }
    )
    page = snapshot.page.model_copy(update={"current_revision_id": revision_id})
    return PageSnapshot(page=page, revision=revision, body=snapshot.body)


def merge_pages(
    *,
    target: PageSnapshot,
    sources: tuple[PageSnapshot, ...],
    claim_target_map: Mapping[str, str],
    revision_ids: Mapping[str, str],
    reason: str,
) -> PageChangeSet:
    if not sources:
        raise ChangeValidationError("merge_sources_empty", target.page.page_id)
    page_ids = (target.page.page_id, *(source.page.page_id for source in sources))
    if len(set(page_ids)) != len(page_ids):
        raise ChangeValidationError("merge_pages_not_unique", target.page.page_id)
    require_exact_keys(
        actual=revision_ids,
        expected=page_ids,
        error_code="revision_mapping_incomplete",
    )
    source_claims = tuple(claim for source in sources for claim in source.revision.claims)
    require_exact_keys(
        actual=claim_target_map,
        expected=(claim.claim_id for claim in source_claims),
        error_code="claim_mapping_incomplete",
    )
    if any(destination != target.page.page_id for destination in claim_target_map.values()):
        raise ChangeValidationError("claim_mapping_target_invalid", target.page.page_id)
    for source in sources:
        require_scope_not_wider(
            source=source.page.scope,
            target=target.page.scope,
            target_id=source.page.page_id,
        )

    target_revision_id = revision_ids[target.page.page_id]
    target_revision = target.revision.model_copy(
        update={
            "revision_id": target_revision_id,
            "previous_revision_id": target.revision.revision_id,
            "claims": (*target.revision.claims, *source_claims),
        }
    )
    target_page = target.page.model_copy(update={"current_revision_id": target_revision_id})
    current: dict[str, PageSnapshot] = {
        target.page.page_id: PageSnapshot(target_page, target_revision, target.body)
    }
    redirects: dict[str, str] = {}
    for source in sources:
        revision_id = revision_ids[source.page.page_id]
        relation = PageRelation(
            relation_id=f"merge.{source.page.page_id}.{target.page.page_id}",
            from_page_id=source.page.page_id,
            to_page_id=target.page.page_id,
            kind=PageRelationKind.SUMMARIZES,
            reason=reason,
        )
        source_revision = source.revision.model_copy(
            update={
                "revision_id": revision_id,
                "previous_revision_id": source.revision.revision_id,
                "claims": (),
                "relations": (relation,),
            }
        )
        source_page = source.page.model_copy(
            update={"current_revision_id": revision_id, "status": WikiPageStatus.REDIRECT}
        )
        current[source.page.page_id] = PageSnapshot(source_page, source_revision, source.body)
        redirects[source.page.page_id] = target.page.page_id
    return PageChangeSet(current=current, redirects=redirects)


def split_page(
    *,
    source: PageSnapshot,
    destinations: Mapping[str, tuple[str, AccessScope]],
    claim_target_map: Mapping[str, str],
    revision_ids: Mapping[str, str],
    reason: str,
) -> PageChangeSet:
    if not destinations or source.page.page_id in destinations:
        raise ChangeValidationError("split_destinations_invalid", source.page.page_id)
    require_exact_keys(
        actual=claim_target_map,
        expected=(claim.claim_id for claim in source.revision.claims),
        error_code="claim_mapping_incomplete",
    )
    if any(destination not in destinations for destination in claim_target_map.values()):
        raise ChangeValidationError("claim_mapping_target_invalid", source.page.page_id)
    page_ids = (source.page.page_id, *destinations)
    require_exact_keys(
        actual=revision_ids,
        expected=page_ids,
        error_code="revision_mapping_incomplete",
    )
    for destination_id, (_, scope) in destinations.items():
        require_scope_not_wider(source=source.page.scope, target=scope, target_id=destination_id)

    relations = tuple(
        PageRelation(
            relation_id=f"split.{source.page.page_id}.{destination_id}",
            from_page_id=source.page.page_id,
            to_page_id=destination_id,
            kind=PageRelationKind.SPLIT_INTO,
            reason=reason,
        )
        for destination_id in destinations
    )
    source_revision_id = revision_ids[source.page.page_id]
    source_revision = source.revision.model_copy(
        update={
            "revision_id": source_revision_id,
            "previous_revision_id": source.revision.revision_id,
            "claims": (),
            "relations": relations,
        }
    )
    source_page = source.page.model_copy(
        update={"current_revision_id": source_revision_id, "status": WikiPageStatus.SPLIT}
    )
    current: dict[str, PageSnapshot] = {
        source.page.page_id: PageSnapshot(source_page, source_revision, source.body)
    }
    empty_body = b""
    for destination_id, (title, scope) in destinations.items():
        revision_id = revision_ids[destination_id]
        claims = tuple(
            claim
            for claim in source.revision.claims
            if claim_target_map[claim.claim_id] == destination_id
        )
        revision = KnowledgeRevision(
            page_id=destination_id,
            revision_id=revision_id,
            previous_revision_id=None,
            body_sha256=sha256(empty_body).hexdigest(),
            title=title,
            claims=claims,
            scope=scope,
        )
        page = WikiPage(
            page_id=destination_id,
            title=title,
            scope=scope,
            current_revision_id=revision_id,
            status=WikiPageStatus.ACTIVE,
        )
        current[destination_id] = PageSnapshot(page, revision, empty_body)
    return PageChangeSet(current=current, redirects={})


__all__ = [
    "PageChangeSet",
    "PageSnapshot",
    "link_page",
    "merge_pages",
    "page_candidates",
    "read_current_page",
    "rename_page",
    "split_page",
    "unlink_page",
]
