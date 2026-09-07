from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter

from ads_booster.knowledge.contracts import DependencyState
from ads_booster.knowledge.repository_source import _require_read

if TYPE_CHECKING:
    import sqlite3

    from ads_booster.knowledge.contracts import ActorContext
    from ads_booster.knowledge.repository_protocol import KnowledgeRepository
    from ads_booster.knowledge.repository_types import EvidenceDependencyInvalidation

_STRING = TypeAdapter(str)


def append_dependency_invalidations(
    connection: sqlite3.Connection,
    workspace_id: str,
    operation_id: str,
    invalidations: tuple[EvidenceDependencyInvalidation, ...],
) -> None:
    for invalidation in invalidations:
        _ = connection.execute(
            """
            INSERT INTO evidence_dependency_invalidations(
                workspace_id,upstream_kind,upstream_id,upstream_revision_id,
                operation_id,resulting_state,reason
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                workspace_id,
                invalidation.upstream_kind,
                invalidation.upstream_id,
                invalidation.upstream_revision_id,
                operation_id,
                invalidation.resulting_state.value,
                invalidation.reason,
            ),
        )
    _append_current_claim_fences(connection, workspace_id)


def _append_current_claim_fences(
    connection: sqlite3.Connection,
    workspace_id: str,
) -> None:
    _ = connection.execute(
        """
        WITH RECURSIVE reachable(
            upstream_kind,upstream_id,upstream_revision_id,invalidation_operation_id,
            resulting_state,reason,node_id
        ) AS (
            SELECT invalidation.upstream_kind,invalidation.upstream_id,
                invalidation.upstream_revision_id,invalidation.operation_id,
                invalidation.resulting_state,invalidation.reason,node.node_id
            FROM evidence_dependency_invalidations AS invalidation
            JOIN evidence_nodes AS node
            ON node.workspace_id=invalidation.workspace_id
            AND node.entity_kind=invalidation.upstream_kind
            AND node.entity_id=invalidation.upstream_id
            AND node.revision_id=invalidation.upstream_revision_id
            WHERE invalidation.workspace_id=?
            UNION
            SELECT reachable.upstream_kind,reachable.upstream_id,
                reachable.upstream_revision_id,reachable.invalidation_operation_id,
                reachable.resulting_state,reachable.reason,edge.from_node_id
            FROM reachable
            JOIN evidence_edges AS edge ON edge.to_node_id=reachable.node_id
        )
        INSERT OR IGNORE INTO claim_visibility_fences(
            workspace_id,dependent_claim_id,dependent_revision_id,
            upstream_kind,upstream_id,upstream_revision_id,operation_id,
            resulting_state,reason
        )
        SELECT node.workspace_id,node.entity_id,node.revision_id,
            reachable.upstream_kind,reachable.upstream_id,reachable.upstream_revision_id,
            reachable.invalidation_operation_id,reachable.resulting_state,reachable.reason
        FROM reachable
        JOIN evidence_nodes AS node ON node.node_id=reachable.node_id
        JOIN claim_locations AS location
        ON location.workspace_id=node.workspace_id
        AND location.claim_id=node.entity_id
        AND location.claim_revision_id=node.revision_id
        WHERE node.entity_kind='claim' AND location.is_current=1
        """,
        (workspace_id,),
    )


def claim_dependency_state(
    repository: KnowledgeRepository,
    actor: ActorContext,
    claim_id: str,
    revision_id: str,
) -> DependencyState | None:
    with repository.connection() as connection:
        _require_read(connection, actor)
        row = cast(
            "tuple[object, ...] | None",
            connection.execute(
                """
                SELECT fence.resulting_state FROM claim_visibility_fences AS fence
                JOIN claim_locations AS location
                ON location.workspace_id=fence.workspace_id
                AND location.claim_id=fence.dependent_claim_id
                AND location.claim_revision_id=fence.dependent_revision_id
                WHERE fence.workspace_id=? AND fence.dependent_claim_id=?
                AND fence.dependent_revision_id=? AND location.is_current=1
                ORDER BY CASE fence.resulting_state WHEN 'restricted' THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (actor.workspace_id, claim_id, revision_id),
            ).fetchone(),
        )
    return None if row is None else DependencyState(_STRING.validate_python(row[0]))


__all__ = ["append_dependency_invalidations", "claim_dependency_state"]
