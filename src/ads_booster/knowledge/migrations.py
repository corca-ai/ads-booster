from __future__ import annotations

import os
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Final, override

from pydantic import TypeAdapter

from ads_booster.knowledge.contract_types import ScopeKind
from ads_booster.knowledge.file_paths import fail
from ads_booster.knowledge.repository_identity import scope_key
from ads_booster.knowledge.schema_authority import AUTHORITY_SCHEMA
from ads_booster.knowledge.schema_channel import CHANNEL_SCHEMA
from ads_booster.knowledge.schema_content import CONTENT_SCHEMA
from ads_booster.knowledge.schema_deletion import DELETION_SCHEMA
from ads_booster.knowledge.schema_learning import LEARNING_SCHEMA
from ads_booster.knowledge.schema_personal import PERSONAL_SCHEMA
from ads_booster.knowledge.schema_skills import SKILL_SCHEMA
from ads_booster.knowledge.schema_work import WORK_SCHEMA
from ads_booster.knowledge.scope_contracts import AccessScope

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

_PRIVATE_FILE_MODE: Final = 0o600
_BASE_SCHEMA = AUTHORITY_SCHEMA + CONTENT_SCHEMA + WORK_SCHEMA
_V2_SCHEMA = _BASE_SCHEMA + DELETION_SCHEMA
_SKILL_SCHEMA_FULL = _V2_SCHEMA + SKILL_SCHEMA
_LEARNING_SCHEMA_FULL = _SKILL_SCHEMA_FULL + LEARNING_SCHEMA
_CHANNEL_SCHEMA_FULL = _LEARNING_SCHEMA_FULL + CHANNEL_SCHEMA
_SCHEMA = _CHANNEL_SCHEMA_FULL + PERSONAL_SCHEMA
_SCHEMA_VERSION: Final = 6
_SCHEMA_SHA256 = sha256(_SCHEMA.encode()).hexdigest()
_SCHEMA_ROWS: TypeAdapter[list[tuple[int, str]]] = TypeAdapter(list[tuple[int, str]])
_WORKSPACE_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])


@dataclass(frozen=True, slots=True)
class _Migration:
    version: int
    schema: str
    checksum: str
    initialize_channel_owners: bool = False


_DELETION = _Migration(2, DELETION_SCHEMA, sha256(_V2_SCHEMA.encode()).hexdigest())
_SKILLS = _Migration(3, SKILL_SCHEMA, sha256(_SKILL_SCHEMA_FULL.encode()).hexdigest())
_LEARNING = _Migration(4, LEARNING_SCHEMA, sha256(_LEARNING_SCHEMA_FULL.encode()).hexdigest())
_CHANNEL = _Migration(
    5,
    CHANNEL_SCHEMA,
    sha256(_CHANNEL_SCHEMA_FULL.encode()).hexdigest(),
    initialize_channel_owners=True,
)
_PERSONAL = _Migration(_SCHEMA_VERSION, PERSONAL_SCHEMA, _SCHEMA_SHA256)
_UPGRADES: Final[dict[tuple[int, str], tuple[_Migration, ...]]] = {
    (1, sha256(_BASE_SCHEMA.encode()).hexdigest()): (
        _DELETION,
        _SKILLS,
        _LEARNING,
        _CHANNEL,
        _PERSONAL,
    ),
    (2, _DELETION.checksum): (_SKILLS, _LEARNING, _CHANNEL, _PERSONAL),
    (3, _SKILLS.checksum): (_LEARNING, _CHANNEL, _PERSONAL),
    (4, _LEARNING.checksum): (_CHANNEL, _PERSONAL),
    (5, _CHANNEL.checksum): (_PERSONAL,),
    (6, _SCHEMA_SHA256): (),
    # Pre-merge channel/USER installations used v3/v4 independently of published main.
    (3, sha256((_V2_SCHEMA + CHANNEL_SCHEMA).encode()).hexdigest()): (
        _Migration(6, PERSONAL_SCHEMA + SKILL_SCHEMA + LEARNING_SCHEMA, _SCHEMA_SHA256),
    ),
    (4, sha256((_V2_SCHEMA + CHANNEL_SCHEMA + PERSONAL_SCHEMA).encode()).hexdigest()): (
        _Migration(6, SKILL_SCHEMA + LEARNING_SCHEMA, _SCHEMA_SHA256),
    ),
}


@dataclass(slots=True)
class KnowledgeSchemaError(Exception):
    code: str
    detail: str

    @override
    def __str__(self) -> str:
        """Render the stable storage error code and detail."""
        return f"{self.code}: {self.detail}"


def initialize_database(root: Path) -> Path:
    database_path = root / "index.sqlite"
    try:
        descriptor = os.open(
            database_path,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            _PRIVATE_FILE_MODE,
        )
    except FileExistsError:
        descriptor = -1
    try:
        if descriptor >= 0:
            os.fchmod(descriptor, _PRIVATE_FILE_MODE)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _require_private_database(database_path)
    with connect_database(database_path) as connection:
        _ = connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS knowledge_schema (
                version INTEGER PRIMARY KEY,
                checksum TEXT NOT NULL UNIQUE,
                applied_at TEXT NOT NULL
            );
            """
        )
        rows = _SCHEMA_ROWS.validate_python(
            connection.execute(
                "SELECT version,checksum FROM knowledge_schema ORDER BY version"
            ).fetchall(),
        )
        if not rows:
            _apply_migration(connection, _Migration(2, _V2_SCHEMA, _DELETION.checksum))
        identity = rows[-1] if rows else (2, _DELETION.checksum)
        upgrades = _UPGRADES.get(identity)
        if upgrades is None:
            code = "knowledge_schema_unsupported"
            raise KnowledgeSchemaError(code, str(identity[0]))
        for migration in upgrades:
            _apply_migration(connection, migration)
        _require_foreign_keys(connection)
    database_path.chmod(_PRIVATE_FILE_MODE)
    return database_path


def _apply_migration(connection: sqlite3.Connection, migration: _Migration) -> None:
    _ = connection.execute("PRAGMA foreign_keys=OFF")
    try:
        _ = connection.execute("BEGIN EXCLUSIVE")
        if migration.initialize_channel_owners:
            _initialize_channel_owners(connection)
        _execute_statements(connection, migration.schema)
        _require_foreign_keys(connection)
        _ = connection.execute(
            """INSERT INTO knowledge_schema(version,checksum,applied_at)
            VALUES (?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))""",
            (migration.version, migration.checksum),
        )
        connection.commit()
    except sqlite3.Error, KnowledgeSchemaError:
        connection.rollback()
        raise
    finally:
        _ = connection.execute("PRAGMA foreign_keys=ON")


def _initialize_channel_owners(connection: sqlite3.Connection) -> None:
    workspaces = _WORKSPACE_ROWS.validate_python(
        connection.execute("SELECT workspace_id FROM workspaces").fetchall()
    )
    for (workspace_id,) in workspaces:
        scope = AccessScope(kind=ScopeKind.WORKSPACE, workspace_id=workspace_id)
        _ = connection.execute(
            """INSERT OR IGNORE INTO access_scopes(
                scope_key,kind,workspace_id,member_id,session_id,scope_json
            ) VALUES (?,'workspace',?,NULL,NULL,?)""",
            (scope_key(scope), workspace_id, scope.model_dump_json()),
        )


def _execute_statements(connection: sqlite3.Connection, schema: str) -> None:
    statement = ""
    for line in schema.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            _ = connection.execute(statement)
            statement = ""


def _require_foreign_keys(connection: sqlite3.Connection) -> None:
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        code = "knowledge_schema_foreign_key_invalid"
        raise KnowledgeSchemaError(code, repr(violations))


def _require_private_database(path: Path) -> None:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        fail("knowledge_database_type_unsafe", str(path))
    if stat.S_IMODE(metadata.st_mode) != _PRIVATE_FILE_MODE:
        fail("knowledge_database_permissions_unsafe", str(path))


@contextmanager
def connect_database(database_path: Path) -> Generator[sqlite3.Connection]:
    connection = sqlite3.connect(database_path, isolation_level=None, timeout=5)
    try:
        connection.row_factory = sqlite3.Row
        _ = connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            PRAGMA busy_timeout = 5000;
            PRAGMA journal_mode = WAL;
            """
        )
        with connection:
            yield connection
    finally:
        connection.close()


__all__ = ["KnowledgeSchemaError", "connect_database", "initialize_database"]
