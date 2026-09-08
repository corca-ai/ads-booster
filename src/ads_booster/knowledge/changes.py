from __future__ import annotations

# ruff: noqa: EM101, TC001
from hashlib import sha256
from typing import TYPE_CHECKING, Never, assert_never, cast

from ads_booster.knowledge.change_publication import (
    ChangeGroup,
    ChangePublisher,
    MemoryPublication,
)
from ads_booster.knowledge.change_validation import (
    ChangeValidationError,
    claim_semantic_fingerprint,
)
from ads_booster.knowledge.contract_types import AuthorityClass
from ads_booster.knowledge.governance_contracts import ConstraintBinding
from ads_booster.knowledge.operation_contracts import ChangeImpact, SemanticFingerprint
from ads_booster.knowledge.operation_enums import ChangeImpactKind, ConstraintCompatibility

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ads_booster.knowledge.wiki_contracts import Claim


def classify_claim_changes(
    *,
    previous: tuple[Claim, ...],
    current: tuple[Claim, ...],
    dependency_ids: Mapping[str, tuple[str, ...]],
) -> ChangeImpact:
    previous_by_id = {claim.claim_id: claim for claim in previous}
    current_by_id = {claim.claim_id: claim for claim in current}
    changed_ids = tuple(
        sorted(
            claim_id
            for claim_id in previous_by_id.keys() | current_by_id.keys()
            if _fingerprint(previous_by_id.get(claim_id))
            != _fingerprint(current_by_id.get(claim_id))
        )
    )
    if not changed_ids:
        return ChangeImpact(
            kind=ChangeImpactKind.DISPLAY_ONLY,
            affected_dependency_ids=(),
            reason="Structured claim semantics and evidence are unchanged.",
        )
    affected = tuple(
        dict.fromkeys(
            dependency_id
            for claim_id in changed_ids
            for dependency_id in dependency_ids.get(claim_id, ())
        )
    )
    first_id = changed_ids[0]
    return ChangeImpact(
        kind=ChangeImpactKind.SEMANTIC_OR_UNKNOWN,
        previous_fingerprint=_contract_fingerprint(previous_by_id.get(first_id)),
        current_fingerprint=_contract_fingerprint(current_by_id.get(first_id)),
        affected_dependency_ids=affected,
        reason="A claim statement, evidence, applicability, kind, or status changed.",
    )


def resolve_constraints(bindings: tuple[ConstraintBinding, ...]) -> tuple[ConstraintBinding, ...]:
    by_id = {binding.constraint_id: binding for binding in bindings}
    if len(by_id) != len(bindings):
        raise ChangeValidationError("constraint_ids_not_unique")
    superseded: set[str] = set()
    _resolve_overrides(bindings, by_id, superseded)
    _resolve_supersessions(bindings, by_id, superseded)
    active = tuple(binding for binding in bindings if binding.constraint_id not in superseded)
    for index, binding in enumerate(active):
        if binding.compatibility is ConstraintCompatibility.COMPATIBLE:
            continue
        if any(_applies_overlap(binding, other) for other in active[index + 1 :]) or any(
            _applies_overlap(binding, other) for other in active[:index]
        ):
            raise ChangeValidationError("constraint_conflict", binding.constraint_id)
    return active


def _resolve_overrides(
    bindings: tuple[ConstraintBinding, ...],
    by_id: dict[str, ConstraintBinding],
    superseded: set[str],
) -> None:
    for binding in bindings:
        for target_id in binding.overrides_ids:
            target = by_id.get(target_id)
            if target is None:
                raise ChangeValidationError("constraint_override_target_missing", target_id)
            if _authority_rank(binding.authority_class) < _authority_rank(target.authority_class):
                raise ChangeValidationError(
                    "constraint_authority_insufficient", binding.constraint_id
                )
            if not _applies_narrower(binding, target):
                raise ChangeValidationError(
                    "constraint_override_scope_not_narrower", binding.constraint_id
                )
            superseded.add(target_id)


def _resolve_supersessions(
    bindings: tuple[ConstraintBinding, ...],
    by_id: dict[str, ConstraintBinding],
    superseded: set[str],
) -> None:
    for binding in bindings:
        for target_id in binding.supersedes_ids:
            target = by_id.get(target_id)
            if target is None:
                raise ChangeValidationError("constraint_supersession_target_missing", target_id)
            if _authority_rank(binding.authority_class) < _authority_rank(target.authority_class):
                raise ChangeValidationError(
                    "constraint_authority_insufficient", binding.constraint_id
                )
            if not _applies_overlap(binding, target):
                raise ChangeValidationError(
                    "constraint_supersession_scope_mismatch", binding.constraint_id
                )
            superseded.add(target_id)


def _fingerprint(claim: Claim | None) -> str | None:
    if claim is None:
        return None
    return claim_semantic_fingerprint(claim)


def _contract_fingerprint(claim: Claim | None) -> SemanticFingerprint | None:
    if claim is None:
        return None
    statement = sha256(claim.statement.encode()).hexdigest()
    evidence = sha256(
        "\n".join(
            sorted(
                ":".join(
                    (
                        item.evidence_kind,
                        item.evidence_id,
                        item.revision_id,
                        str(item.segment_id),
                        str(item.quote_sha256),
                    )
                )
                for item in (*claim.evidence_refs, *claim.counter_evidence_refs)
            )
        ).encode()
    ).hexdigest()
    applicability = sha256(
        (
            claim.applicability.model_dump_json() if claim.applicability is not None else "null"
        ).encode()
    ).hexdigest()
    return SemanticFingerprint(
        claim_id=claim.claim_id,
        statement_sha256=statement,
        evidence_sha256=evidence,
        applicability_sha256=applicability,
        combined_sha256=claim_semantic_fingerprint(claim),
    )


def _authority_rank(authority: AuthorityClass) -> int:
    authority_value = cast("AuthorityClass | str", authority)
    match authority_value:
        case AuthorityClass.RUNTIME_POLICY:
            return 3
        case AuthorityClass.DELEGATED_TEAM_RULE:
            return 2
        case AuthorityClass.AUTHORIZED_TASK_INSTRUCTION:
            return 1
        case _ as unreachable:
            assert_never(cast("Never", unreachable))


def _applies_overlap(first: ConstraintBinding, second: ConstraintBinding) -> bool:
    first_actions = set(first.applies_to.action_kinds)
    second_actions = set(second.applies_to.action_kinds)
    actions_overlap = (
        not first_actions or not second_actions or bool(first_actions & second_actions)
    )
    task_overlap = (
        first.applies_to.task_ref is None
        or second.applies_to.task_ref is None
        or first.applies_to.task_ref == second.applies_to.task_ref
    )
    subject_overlap = (
        first.applies_to.subject_key is None
        or second.applies_to.subject_key is None
        or first.applies_to.subject_key == second.applies_to.subject_key
    )
    products_first = set(first.applies_to.product_refs)
    products_second = set(second.applies_to.product_refs)
    products_overlap = (
        not products_first or not products_second or bool(products_first & products_second)
    )
    return actions_overlap and task_overlap and subject_overlap and products_overlap


def _applies_narrower(override: ConstraintBinding, target: ConstraintBinding) -> bool:
    override_actions = set(override.applies_to.action_kinds)
    target_actions = set(target.applies_to.action_kinds)
    actions_narrower = not target_actions or (
        bool(override_actions) and override_actions <= target_actions
    )
    task_narrower = target.applies_to.task_ref is None or (
        override.applies_to.task_ref == target.applies_to.task_ref
    )
    subject_narrower = target.applies_to.subject_key is None or (
        override.applies_to.subject_key == target.applies_to.subject_key
    )
    override_products = set(override.applies_to.product_refs)
    target_products = set(target.applies_to.product_refs)
    products_narrower = not target_products or (
        bool(override_products) and override_products <= target_products
    )
    strictly_narrower = override.applies_to != target.applies_to and any(
        (
            override.applies_to.task_ref is not None,
            override.applies_to.subject_key is not None,
            bool(override_actions),
            bool(override_products),
        )
    )
    return (
        actions_narrower
        and task_narrower
        and subject_narrower
        and products_narrower
        and strictly_narrower
    )


__all__ = [
    "ChangeGroup",
    "ChangePublisher",
    "MemoryPublication",
    "classify_claim_changes",
    "resolve_constraints",
]
