from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from ads_booster.knowledge.file_store import (
    RevisionFileDraft,
    SourceFileKind,
    SourceRevisionTarget,
)

if TYPE_CHECKING:
    from ads_booster.knowledge.extractors import ExtractionResult
    from ads_booster.knowledge.file_store import ImmutableFileStore, PreparedRevisionFile


@dataclass(frozen=True, slots=True)
class SourceFileSet:
    operation_id: str
    source_id: str
    revision_id: str
    original: bytes
    extraction: ExtractionResult
    message: bytes | None


def prepare_source_files(
    store: ImmutableFileStore,
    source_files: SourceFileSet,
) -> tuple[PreparedRevisionFile, ...]:
    files = [
        (SourceFileKind.ORIGINAL, source_files.original),
        (SourceFileKind.EXTRACTED, source_files.extraction.text.encode()),
        (SourceFileKind.MANIFEST, _manifest(source_files.original, source_files.extraction)),
    ]
    if source_files.message is not None:
        files.append((SourceFileKind.MESSAGE, source_files.message))
    return tuple(
        store.prepare(
            RevisionFileDraft(
                operation_id=source_files.operation_id,
                target=SourceRevisionTarget(
                    source_id=source_files.source_id,
                    revision_id=source_files.revision_id,
                    file_kind=file_kind,
                ),
                content=content,
                sha256=sha256(content).hexdigest(),
            )
        )
        for file_kind, content in files
    )


def _manifest(original: bytes, extraction: ExtractionResult) -> bytes:
    payload = {
        "byte_length": len(original),
        "completeness": extraction.completeness.value,
        "error_code": None if extraction.error_code is None else extraction.error_code.value,
        "extracted_sha256": extraction.text_sha256,
        "extraction_status": extraction.extraction_status.value,
        "extractor_version": extraction.extractor_version,
        "original_sha256": sha256(original).hexdigest(),
        "schema": "knowledge.source-manifest.v1",
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


__all__ = ["SourceFileSet", "prepare_source_files"]
