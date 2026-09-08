"""Trusted current-file and lineage checks at production/publication review boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ads_booster.contracts.marketing_delivery import ReviewAsset

if TYPE_CHECKING:
    from ads_booster.contracts.creative_work import CreativeScope
    from ads_booster.marketing.agent_service.creative_assets import SqliteCreativeAssetRepository

_MAX_LINEAGE = 128


@dataclass(frozen=True, slots=True)
class CreativeAssetVerifier:
    repository: SqliteCreativeAssetRepository

    def verify(self, scope: CreativeScope, refs: tuple[ReviewAsset, ...]) -> None:
        """Check bytes and every current ancestor; this does not certify visual QA."""
        pending = list(refs)
        visited: set[tuple[str, int, str]] = set()
        while pending:
            reference = pending.pop()
            key = (reference.asset_id, reference.revision, reference.sha256)
            if key in visited:
                continue
            visited.add(key)
            if len(visited) > _MAX_LINEAGE:
                raise ValueError("delivery_asset_lineage_limit")
            current = self.repository.get(scope, reference.asset_id)
            if current is None:
                raise ValueError("delivery_asset_missing")
            if current.revision != reference.revision or current.sha256 != reference.sha256:
                raise ValueError("delivery_asset_not_current")
            if self.repository.is_stale(scope, current.asset_id, current.revision):
                raise ValueError("delivery_asset_stale")
            pending.extend(
                ReviewAsset.model_validate(parent.model_dump()) for parent in current.parents
            )
