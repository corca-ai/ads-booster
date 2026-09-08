from __future__ import annotations

import unicodedata
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.contracts import KnowledgeRevision, WikiPage
from ads_booster.knowledge.file_store import KnowledgeRevisionTarget, PublishedRevisionFile
from ads_booster.knowledge.grant_policy import authorize_read
from ads_booster.knowledge.repository_evidence import insert_claim
from ads_booster.knowledge.repository_identity import scope_key
from ads_booster.knowledge.repository_source import _require_read
from ads_booster.knowledge.repository_types import (
    PageRevisionWrite,
    StoredPage,
    conflict,
)

if TYPE_CHECKING:
    import sqlite3

    from ads_booster.knowledge.contracts import ActorContext
    from ads_booster.knowledge.repository_protocol import KnowledgeRepository

_STRING = TypeAdapter(str)
_INTEGER = TypeAdapter(int)


def assert_page_head(
    connection: sqlite3.Connection, workspace_id: str, write: PageRevisionWrite
) -> None:
    row = cast(
        "tuple[object, ...] | None",
        connection.execute(
            "SELECT revision_id FROM knowledge_heads WHERE workspace_id=? AND page_id=?",
            (workspace_id, write.page.page_id),
        ).fetchone(),
    )
    current = None if row is None else _STRING.validate_python(row[0])
    if current != write.expected.expected_revision_id:
        conflict("knowledge_head_conflict", write.page.page_id)
    if (
        write.expected.entity_id != write.page.page_id
        or write.expected.resulting_revision_id != write.revision.revision_id
        or write.page.current_revision_id != write.revision.revision_id
        or write.revision.page_id != write.page.page_id
        or write.revision.previous_revision_id != write.expected.expected_revision_id
    ):
        conflict("knowledge_write_binding_conflict", write.page.page_id)


def insert_page_shell(
    connection: sqlite3.Connection, workspace_id: str, write: PageRevisionWrite
) -> None:
    if write.expected.expected_revision_id is None:
        _ = connection.execute(
            """
            INSERT INTO wiki_pages(workspace_id,page_id,scope_key,status,page_json)
            VALUES (?,?,?,?,?)
            """,
            (
                workspace_id,
                write.page.page_id,
                scope_key(write.page.scope),
                write.page.status.value,
                write.page.model_dump_json(),
            ),
        )


def insert_page_revision(
    connection: sqlite3.Connection,
    workspace_id: str,
    operation_id: str,
    write: PageRevisionWrite,
    published: PublishedRevisionFile,
) -> None:
    expected_target = KnowledgeRevisionTarget(
        page_id=write.page.page_id,
        revision_id=write.revision.revision_id,
    )
    if published.target != expected_target or published.sha256 != write.revision.body_sha256:
        conflict("knowledge_file_binding_conflict", write.page.page_id)
    attributes_digest = contract_sha256(
        {"attributes": [item.model_dump(mode="json") for item in write.revision.attributes]}
    )
    _ = connection.execute(
        """
        INSERT INTO knowledge_revisions(
            workspace_id,page_id,revision_id,previous_revision_id,body_sha256,
            relative_path,title,attributes_sha256,revision_json,operation_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            workspace_id,
            write.page.page_id,
            write.revision.revision_id,
            write.revision.previous_revision_id,
            write.revision.body_sha256,
            published.relative_path,
            write.revision.title,
            attributes_digest,
            write.revision.model_dump_json(),
            operation_id,
        ),
    )
    for alias in write.revision.aliases:
        _ = connection.execute(
            """
            INSERT INTO page_aliases(
                workspace_id,page_id,revision_id,alias,normalized_alias
            ) VALUES (?,?,?,?,?)
            """,
            (
                workspace_id,
                write.page.page_id,
                write.revision.revision_id,
                alias,
                unicodedata.normalize("NFKC", alias).casefold(),
            ),
        )
    for claim in write.revision.claims:
        insert_claim(
            connection,
            workspace_id,
            write.page.page_id,
            write.revision.revision_id,
            claim,
        )
    for relation in write.revision.relations:
        _ = connection.execute(
            """
            INSERT INTO page_relations(
                workspace_id,relation_id,from_page_id,from_revision_id,to_page_id,kind,relation_json
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                workspace_id,
                relation.relation_id,
                relation.from_page_id,
                write.revision.revision_id,
                relation.to_page_id,
                relation.kind.value,
                relation.model_dump_json(),
            ),
        )
    previous_sequence = cast(
        "tuple[object, ...] | None",
        connection.execute(
            "SELECT head_sequence FROM knowledge_heads WHERE workspace_id=? AND page_id=?",
            (workspace_id, write.page.page_id),
        ).fetchone(),
    )
    sequence = (
        1 if previous_sequence is None else _INTEGER.validate_python(previous_sequence[0]) + 1
    )
    _ = connection.execute(
        """
        INSERT INTO knowledge_heads(workspace_id,page_id,revision_id,head_sequence,operation_id)
        VALUES (?,?,?,?,?)
        ON CONFLICT(workspace_id,page_id) DO UPDATE SET
            revision_id=excluded.revision_id,
            head_sequence=excluded.head_sequence,
            operation_id=excluded.operation_id
        """,
        (workspace_id, write.page.page_id, write.revision.revision_id, sequence, operation_id),
    )
    _ = connection.execute(
        """
        UPDATE wiki_pages SET scope_key=?,status=?,page_json=?
        WHERE workspace_id=? AND page_id=?
        """,
        (
            scope_key(write.page.scope),
            write.page.status.value,
            write.page.model_dump_json(),
            workspace_id,
            write.page.page_id,
        ),
    )


def page_head(
    repository: KnowledgeRepository,
    actor: ActorContext,
    page_id: str,
) -> str | None:
    with repository.connection() as connection:
        _require_read(connection, actor)
        row = cast(
            "tuple[object, ...] | None",
            connection.execute(
                """
                SELECT head.revision_id,page.page_json FROM knowledge_heads AS head
                JOIN wiki_pages AS page USING(workspace_id,page_id)
                WHERE head.workspace_id=? AND head.page_id=?
                """,
                (actor.workspace_id, page_id),
            ).fetchone(),
        )
    if row is None:
        return None
    page = WikiPage.model_validate_json(_STRING.validate_python(row[1]))
    _ = authorize_read(actor=actor, target_scope=page.scope, at=datetime.now(UTC))
    return _STRING.validate_python(row[0])


def resolve_page_id(
    repository: KnowledgeRepository,
    actor: ActorContext,
    page_id: str,
) -> str:
    seen: set[str] = set()
    current = page_id
    with repository.connection() as connection:
        _require_read(connection, actor)
        while current not in seen:
            seen.add(current)
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    """
                SELECT page.page_json,redirect.to_page_id
                FROM wiki_pages AS page
                LEFT JOIN page_redirects AS redirect
                ON redirect.workspace_id=page.workspace_id
                AND redirect.from_page_id=page.page_id
                WHERE page.workspace_id=? AND page.page_id=?
                """,
                    (actor.workspace_id, current),
                ).fetchone(),
            )
            if row is None:
                return current
            page = WikiPage.model_validate_json(_STRING.validate_python(row[0]))
            _ = authorize_read(actor=actor, target_scope=page.scope, at=datetime.now(UTC))
            if row[1] is None:
                return current
            current = _STRING.validate_python(row[1])
    return conflict("page_redirect_cycle", page_id)


def read_page(
    repository: KnowledgeRepository,
    actor: ActorContext,
    page_id: str,
    revision_id: str | None,
) -> StoredPage | None:
    selected_page_id = (
        page_id if revision_id is not None else resolve_page_id(repository, actor, page_id)
    )
    with repository.connection() as connection:
        _require_read(connection, actor)
        selected_revision = revision_id
        if selected_revision is None:
            head = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    "SELECT revision_id FROM knowledge_heads WHERE workspace_id=? AND page_id=?",
                    (actor.workspace_id, selected_page_id),
                ).fetchone(),
            )
            if head is None:
                return None
            selected_revision = _STRING.validate_python(head[0])
        blocked_claim = connection.execute(
            """
            SELECT 1 FROM claim_locations AS location
            JOIN tombstones AS tomb ON tomb.workspace_id=location.workspace_id
                AND tomb.target_kind='claim' AND tomb.target_id=location.claim_id
                AND (tomb.target_revision_id=''
                    OR tomb.target_revision_id=location.claim_revision_id)
            WHERE location.workspace_id=? AND location.page_id=?
                AND location.page_revision_id=?
                AND tomb.state IN ('blocked','purge_pending','purged') LIMIT 1
            """,
            (actor.workspace_id, selected_page_id, selected_revision),
        ).fetchone()
        if blocked_claim is not None:
            return None
        row = cast(
            "tuple[object, ...] | None",
            connection.execute(
                """
                SELECT page.page_json,revision.revision_json,revision.body_sha256
                FROM wiki_pages AS page
                JOIN knowledge_revisions AS revision USING(workspace_id,page_id)
                WHERE page.workspace_id=? AND page.page_id=? AND revision.revision_id=?
                AND NOT EXISTS (
                    SELECT 1 FROM tombstones AS tomb
                    WHERE tomb.workspace_id=page.workspace_id AND tomb.target_id=page.page_id
                    AND tomb.state IN ('blocked','purge_pending','purged')
                )
                """,
                (actor.workspace_id, selected_page_id, selected_revision),
            ).fetchone(),
        )
    if row is None:
        return None
    page = WikiPage.model_validate_json(_STRING.validate_python(row[0]))
    _ = authorize_read(actor=actor, target_scope=page.scope, at=datetime.now(UTC))
    revision = KnowledgeRevision.model_validate_json(_STRING.validate_python(row[1]))
    if revision_id is None:
        with repository.connection() as connection:
            fenced_rows = cast(
                "list[tuple[object, ...]]",
                connection.execute(
                    """
                    SELECT dependent_claim_id FROM claim_visibility_fences
                    WHERE workspace_id=? AND dependent_revision_id=?
                    """,
                    (actor.workspace_id, revision.revision_id),
                ).fetchall(),
            )
        fenced_claim_ids = {_STRING.validate_python(item[0]) for item in fenced_rows}
        revision = revision.model_copy(
            update={
                "claims": tuple(
                    claim for claim in revision.claims if claim.claim_id not in fenced_claim_ids
                )
            }
        )
    target = KnowledgeRevisionTarget(page_id=selected_page_id, revision_id=revision.revision_id)
    published = repository.files.published(target, _STRING.validate_python(row[2]))
    return StoredPage(page=page, revision=revision, body=repository.files.read(published))


__all__ = [
    "assert_page_head",
    "insert_page_revision",
    "insert_page_shell",
    "page_head",
    "read_page",
    "resolve_page_id",
]
