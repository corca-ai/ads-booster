"""Bound source fetching needs authenticated lineage, but no model inference."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING

import pytest

from ads_booster.marketing.agent_service.slack_image_files import SlackImageFiles
from ads_booster.marketing.agent_service.slack_image_review import bind_files

from .test_slack_image_review import HTTP, png

if TYPE_CHECKING:
    from pathlib import Path


def files(tmp_path: Path, http: HTTP) -> SlackImageFiles:
    database = tmp_path / "service.db"
    bind_files(database, "team", "run-1", "C01", ("F01",))
    return SlackImageFiles(
        database, tmp_path / "artifacts", "team", "synthetic-test-token", "T00", http
    )


def test_fetch_saves_verified_immutable_source_without_model(tmp_path: Path) -> None:
    http = HTTP(png())
    owner = files(tmp_path, http)
    result = owner.fetch("run-1", "F01")
    assert result.file_id == "F01"
    assert result.channel_id == "C01"
    assert result.sha256 == sha256(png()).hexdigest()
    assert result.byte_size == len(png())
    assert result.path.read_bytes() == png()
    assert owner.fetch("run-1", "F01") == result
    assert all(response.closed for response in http.responses)
    assert "synthetic-test-token" not in repr(owner)


@pytest.mark.parametrize(
    ("run", "file_id"), [("other-run", "F01"), ("run-1", "F02"), ("run-1", "../F01")]
)
def test_unsigned_file_never_reaches_network(tmp_path: Path, run: str, file_id: str) -> None:
    http = HTTP(png())
    owner = files(tmp_path, http)
    with pytest.raises(ValueError, match="slack_image_file_not_bound"):
        _ = owner.fetch(run, file_id)
    assert http.calls == []


@pytest.mark.parametrize("fault", ["redirect", "oversize", "decode", "team", "path"])
def test_fetch_safety_failures_are_sanitized(tmp_path: Path, fault: str) -> None:
    http = HTTP(png())
    owner = files(tmp_path, http)
    if fault == "redirect":
        http.response_url = "https://example.com/private"
    elif fault == "oversize":
        http.image = b"x" * (10 * 1024 * 1024 + 1)
    elif fault == "decode":
        http.image = b"not image"
    elif fault == "team":
        http.team = "OTHER"
    else:
        result = owner.fetch("run-1", "F01")
        _ = result.path.write_bytes(b"changed")
    with pytest.raises(ValueError, match=r"^slack_image_file_fetch_failed$"):
        _ = owner.fetch("run-1", "F01")
    assert all(response.closed for response in http.responses)
