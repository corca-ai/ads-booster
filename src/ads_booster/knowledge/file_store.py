from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from ads_booster.knowledge.file_paths import (
    KnowledgeFileStoreError,
    KnowledgeRevisionTarget,
    MemoryRevisionTarget,
    PreparedRevisionFile,
    PublishedRevisionFile,
    RevisionFileDraft,
    RevisionTarget,
    SkillRevisionTarget,
    SourceFileKind,
    SourceRevisionTarget,
    bounded_id,
    digest,
    fail,
    revision_relative_path,
    skill_display_relative_path,
    store_error,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ads_booster.contracts.models import RelativePath, Sha256Digest

_PRIVATE_DIRECTORY_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600
_MAX_REVISION_BYTES = 52_428_800


@dataclass(frozen=True, slots=True)
class ImmutableFileStore:
    """Validate and publish immutable knowledge revision files under one private root."""

    root: Path

    def __post_init__(self) -> None:
        """Create or validate the configured private root."""
        if not self.root.is_absolute():
            fail("root_must_be_absolute", str(self.root))
        self._create_root()

    def relative_path(self, target: RevisionTarget) -> RelativePath:
        return revision_relative_path(target)

    def prepare(self, draft: RevisionFileDraft) -> PreparedRevisionFile:
        operation_id = bounded_id("operation_id", draft.operation_id)
        expected_digest = digest(draft.sha256)
        actual_digest = sha256(draft.content).hexdigest()
        if actual_digest != expected_digest:
            fail("prepared_digest_mismatch", operation_id)
        prepared = PreparedRevisionFile(
            operation_id=operation_id,
            target=draft.target,
            sha256=expected_digest,
            byte_length=len(draft.content),
        )
        directory = self._prepared_path(prepared).parent
        self._ensure_private_directories(directory)
        stable = self._prepared_path(prepared)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".prepare-", dir=directory)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, _PRIVATE_FILE_MODE)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                _ = stream.write(draft.content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, stable, follow_symlinks=False)
            except FileExistsError:
                _ = self._verify(stable, expected_digest, len(draft.content))
            self._fsync_directory(directory)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
        return prepared

    def publish(self, prepared: PreparedRevisionFile) -> PublishedRevisionFile:
        expected_digest = digest(prepared.sha256)
        relative_path = self.relative_path(prepared.target)
        destination = self.root.joinpath(*PurePosixPath(relative_path).parts)
        self._ensure_private_directories(destination.parent)
        staged = self._prepared_path(prepared)
        if not self._private_file_exists(staged):
            if self._private_file_exists(destination):
                _ = self._verify(destination, expected_digest, prepared.byte_length)
                return self._published(prepared, relative_path)
            fail("prepared_file_missing", str(relative_path))
        _ = self._verify(staged, expected_digest, prepared.byte_length)
        try:
            os.link(staged, destination, follow_symlinks=False)
        except FileExistsError:
            try:
                _ = self._verify(destination, expected_digest, prepared.byte_length)
            except KnowledgeFileStoreError as error:
                error_code = "immutable_file_collision"
                raise store_error(
                    error_code,
                    str(relative_path),
                ) from error
        self._fsync_directory(destination.parent)
        _ = self._verify(destination, expected_digest, prepared.byte_length)
        staged.unlink()
        self._fsync_directory(staged.parent)
        return self._published(prepared, relative_path)

    def published(
        self,
        target: RevisionTarget,
        expected_sha256: Sha256Digest,
    ) -> PublishedRevisionFile:
        relative_path = self.relative_path(target)
        expected_digest = digest(expected_sha256)
        path = self.root.joinpath(*PurePosixPath(relative_path).parts)
        content = self._read_private_file(path, _MAX_REVISION_BYTES)
        if sha256(content).hexdigest() != expected_digest:
            fail("immutable_file_integrity_error", str(relative_path))
        return PublishedRevisionFile(
            target=target,
            relative_path=relative_path,
            sha256=expected_digest,
            byte_length=len(content),
        )

    def read(self, published: PublishedRevisionFile) -> bytes:
        relative_path = self.relative_path(published.target)
        if relative_path != published.relative_path:
            fail("published_path_mismatch", str(relative_path))
        path = self.root.joinpath(*PurePosixPath(relative_path).parts)
        return self._verify(path, digest(published.sha256), published.byte_length)

    def prepared_exists(self, prepared: PreparedRevisionFile) -> bool:
        return self._private_file_exists(self._prepared_path(prepared))

    def published_exists(self, target: RevisionTarget) -> bool:
        relative_path = self.relative_path(target)
        path = self.root.joinpath(*PurePosixPath(relative_path).parts)
        return self._private_file_exists(path)

    def publish_skill_display(
        self,
        workspace_id: str,
        skill_id: str,
        content: bytes,
    ) -> RelativePath:
        relative_path = skill_display_relative_path(workspace_id, skill_id)
        destination = self.root.joinpath(*PurePosixPath(relative_path).parts)
        self._ensure_private_directories(destination.parent)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".skill-", dir=destination.parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, _PRIVATE_FILE_MODE)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                _ = stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            _ = temporary.replace(destination)
            self._fsync_directory(destination.parent)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
        return relative_path

    def remove_skill_display(self, workspace_id: str, skill_id: str) -> None:
        relative_path = skill_display_relative_path(workspace_id, skill_id)
        destination = self.root.joinpath(*PurePosixPath(relative_path).parts)
        destination.unlink(missing_ok=True)
        if destination.parent.exists():
            self._fsync_directory(destination.parent)

    def _published(
        self,
        prepared: PreparedRevisionFile,
        relative_path: RelativePath,
    ) -> PublishedRevisionFile:
        return PublishedRevisionFile(
            target=prepared.target,
            relative_path=relative_path,
            sha256=prepared.sha256,
            byte_length=prepared.byte_length,
        )

    def _prepared_path(self, prepared: PreparedRevisionFile) -> Path:
        operation_id = bounded_id("operation_id", prepared.operation_id)
        target_path = self.relative_path(prepared.target)
        name = sha256(target_path.encode()).hexdigest()
        return self.root / "staging" / operation_id / f"{name}.prepared"

    def _create_root(self) -> None:
        self._reject_symlink_components(self.root)
        try:
            self.root.mkdir(mode=_PRIVATE_DIRECTORY_MODE, parents=True)
        except FileExistsError:
            _ = self.root
        self._require_private_directory(self.root, "root")

    def _ensure_private_directories(self, directory: Path) -> None:
        relative = directory.relative_to(self.root)
        current = self.root
        for part in relative.parts:
            current /= part
            try:
                current.mkdir(mode=_PRIVATE_DIRECTORY_MODE)
                self._fsync_directory(current.parent)
            except FileExistsError:
                _ = current
            self._require_private_directory(current, "directory")

    @staticmethod
    def _reject_symlink_components(path: Path) -> None:
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            if current.is_symlink():
                fail("root_symlink_forbidden", str(current))

    @staticmethod
    def _require_private_directory(path: Path, kind: str) -> None:
        try:
            metadata = path.lstat()
        except FileNotFoundError as error:
            error_code = f"{kind}_missing"
            raise store_error(error_code, str(path)) from error
        if stat.S_ISLNK(metadata.st_mode):
            fail(f"{kind}_symlink_forbidden", str(path))
        if not stat.S_ISDIR(metadata.st_mode):
            fail(f"{kind}_not_directory", str(path))
        if stat.S_IMODE(metadata.st_mode) != _PRIVATE_DIRECTORY_MODE:
            fail(f"{kind}_permissions_unsafe", str(path))

    @staticmethod
    def _verify(path: Path, expected_digest: str, byte_length: int) -> bytes:
        content = ImmutableFileStore._read_private_file(path, byte_length)
        if len(content) != byte_length or sha256(content).hexdigest() != expected_digest:
            fail("immutable_file_integrity_error", str(path))
        return content

    @staticmethod
    def _read_private_file(path: Path, maximum_bytes: int) -> bytes:
        try:
            metadata = path.lstat()
        except FileNotFoundError as error:
            error_code = "immutable_file_missing"
            raise store_error(error_code, str(path)) from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            fail("immutable_file_type_unsafe", str(path))
        if stat.S_IMODE(metadata.st_mode) != _PRIVATE_FILE_MODE:
            fail("immutable_file_permissions_unsafe", str(path))
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                fail("immutable_file_type_unsafe", str(path))
            if stat.S_IMODE(opened.st_mode) != _PRIVATE_FILE_MODE:
                fail("immutable_file_permissions_unsafe", str(path))
            if opened.st_size > maximum_bytes:
                fail("immutable_file_size_unsafe", str(path))
            chunks: Iterable[bytes] = iter(lambda: os.read(descriptor, 1024 * 1024), b"")
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    @staticmethod
    def _private_file_exists(path: Path) -> bool:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            fail("immutable_file_type_unsafe", str(path))
        if stat.S_IMODE(metadata.st_mode) != _PRIVATE_FILE_MODE:
            fail("immutable_file_permissions_unsafe", str(path))
        return True

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


__all__ = [
    "ImmutableFileStore",
    "KnowledgeFileStoreError",
    "KnowledgeRevisionTarget",
    "MemoryRevisionTarget",
    "PreparedRevisionFile",
    "PublishedRevisionFile",
    "RevisionFileDraft",
    "RevisionTarget",
    "SkillRevisionTarget",
    "SourceFileKind",
    "SourceRevisionTarget",
]
