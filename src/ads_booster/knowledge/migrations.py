from __future__ import annotations

import os
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Final, override

from pydantic import TypeAdapter

from ads_booster.knowledge.file_paths import fail
from ads_booster.knowledge.schema_authority import AUTHORITY_SCHEMA
from ads_booster.knowledge.schema_content import CONTENT_SCHEMA
from ads_booster.knowledge.schema_deletion import DELETION_SCHEMA
from ads_booster.knowledge.schema_learning import LEARNING_SCHEMA
from ads_booster.knowledge.schema_skills import SKILL_SCHEMA
from ads_booster.knowledge.schema_work import WORK_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

_SCHEMA_VERSION: Final = 4
_PRIVATE_FILE_MODE: Final = 0o600
_BASE_SCHEMA = AUTHORITY_SCHEMA + CONTENT_SCHEMA + WORK_SCHEMA
_BASE_SCHEMA_SHA256 = sha256(_BASE_SCHEMA.encode()).hexdigest()
_DELETION_SCHEMA_VERSION: Final = 2
_DELETION_SCHEMA_FULL = _BASE_SCHEMA + DELETION_SCHEMA
_DELETION_SCHEMA_SHA256 = sha256(_DELETION_SCHEMA_FULL.encode()).hexdigest()
_SKILL_SCHEMA_VERSION: Final = 3
_SKILL_SCHEMA_FULL = _DELETION_SCHEMA_FULL + SKILL_SCHEMA
_SKILL_SCHEMA_SHA256 = sha256(_SKILL_SCHEMA_FULL.encode()).hexdigest()
_SCHEMA = _SKILL_SCHEMA_FULL + LEARNING_SCHEMA
_SCHEMA_SHA256 = sha256(_SCHEMA.encode()).hexdigest()
_SCHEMA_ROWS: TypeAdapter[list[tuple[int, str]]] = TypeAdapter(list[tuple[int, str]])


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
        if rows:
            version, checksum = rows[-1]
            if version == 1 and checksum == _BASE_SCHEMA_SHA256:
                _apply_deletion_schema(connection)
                _apply_skill_schema(connection)
                _apply_learning_schema(connection)
            elif version == _DELETION_SCHEMA_VERSION and checksum == _DELETION_SCHEMA_SHA256:
                _apply_skill_schema(connection)
                _apply_learning_schema(connection)
            elif version == _SKILL_SCHEMA_VERSION and checksum == _SKILL_SCHEMA_SHA256:
                _apply_learning_schema(connection)
            elif version != _SCHEMA_VERSION or checksum != _SCHEMA_SHA256:
                code = "knowledge_schema_unsupported"
                raise KnowledgeSchemaError(code, str(version))
        else:
            _apply_initial_schema(connection)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            code = "knowledge_schema_foreign_key_invalid"
            raise KnowledgeSchemaError(code, repr(violations))
    database_path.chmod(_PRIVATE_FILE_MODE)
    return database_path


def _apply_initial_schema(connection: sqlite3.Connection) -> None:
    migration = (
        "BEGIN EXCLUSIVE;\n"
        + _SCHEMA
        + "\nINSERT INTO knowledge_schema(version,checksum,applied_at) VALUES ("
        + f"{_SCHEMA_VERSION},'{_SCHEMA_SHA256}',strftime('%Y-%m-%dT%H:%M:%fZ','now'));\n"
        + "COMMIT;"
    )
    try:
        _ = connection.executescript(migration)
    except sqlite3.Error:
        if connection.in_transaction:
            connection.rollback()
        raise


def _apply_deletion_schema(connection: sqlite3.Connection) -> None:
    migration = (
        "BEGIN EXCLUSIVE;\n"
        + DELETION_SCHEMA
        + "\nINSERT INTO knowledge_schema(version,checksum,applied_at) VALUES ("
        + f"{_DELETION_SCHEMA_VERSION},'{_DELETION_SCHEMA_SHA256}',"
        + "strftime('%Y-%m-%dT%H:%M:%fZ','now'));\n"
        + "COMMIT;"
    )
    try:
        _ = connection.executescript(migration)
    except sqlite3.Error:
        if connection.in_transaction:
            connection.rollback()
        raise


def _apply_skill_schema(connection: sqlite3.Connection) -> None:
    migration = (
        "BEGIN EXCLUSIVE;\n"
        + SKILL_SCHEMA
        + "\nINSERT INTO knowledge_schema(version,checksum,applied_at) VALUES ("
        + f"{_SKILL_SCHEMA_VERSION},'{_SKILL_SCHEMA_SHA256}',"
        + "strftime('%Y-%m-%dT%H:%M:%fZ','now'));\n"
        + "COMMIT;"
    )
    try:
        _ = connection.executescript(migration)
    except sqlite3.Error:
        if connection.in_transaction:
            connection.rollback()
        raise


def _apply_learning_schema(connection: sqlite3.Connection) -> None:
    migration = (
        "BEGIN EXCLUSIVE;\n"
        + LEARNING_SCHEMA
        + "\nINSERT INTO knowledge_schema(version,checksum,applied_at) VALUES ("
        + f"{_SCHEMA_VERSION},'{_SCHEMA_SHA256}',strftime('%Y-%m-%dT%H:%M:%fZ','now'));\n"
        + "COMMIT;"
    )
    try:
        _ = connection.executescript(migration)
    except sqlite3.Error:
        if connection.in_transaction:
            connection.rollback()
        raise


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
