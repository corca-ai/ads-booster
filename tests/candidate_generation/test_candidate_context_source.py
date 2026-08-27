from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.candidate_generation import (
    CandidateContextMissingError,
    CandidateContextSource,
    default_context_directory,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

_DOCUMENTS = (
    "core/FACTS.md",
    "domains/KR/VOICE.md",
    "references/KR/INDEX.md",
)


def _write_context(root: Path, *, skip: Sequence[str] = ()) -> Path:
    directory = root / "context"
    for relative_path in _DOCUMENTS:
        if relative_path in skip:
            continue
        path = directory / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(f"# {relative_path}\n내용", encoding="utf-8")
    return directory


def test_context_directory_prefers_workspace_and_can_be_overridden(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a workspace context and an explicit alternate location
    local = _write_context(tmp_path)

    # When / Then workspace context wins until the operator explicitly overrides it
    assert default_context_directory(tmp_path) == local
    monkeypatch.setenv("TRACE_AGENT_CONTEXT_DIR", str(tmp_path / "elsewhere"))
    assert default_context_directory(tmp_path) == tmp_path / "elsewhere"


def test_packaged_context_is_complete_when_workspace_has_none(tmp_path: Path) -> None:
    # Given no workspace-owned context directory
    directory = default_context_directory(tmp_path)

    # When the packaged context is parsed
    bundle = CandidateContextSource(directory).load()

    # Then every required document is non-empty and explicitly identified
    paths = tuple(document.relative_path for document in bundle.documents)
    assert "core/FACTS.md" in paths
    assert any(path.startswith("markets/") for path in paths)
    assert all(document.text.strip() for document in bundle.documents)


def test_missing_or_blank_context_documents_fail_before_agent_execution(tmp_path: Path) -> None:
    # Given a context snapshot with one missing file and one blank file
    directory = _write_context(tmp_path)
    _ = (directory / "domains" / "KR" / "VOICE.md").write_text("  \n", encoding="utf-8")

    # When / Then the typed failure names both unusable inputs
    with pytest.raises(CandidateContextMissingError) as failure:
        _ = CandidateContextSource(directory).load()
    assert failure.value.missing == ("domains/KR/VOICE.md",)


def test_missing_context_directory_reports_its_resolved_location(tmp_path: Path) -> None:
    # Given a configured context directory does not exist
    missing = tmp_path / "missing-context"

    # When / Then the boundary reports the exact location without entering the model loop
    with pytest.raises(CandidateContextMissingError) as failure:
        _ = CandidateContextSource(missing).load()
    assert failure.value.directory == missing


def test_context_source_discovers_new_domain_documents_without_a_code_list(tmp_path: Path) -> None:
    # Given a new marketing domain contributes its own document path
    directory = tmp_path / "context"
    document = directory / "domains" / "fitness" / "VOICE.md"
    document.parent.mkdir(parents=True)
    _ = document.write_text("실제 운동 루틴의 언어", encoding="utf-8")

    # When context is loaded
    bundle = CandidateContextSource(directory).load()

    # Then discovery includes the new document without changing Python constants
    assert tuple(item.relative_path for item in bundle.documents) == ("domains/fitness/VOICE.md",)
