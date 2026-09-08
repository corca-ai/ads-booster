# pyright: reportUnknownMemberType=false
"""Exact source-pixel preservation; Pillow's variadic raster stubs are incomplete."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from ads_booster.contracts.creative_work import AssetParent
from ads_booster.marketing.agent_service.creative_image_edit_contract import (
    CreativeImageEditInput,
    EditRegion,
    compose_preserved_edit,
)
from ads_booster.providers.codex_cli import ReviewImage


def source_image(color: str, size: tuple[int, int] = (32, 64)) -> ReviewImage:
    image = Image.new("RGBA", size, color)
    image.putpixel((3, 3), (41, 42, 43, 123))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return ReviewImage(output.getvalue(), "PNG", size[0], size[1])


def request(**values: object) -> CreativeImageEditInput:
    original = source_image("red")
    return CreativeImageEditInput.model_validate(
        {
            "source": AssetParent(asset_id="source", revision=1, sha256=original.sha256),
            "operation": "extend_top",
            "instruction": "캐릭터와 글씨는 유지하고 위쪽 여백만 늘려줘",
            "top_pixels": 16,
            **values,
        }
    )


def test_extended_top_uses_generated_pixels_and_preserves_every_original_pixel() -> None:
    original = source_image("red")
    generated = source_image("blue", (32, 80))
    result = compose_preserved_edit(request(), original, generated)
    with (
        Image.open(io.BytesIO(result.png)) as output,
        Image.open(io.BytesIO(original.data)) as before,
    ):
        assert output.size == (32, 80)
        assert output.crop((0, 16, 32, 80)).tobytes() == before.tobytes()
        assert output.getpixel((0, 0)) == (0, 0, 255, 255)
    assert result.preserved_pixels == 32 * 64
    assert not result.generated_resized


def test_localization_changes_only_approved_title_region_even_if_generator_changes_everything() -> (
    None
):
    original = source_image("red")
    generated = source_image("blue", (64, 128))
    edit = request(
        operation="replace_regions",
        top_pixels=0,
        locale="ja-JP",
        regions=(
            EditRegion(
                x=4, y=8, width=12, height=10, instruction="タイトルだけ", exact_text="予定"
            ),
        ),
    )
    result = compose_preserved_edit(edit, original, generated)
    with (
        Image.open(io.BytesIO(result.png)) as output,
        Image.open(io.BytesIO(original.data)) as before,
    ):
        for y in range(64):
            for x in range(32):
                if not (4 <= x < 16 and 8 <= y < 18):
                    assert output.getpixel((x, y)) == before.getpixel((x, y))
        assert output.getpixel((8, 12)) == (0, 0, 255, 255)
    assert result.preserved_pixels == 32 * 64 - 12 * 10
    assert result.generated_resized


def test_overlapping_regions_and_unknown_localized_text_are_rejected() -> None:
    region = EditRegion(x=0, y=0, width=10, height=10, instruction="title")
    with pytest.raises(ValueError, match="overlap"):
        _ = request(operation="replace_regions", top_pixels=0, regions=(region, region))
    with pytest.raises(ValueError, match="exact_text"):
        _ = request(operation="replace_regions", top_pixels=0, locale="ja", regions=(region,))


@pytest.mark.parametrize(
    ("width", "height", "error"), [(33, 64, "outside_canvas"), (32, 64, "preserved_pixels")]
)
def test_invalid_region_rejected_before_generation(width: int, height: int, error: str) -> None:
    edit = request(
        operation="replace_regions",
        top_pixels=0,
        regions=(EditRegion(x=0, y=0, width=width, height=height, instruction="title"),),
    )
    with pytest.raises(ValueError, match=error):
        _ = edit.output_size(source_image("red"))
