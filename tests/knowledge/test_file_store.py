from __future__ import annotations

import os
import stat
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest

from ads_booster.knowledge.file_store import (
    ImmutableFileStore,
    KnowledgeFileStoreError,
    KnowledgeRevisionTarget,
    MemoryRevisionTarget,
    RevisionFileDraft,
    SourceFileKind,
    SourceRevisionTarget,
)

if TYPE_CHECKING:
    from pathlib import Path


def _draft(
    operation_id: str,
    target: KnowledgeRevisionTarget,
    content: bytes,
) -> RevisionFileDraft:
    return RevisionFileDraft(
        operation_id=operation_id,
        target=target,
        content=content,
        sha256=sha256(content).hexdigest(),
    )


def test_revision_paths_are_derived_only_from_bounded_ids(tmp_path: Path) -> None:
    # Given
    store = ImmutableFileStore(tmp_path / "knowledge-root")

    # When
    source = store.relative_path(
        SourceRevisionTarget(
            source_id="source.price.kr",
            revision_id="source.price.kr.rev1",
            file_kind=SourceFileKind.ORIGINAL,
        )
    )
    knowledge = store.relative_path(
        KnowledgeRevisionTarget(page_id="page.price", revision_id="page.price.rev1")
    )
    memory = store.relative_path(
        MemoryRevisionTarget(
            workspace_id="workspace.team-a",
            document_id="memory.core",
            revision_id="memory.core.rev1",
        )
    )

    # Then
    assert source == "sources/source.price.kr/source.price.kr.rev1/original.bin"
    assert knowledge == "knowledge/page.price/page.price.rev1.md"
    assert memory == "teams/workspace.team-a/revisions/memory.core/memory.core.rev1.md"
    with pytest.raises(KnowledgeFileStoreError, match="revision_id_invalid"):
        _ = store.relative_path(
            KnowledgeRevisionTarget(page_id="page.price", revision_id="../escape")
        )


def test_root_rejects_unsafe_permissions_and_symlinks(tmp_path: Path) -> None:
    # Given
    unsafe_root = tmp_path / "unsafe"
    unsafe_root.mkdir(mode=0o700)
    unsafe_root.chmod(0o770)
    real_root = tmp_path / "real"
    real_root.mkdir(mode=0o700)
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root, target_is_directory=True)

    # When / Then
    with pytest.raises(KnowledgeFileStoreError, match="root_permissions_unsafe"):
        _ = ImmutableFileStore(unsafe_root)
    with pytest.raises(KnowledgeFileStoreError, match="root_symlink_forbidden"):
        _ = ImmutableFileStore(linked_root)


def test_prepare_and_publish_write_exact_digest_with_private_modes(tmp_path: Path) -> None:
    # Given
    store = ImmutableFileStore(tmp_path / "knowledge-root")
    content = "가격은 31,000원입니다.\n".encode()
    draft = _draft(
        "operation.page.1",
        KnowledgeRevisionTarget(page_id="page.price", revision_id="page.price.rev1"),
        content,
    )

    # When
    prepared = store.prepare(draft)
    published = store.publish(prepared)

    # Then
    path = store.root / published.relative_path
    assert store.read(published) == content
    assert published.sha256 == sha256(content).hexdigest()
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_prepare_and_publish_fsync_file_and_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    synced_types: list[int] = []
    real_fsync = os.fsync

    def record_fsync(descriptor: int) -> None:
        synced_types.append(stat.S_IFMT(os.fstat(descriptor).st_mode))
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", record_fsync)
    store = ImmutableFileStore(tmp_path / "knowledge-root")
    draft = _draft(
        "operation.page.fsync",
        KnowledgeRevisionTarget(page_id="page.fsync", revision_id="page.fsync.rev1"),
        b"durable",
    )

    # When
    prepared = store.prepare(draft)
    _ = store.publish(prepared)

    # Then
    assert stat.S_IFREG in synced_types
    assert synced_types.count(stat.S_IFDIR) >= 2


def test_same_bytes_replay_succeeds_and_different_bytes_collide(tmp_path: Path) -> None:
    # Given
    store = ImmutableFileStore(tmp_path / "knowledge-root")
    target = KnowledgeRevisionTarget(page_id="page.price", revision_id="page.price.rev1")
    first = store.prepare(_draft("operation.page.1", target, b"first"))
    _ = store.publish(first)

    # When
    replay = store.publish(store.prepare(_draft("operation.page.2", target, b"first")))

    # Then
    assert store.read(replay) == b"first"
    conflicting = store.prepare(_draft("operation.page.3", target, b"different"))
    with pytest.raises(KnowledgeFileStoreError, match="immutable_file_collision"):
        _ = store.publish(conflicting)
    assert store.read(replay) == b"first"


def test_publish_rejects_symlinked_destination_directory(tmp_path: Path) -> None:
    # Given
    store = ImmutableFileStore(tmp_path / "knowledge-root")
    target = KnowledgeRevisionTarget(page_id="page.price", revision_id="page.price.rev1")
    prepared = store.prepare(_draft("operation.page.1", target, b"first"))
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (store.root / "knowledge").symlink_to(outside, target_is_directory=True)

    # When / Then
    with pytest.raises(KnowledgeFileStoreError, match="directory_symlink_forbidden"):
        _ = store.publish(prepared)
    assert list(outside.iterdir()) == []


def test_concurrent_no_clobber_publish_has_one_winner_and_intact_bytes(tmp_path: Path) -> None:
    # Given
    store = ImmutableFileStore(tmp_path / "knowledge-root")
    target = KnowledgeRevisionTarget(page_id="page.race", revision_id="page.race.rev1")
    prepared = (
        store.prepare(_draft("operation.race.a", target, b"alpha")),
        store.prepare(_draft("operation.race.b", target, b"bravo")),
    )

    def publish(index: int) -> str:
        try:
            return store.publish(prepared[index]).sha256
        except KnowledgeFileStoreError as error:
            return error.code

    # When
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(publish, range(2)))

    # Then
    assert outcomes.count("immutable_file_collision") == 1
    winner_digests = {sha256(b"alpha").hexdigest(), sha256(b"bravo").hexdigest()}
    winner = next(outcome for outcome in outcomes if outcome in winner_digests)
    published = store.published(target, winner)
    assert sha256(store.read(published)).hexdigest() == winner


def test_interrupted_preparation_never_creates_published_revision(tmp_path: Path) -> None:
    # Given
    store = ImmutableFileStore(tmp_path / "knowledge-root")
    target = KnowledgeRevisionTarget(page_id="page.draft", revision_id="page.draft.rev1")

    # When
    prepared = store.prepare(_draft("operation.interrupted", target, b"not committed"))

    # Then
    assert store.prepared_exists(prepared)
    assert not store.published_exists(target)
