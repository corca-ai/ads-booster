from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from ads_booster.marketing.agent_service.image_generation import generated_image, read_artifact

if TYPE_CHECKING:
    from pathlib import Path


def test_artifact_reader_rejects_path_escape_symlink_and_tamper(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="image_"):
        _ = read_artifact(tmp_path, "../secret")
    target = tmp_path / "image.png"
    Image.new("RGB", (128, 128), "blue").save(target)
    path = tmp_path / ("a" * 64 + ".png")
    path.symlink_to(target)
    with pytest.raises(ValueError, match="image_"):
        _ = read_artifact(tmp_path, "a" * 64)
    path.unlink()
    _ = path.write_bytes(target.read_bytes())
    with pytest.raises(ValueError, match="image_"):
        _ = read_artifact(tmp_path, "a" * 64)


def test_collector_uses_only_cli_thread_and_rejects_ambiguous_images(tmp_path: Path) -> None:
    thread = "00000000-0000-0000-0000-000000000002"
    folder = tmp_path / "generated_images" / thread
    folder.mkdir(parents=True)
    Image.new("RGB", (128, 128), "blue").save(folder / "first.png")
    events = json.dumps({"type": "thread.started", "thread_id": thread})
    assert generated_image(tmp_path, events) == folder / "first.png"
    for wrong in ("../secret", "00000000-0000-0000-0000-000000000003"):
        with pytest.raises(ValueError, match="codex_image_"):
            _ = generated_image(
                tmp_path, json.dumps({"type": "thread.started", "thread_id": wrong})
            )
    with pytest.raises(ValueError, match="codex_image_thread_missing"):
        _ = generated_image(tmp_path, json.dumps({"type": "item.completed", "text": events}))
    Image.new("RGB", (128, 128), "blue").save(folder / "second.png")
    assert generated_image(tmp_path, events) == folder / "second.png"
    for i in range(3):
        Image.new("RGB", (128, 128), "blue").save(folder / f"extra-{i}.png")
    with pytest.raises(ValueError, match="codex_image_count_invalid"):
        _ = generated_image(tmp_path, events)
