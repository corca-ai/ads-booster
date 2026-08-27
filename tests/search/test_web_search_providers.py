from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from ads_booster.search.text.providers import DdgsSearchProvider

if TYPE_CHECKING:
    import pytest


def test_ddgs_provider_reads_cli_json_output_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a ddgs-compatible executable that writes JSON to the requested output path
    def locate(_name: str) -> str:
        return "/usr/bin/ddgs"

    def run(
        argv: tuple[str, ...],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        _ = (check, capture_output, text, timeout)
        output_path = Path(argv[argv.index("--output") + 1])
        _ = output_path.write_text(
            '[{"title":"Source","href":"https://example.com","body":"snippet"}]',
            encoding="utf-8",
        )
        assert str(output_path) == argv[argv.index("--output") + 1]
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("ads_booster.search.text.providers.shutil.which", locate)
    monkeypatch.setattr("ads_booster.search.text.providers.subprocess.run", run)

    # When the provider invokes the executable
    result = DdgsSearchProvider(timeout_seconds=5).search("Trace", 1)

    # Then it parses the file-backed JSON into the normalized source contract
    assert result.provider == "duckduckgo"
    assert result.results[0].title == "Source"
    assert result.results[0].url == "https://example.com"
