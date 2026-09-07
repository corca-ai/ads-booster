from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.repository_identity import scope_key

if TYPE_CHECKING:
    import sqlite3

    from ads_booster.knowledge.contracts import Claim, EvidenceRef, MemoryEntry


def insert_claim(
    connection: sqlite3.Connection,
    workspace_id: str,
    page_id: str,
    revision_id: str,
    claim: Claim,
) -> None:
    _ = connection.execute(
        "INSERT OR IGNORE INTO claims(workspace_id,claim_id,kind) VALUES (?,?,?)",
        (workspace_id, claim.claim_id, claim.kind.value),
    )
    evidence_digest = contract_sha256(
        {
            "evidence": [item.model_dump(mode="json") for item in claim.evidence_refs],
            "counter_evidence": [
                item.model_dump(mode="json") for item in claim.counter_evidence_refs
            ],
        }
    )
    applicability_digest = contract_sha256(
        {} if claim.applicability is None else claim.applicability.model_dump(mode="json")
    )
    _ = connection.execute(
        """
        INSERT INTO claim_versions(
            workspace_id,claim_id,revision_id,status,statement_sha256,
            applicability_sha256,evidence_sha256,claim_json
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            workspace_id,
            claim.claim_id,
            revision_id,
            claim.status.value,
            sha256(claim.statement.encode()).hexdigest(),
            applicability_digest,
            evidence_digest,
            claim.model_dump_json(),
        ),
    )
    _ = connection.execute(
        "UPDATE claim_locations SET is_current=0 WHERE workspace_id=? AND claim_id=?",
        (workspace_id, claim.claim_id),
    )
    _ = connection.execute(
        """
        INSERT INTO claim_locations(
            workspace_id,claim_id,claim_revision_id,page_id,page_revision_id,is_current
        ) VALUES (?,?,?,?,?,1)
        """,
        (workspace_id, claim.claim_id, revision_id, page_id, revision_id),
    )
    owner_node = _insert_node(
        connection,
        EvidenceNodeWrite(
            workspace_id=workspace_id,
            entity_kind="claim",
            entity_id=claim.claim_id,
            revision_id=revision_id,
            segment_id="",
            scope_json=claim.evidence_refs[0].scope.model_dump_json(),
            owner_scope_key=scope_key(claim.evidence_refs[0].scope),
        ),
    )
    for evidence in claim.evidence_refs:
        _insert_edge(connection, workspace_id, owner_node, evidence, "supports")
    for evidence in claim.counter_evidence_refs:
        _insert_edge(connection, workspace_id, owner_node, evidence, "counters")


def insert_memory_entry(
    connection: sqlite3.Connection,
    workspace_id: str,
    memory_revision_id: str,
    entry: MemoryEntry,
) -> None:
    _ = connection.execute(
        """
        INSERT INTO memory_entries(
            workspace_id,document_id,memory_revision_id,entry_id,kind,status,
            dependency_state,origin,usage_role,soul_section,scope_key,entry_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            workspace_id,
            entry.document_id,
            memory_revision_id,
            entry.entry_id,
            entry.kind.value,
            entry.status.value,
            entry.dependency_state.value,
            entry.origin.value,
            entry.usage_role.value,
            None if entry.soul_section is None else entry.soul_section.value,
            scope_key(entry.scope),
            entry.model_dump_json(),
        ),
    )
    owner_node = _insert_node(
        connection,
        EvidenceNodeWrite(
            workspace_id=workspace_id,
            entity_kind="memory_entry",
            entity_id=entry.entry_id,
            revision_id=memory_revision_id,
            segment_id="",
            scope_json=entry.scope.model_dump_json(),
            owner_scope_key=scope_key(entry.scope),
        ),
    )
    for evidence in entry.source_refs:
        _insert_edge(connection, workspace_id, owner_node, evidence, "supports")
        _ = connection.execute(
            """
            INSERT INTO derived_memory_refs(
                workspace_id,document_id,memory_revision_id,entry_id,
                upstream_kind,upstream_id,upstream_revision_id,semantic_fingerprint
            ) VALUES (?,?,?,?,?,?,?,NULL)
            """,
            (
                workspace_id,
                entry.document_id,
                memory_revision_id,
                entry.entry_id,
                _upstream_kind(evidence.evidence_kind.value),
                evidence.evidence_id,
                evidence.revision_id,
            ),
        )
    if entry.wiki_ref is not None:
        _ = connection.execute(
            """
            INSERT INTO derived_memory_refs(
                workspace_id,document_id,memory_revision_id,entry_id,
                upstream_kind,upstream_id,upstream_revision_id,semantic_fingerprint
            ) VALUES (?,?,?,?,'wiki_claim',?,?,?)
            """,
            (
                workspace_id,
                entry.document_id,
                memory_revision_id,
                entry.entry_id,
                entry.wiki_ref.claim_id,
                entry.wiki_ref.revision_id,
                entry.wiki_ref.semantic_fingerprint,
            ),
        )


def _insert_edge(
    connection: sqlite3.Connection,
    workspace_id: str,
    owner_node: str,
    evidence: EvidenceRef,
    edge_kind: str,
) -> None:
    target_node = _insert_node(
        connection,
        EvidenceNodeWrite(
            workspace_id=workspace_id,
            entity_kind=evidence.evidence_kind.value,
            entity_id=evidence.evidence_id,
            revision_id=evidence.revision_id,
            segment_id=evidence.segment_id or "",
            scope_json=evidence.scope.model_dump_json(),
            owner_scope_key=scope_key(evidence.scope),
        ),
    )
    _ = connection.execute(
        """
        INSERT OR IGNORE INTO evidence_edges(
            from_node_id,to_node_id,edge_kind,quote_sha256
        ) VALUES (?,?,?,?)
        """,
        (owner_node, target_node, edge_kind, evidence.quote_sha256),
    )


@dataclass(frozen=True, slots=True)
class EvidenceNodeWrite:
    workspace_id: str
    entity_kind: str
    entity_id: str
    revision_id: str
    segment_id: str
    scope_json: str
    owner_scope_key: str


def _insert_node(
    connection: sqlite3.Connection,
    node: EvidenceNodeWrite,
) -> str:
    workspace_id = node.workspace_id
    entity_kind = node.entity_kind
    entity_id = node.entity_id
    revision_id = node.revision_id
    segment_id = node.segment_id
    node_id = sha256(
        f"{workspace_id}\x1f{entity_kind}\x1f{entity_id}\x1f{revision_id}\x1f{segment_id}".encode()
    ).hexdigest()
    _ = connection.execute(
        """
        INSERT OR IGNORE INTO access_scopes(
            scope_key,kind,workspace_id,member_id,session_id,scope_json
        ) SELECT ?,kind,workspace_id,member_id,session_id,? FROM access_scopes WHERE scope_key=?
        """,
        (node.owner_scope_key, node.scope_json, node.owner_scope_key),
    )
    _ = connection.execute(
        """
        INSERT OR IGNORE INTO evidence_nodes(
            node_id,workspace_id,scope_key,entity_kind,entity_id,revision_id,segment_id
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (
            node_id,
            workspace_id,
            node.owner_scope_key,
            entity_kind,
            entity_id,
            revision_id,
            segment_id,
        ),
    )
    return node_id


def _upstream_kind(evidence_kind: str) -> str:
    return "memory_entry" if evidence_kind == "memory_entry" else "source"


__all__ = ["insert_claim", "insert_memory_entry"]
