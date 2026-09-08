from __future__ import annotations

# ruff: noqa: D107, EM101
from typing import TYPE_CHECKING

from ads_booster.knowledge.change_validation import ChangeValidationError
from ads_booster.knowledge.contract_types import EvidenceKind

if TYPE_CHECKING:
    from ads_booster.knowledge.evidence_contracts import EvidenceRef
    from ads_booster.knowledge.memory import MemorySnapshot
    from ads_booster.knowledge.pages import PageSnapshot
    from ads_booster.knowledge.repository_resolution import ResolvedEvidence


class PlannedEvidenceOverlay:
    def __init__(
        self,
        pages: tuple[PageSnapshot, ...],
        memories: tuple[MemorySnapshot, ...],
    ) -> None:
        values: dict[tuple[EvidenceKind, str, str], ResolvedEvidence] = {}
        for snapshot in pages:
            for claim in snapshot.revision.claims:
                self._add(
                    values,
                    (EvidenceKind.CLAIM, claim.claim_id, snapshot.revision.revision_id),
                    claim,
                )
        for snapshot in memories:
            for entry in snapshot.entries:
                self._add(
                    values,
                    (EvidenceKind.MEMORY_ENTRY, entry.entry_id, snapshot.revision.revision_id),
                    entry,
                )
        self._values: dict[tuple[EvidenceKind, str, str], ResolvedEvidence] = values

    @staticmethod
    def _add(
        values: dict[tuple[EvidenceKind, str, str], ResolvedEvidence],
        key: tuple[EvidenceKind, str, str],
        value: ResolvedEvidence,
    ) -> None:
        if key in values:
            raise ChangeValidationError("planned_evidence_not_unique", key[1])
        values[key] = value

    def resolve(self, ref: EvidenceRef) -> ResolvedEvidence | None:
        return self._values.get((ref.evidence_kind, ref.evidence_id, ref.revision_id))


__all__ = ["PlannedEvidenceOverlay"]
