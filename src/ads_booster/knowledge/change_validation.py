from __future__ import annotations

# ruff: noqa: EM101
from dataclasses import dataclass
from hashlib import sha256
from json import dumps
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from ads_booster.knowledge.evidence_contracts import EvidenceRef
    from ads_booster.knowledge.scope_contracts import AccessScope
    from ads_booster.knowledge.wiki_contracts import Claim, EvidenceEdge


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK
class ChangeValidationError(Exception):
    code: str
    target_id: str | None = None

    @override
    def __str__(self) -> str:
        if self.target_id is None:
            return self.code
        return f"{self.code}: target={self.target_id}"


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    ref: EvidenceRef
    quote: str | None
    ancestry: tuple[EvidenceEdge, ...] = ()


def require_scope_not_wider(*, source: AccessScope, target: AccessScope, target_id: str) -> None:
    if source.workspace_id != target.workspace_id:
        raise ChangeValidationError("workspace_scope_mismatch", target_id)
    if not source.contains(target):
        raise ChangeValidationError("scope_expansion_forbidden", target_id)


def require_acyclic_ancestry(edges: Iterable[EvidenceEdge]) -> None:
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge.derived_evidence_id, set()).add(edge.upstream_evidence_id)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ChangeValidationError("evidence_ancestry_cycle", node)
        if node in visited:
            return
        visiting.add(node)
        for upstream in adjacency.get(node, ()):
            visit(upstream)
        visiting.remove(node)
        visited.add(node)

    for node in adjacency:
        visit(node)


def claim_semantic_fingerprint(claim: Claim) -> str:
    payload = {
        "applicability": claim.applicability.model_dump(mode="json")
        if claim.applicability is not None
        else None,
        "evidence": sorted(
            (
                item.evidence_kind,
                item.evidence_id,
                item.revision_id,
                item.segment_id,
                item.quote_sha256,
                item.scope.model_dump_json(),
            )
            for item in (*claim.evidence_refs, *claim.counter_evidence_refs)
        ),
        "kind": claim.kind,
        "statement": claim.statement,
        "status": claim.status,
    }
    return sha256(dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def require_exact_keys(*, actual: Iterable[str], expected: Iterable[str], error_code: str) -> None:
    if set(actual) != set(expected):
        raise ChangeValidationError(error_code)


def require_no_redirect_cycle(redirects: Mapping[str, str]) -> None:
    for origin in redirects:
        current = origin
        seen: set[str] = set()
        while current in redirects:
            if current in seen:
                raise ChangeValidationError("redirect_cycle", origin)
            seen.add(current)
            current = redirects[current]


__all__ = [
    "ChangeValidationError",
    "EvidenceRecord",
    "claim_semantic_fingerprint",
    "require_acyclic_ancestry",
    "require_exact_keys",
    "require_no_redirect_cycle",
    "require_scope_not_wider",
]
