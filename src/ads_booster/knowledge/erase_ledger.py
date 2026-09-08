from __future__ import annotations

import json
import os
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Final, Literal, override

from pydantic import Field

from ads_booster.contracts.agent_run import BoundedId
from ads_booster.contracts.models import RelativePath, Sha256Digest
from ads_booster.knowledge.contract_types import KnowledgeContractModel, UtcDatetime

_DATABASE_NAME: Final = "erase-ledger.sqlite"
_DIRECTORY_MODE: Final = 0o700
_FILE_MODE: Final = 0o600
_ZERO_DIGEST: Final = "0" * 64


class EraseTarget(KnowledgeContractModel):
    kind: Literal["source", "page", "claim", "memory_document", "memory_entry", "transfer"]
    entity_id: BoundedId
    revision_id: BoundedId | None = None


class EraseArtifact(KnowledgeContractModel):
    kind: Annotated[str, Field(min_length=1, max_length=80)]
    entity_id: BoundedId
    revision_id: BoundedId | None = None
    relative_path: RelativePath | None = None
    content_sha256: Sha256Digest | None = None


class EraseLedgerEntry(KnowledgeContractModel):
    workspace_id: BoundedId
    sequence: Annotated[int, Field(ge=1)]
    request_id: BoundedId
    target: EraseTarget
    artifacts: Annotated[tuple[EraseArtifact, ...], Field(max_length=4096)] = ()
    manifest_sha256: Sha256Digest
    previous_entry_sha256: Sha256Digest
    entry_sha256: Sha256Digest
    created_at: UtcDatetime


class EraseLedgerExport(KnowledgeContractModel):
    workspace_id: BoundedId
    sequence: Annotated[int, Field(ge=0)]
    head_sha256: Sha256Digest
    entries: Annotated[tuple[EraseLedgerEntry, ...], Field(max_length=100_000)] = ()


class RestoreAuthorityReceipt(KnowledgeContractModel):
    workspace_id: BoundedId
    applied_through_sequence: Annotated[int, Field(ge=0)]
    head_sha256: Sha256Digest
    applied_entries: Annotated[int, Field(ge=0)]


@dataclass(slots=True)
class EraseLedgerError(Exception):
    code: str
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


@dataclass(frozen=True, slots=True)
class EraseLedger:
    control_root: Path

    @property
    def database_path(self) -> Path:
        return self.control_root / _DATABASE_NAME

    def initialize(self) -> Path:
        self._initialize_root()
        try:
            descriptor = os.open(
                self.database_path,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                _FILE_MODE,
            )
        except FileExistsError:
            descriptor = -1
        try:
            if descriptor >= 0:
                os.fchmod(descriptor, _FILE_MODE)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        self._require_database()
        with self._connection() as connection:
            _ = connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS erase_ledger_schema (
                    version INTEGER PRIMARY KEY CHECK(version=1),
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS erase_workspace_heads (
                    workspace_id TEXT PRIMARY KEY,
                    sequence INTEGER NOT NULL CHECK(sequence >= 0),
                    entry_sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS erase_entries (
                    workspace_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL CHECK(sequence >= 1),
                    request_id TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL,
                    previous_entry_sha256 TEXT NOT NULL,
                    entry_sha256 TEXT NOT NULL UNIQUE,
                    entry_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id,sequence),
                    UNIQUE(workspace_id,request_id)
                );
                INSERT OR IGNORE INTO erase_ledger_schema(version,applied_at)
                VALUES (1,strftime('%Y-%m-%dT%H:%M:%fZ','now'));
                """
            )
        return self.database_path

    def latest_sequence(self, workspace_id: str) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT sequence,entry_sha256 FROM erase_workspace_heads WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
        if row is None:
            return 0
        sequence = int(row[0])
        exported = self.export(workspace_id)
        if exported.sequence != sequence or exported.head_sha256 != str(row[1]):
            raise EraseLedgerError("erase_ledger_head_unverifiable", workspace_id)
        return sequence

    def append(
        self,
        *,
        workspace_id: str,
        request_id: str,
        target: EraseTarget,
        artifacts: tuple[EraseArtifact, ...],
        manifest_sha256: str,
        created_at: datetime,
    ) -> EraseLedgerEntry:
        with self._connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            replay = connection.execute(
                "SELECT entry_json FROM erase_entries WHERE workspace_id=? AND request_id=?",
                (workspace_id, request_id),
            ).fetchone()
            if replay is not None:
                entry = EraseLedgerEntry.model_validate_json(str(replay[0]))
                if entry.manifest_sha256 != manifest_sha256 or entry.target != target:
                    raise EraseLedgerError("erase_request_idempotency_conflict", request_id)
                return entry
            head = connection.execute(
                "SELECT sequence,entry_sha256 FROM erase_workspace_heads WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
            sequence = 1 if head is None else int(head[0]) + 1
            previous = _ZERO_DIGEST if head is None else str(head[1])
            unsigned = {
                "workspace_id": workspace_id,
                "sequence": sequence,
                "request_id": request_id,
                "target": target.model_dump(mode="json"),
                "artifacts": [item.model_dump(mode="json") for item in artifacts],
                "manifest_sha256": manifest_sha256,
                "previous_entry_sha256": previous,
                "created_at": created_at.isoformat(),
            }
            entry_digest = _canonical_sha256(unsigned)
            entry = EraseLedgerEntry(
                **unsigned,
                entry_sha256=entry_digest,
            )
            _ = connection.execute(
                """
                INSERT INTO erase_entries(
                    workspace_id,sequence,request_id,manifest_sha256,previous_entry_sha256,
                    entry_sha256,entry_json,created_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    workspace_id,
                    sequence,
                    request_id,
                    manifest_sha256,
                    previous,
                    entry_digest,
                    entry.model_dump_json(),
                    created_at.isoformat(),
                ),
            )
            _ = connection.execute(
                """
                INSERT INTO erase_workspace_heads(workspace_id,sequence,entry_sha256)
                VALUES (?,?,?) ON CONFLICT(workspace_id) DO UPDATE SET
                    sequence=excluded.sequence,entry_sha256=excluded.entry_sha256
                """,
                (workspace_id, sequence, entry_digest),
            )
        return entry

    def export(self, workspace_id: str) -> EraseLedgerExport:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT entry_json FROM erase_entries WHERE workspace_id=? ORDER BY sequence",
                (workspace_id,),
            ).fetchall()
            head = connection.execute(
                "SELECT sequence,entry_sha256 FROM erase_workspace_heads WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
        entries = tuple(EraseLedgerEntry.model_validate_json(str(row[0])) for row in rows)
        previous = _ZERO_DIGEST
        for expected_sequence, entry in enumerate(entries, start=1):
            unsigned = entry.model_dump(mode="json", exclude={"entry_sha256"})
            unsigned["created_at"] = entry.created_at.isoformat()
            if (
                entry.sequence != expected_sequence
                or entry.previous_entry_sha256 != previous
                or _canonical_sha256(unsigned) != entry.entry_sha256
            ):
                raise EraseLedgerError("erase_ledger_chain_unverifiable", workspace_id)
            previous = entry.entry_sha256
        sequence = len(entries)
        if head is None:
            if entries:
                raise EraseLedgerError("erase_ledger_head_missing", workspace_id)
            head_digest = _ZERO_DIGEST
        else:
            head_sequence = int(head[0])
            head_digest = str(head[1])
            if head_sequence != sequence or head_digest != previous:
                raise EraseLedgerError("erase_ledger_head_unverifiable", workspace_id)
        return EraseLedgerExport(
            workspace_id=workspace_id,
            sequence=sequence,
            head_sha256=head_digest,
            entries=entries,
        )

    def verify_restore_authority(
        self,
        workspace_id: str,
        observed_sequence: int,
        observed_head_sha256: str,
    ) -> None:
        current = self.export(workspace_id)
        if (
            current.sequence != observed_sequence
            or current.head_sha256 != observed_head_sha256
        ):
            raise EraseLedgerError("restore_erase_authority_stale", workspace_id)

    def apply_to_restored_target(
        self,
        repository: KnowledgeRepository,
        exported: EraseLedgerExport,
    ) -> RestoreAuthorityReceipt:
        self.verify_restore_authority(
            exported.workspace_id,
            exported.sequence,
            exported.head_sha256,
        )
        from ads_booster.knowledge.repository_deletion import apply_erase_entries

        applied = apply_erase_entries(repository, exported.entries)
        return RestoreAuthorityReceipt(
            workspace_id=exported.workspace_id,
            applied_through_sequence=exported.sequence,
            head_sha256=exported.head_sha256,
            applied_entries=applied,
        )

    def _initialize_root(self) -> None:
        if not self.control_root.is_absolute():
            raise EraseLedgerError("control_root_must_be_absolute", str(self.control_root))
        current = Path(self.control_root.anchor)
        for part in self.control_root.parts[1:]:
            current /= part
            if current.is_symlink():
                raise EraseLedgerError("control_root_symlink_forbidden", str(current))
        self.control_root.mkdir(mode=_DIRECTORY_MODE, parents=True, exist_ok=True)
        metadata = self.control_root.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise EraseLedgerError("control_root_not_directory", str(self.control_root))
        if stat.S_IMODE(metadata.st_mode) != _DIRECTORY_MODE:
            raise EraseLedgerError("control_root_permissions_unsafe", str(self.control_root))

    def _require_database(self) -> None:
        try:
            metadata = self.database_path.lstat()
        except FileNotFoundError as error:
            raise EraseLedgerError("erase_ledger_missing", str(self.database_path)) from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise EraseLedgerError("erase_ledger_type_unsafe", str(self.database_path))
        if stat.S_IMODE(metadata.st_mode) != _FILE_MODE:
            raise EraseLedgerError("erase_ledger_permissions_unsafe", str(self.database_path))

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection]:
        self._require_database()
        connection = sqlite3.connect(self.database_path, isolation_level=None, timeout=5)
        try:
            _ = connection.executescript(
                "PRAGMA busy_timeout=5000; PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;"
            )
            with connection:
                yield connection
        finally:
            connection.close()


def _canonical_sha256(value: dict[str, JsonValue]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()


type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

    from ads_booster.knowledge.repository_protocol import KnowledgeRepository


__all__ = [
    "EraseArtifact",
    "EraseLedger",
    "EraseLedgerEntry",
    "EraseLedgerError",
    "EraseLedgerExport",
    "EraseTarget",
    "RestoreAuthorityReceipt",
]
