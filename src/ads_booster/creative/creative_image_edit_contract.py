"""Exact image change regions and deterministic preservation of the original raster."""

from __future__ import annotations

import io
from dataclasses import dataclass
from hashlib import sha256
from typing import Annotated, Literal, Protocol, Self, cast

from PIL import Image
from pydantic import Field, model_validator

from ads_booster.contracts.creative_work import AssetParent
from ads_booster.contracts.models import ContractModel
from ads_booster.providers.codex_cli import ReviewImage

_MAX_PIXELS = 20_000_000
_MAX_DIMENSION = 8192
_MAX_INSTRUCTION = 1000


class _RasterBytes(Protocol):
    def tobytes(self) -> bytes: ...


def _pixels(image: Image.Image) -> bytes:
    return cast("_RasterBytes", image).tobytes()


class EditRegion(ContractModel):
    x: Annotated[int, Field(ge=0, le=8192)]
    y: Annotated[int, Field(ge=0, le=8192)]
    width: Annotated[int, Field(ge=1, le=8192)]
    height: Annotated[int, Field(ge=1, le=8192)]
    instruction: Annotated[str, Field(min_length=1, max_length=1000)]
    exact_text: Annotated[str | None, Field(max_length=1000)] = None
    font_guidance: Annotated[str | None, Field(max_length=500)] = None


class CreativeImageEditInput(ContractModel):
    source: AssetParent
    operation: Literal["extend_top", "replace_regions"]
    instruction: Annotated[str, Field(min_length=1, max_length=2000)]
    top_pixels: Annotated[int, Field(ge=0, le=4096)] = 0
    regions: Annotated[tuple[EditRegion, ...], Field(max_length=8)] = ()
    locale: Annotated[str | None, Field(pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")] = None
    preserve: Annotated[tuple[str, ...], Field(max_length=16)] = ()

    @model_validator(mode="after")
    def exact_change(self) -> Self:
        if self.operation == "extend_top":
            if not self.top_pixels or self.regions or self.locale is not None:
                raise ValueError("image_extension_requires_top_only")
        elif not self.regions or self.top_pixels:
            raise ValueError("image_edit_requires_regions_only")
        if self.locale is not None and any(region.exact_text is None for region in self.regions):
            raise ValueError("image_localization_requires_exact_text")
        for index, region in enumerate(self.regions):
            for previous in self.regions[:index]:
                if max(region.x, previous.x) < min(
                    region.x + region.width, previous.x + previous.width
                ) and max(region.y, previous.y) < min(
                    region.y + region.height, previous.y + previous.height
                ):
                    raise ValueError("image_edit_regions_overlap")
        if any(not value.strip() or len(value) > _MAX_INSTRUCTION for value in self.preserve):
            raise ValueError("image_preserve_instruction_invalid")
        return self

    def output_size(self, source: ReviewImage) -> tuple[int, int]:
        width, height = source.width, source.height + self.top_pixels
        if width * height > _MAX_PIXELS or max(width, height) > _MAX_DIMENSION:
            raise ValueError("image_edit_canvas_too_large")
        if any(
            region.x + region.width > width or region.y + region.height > height
            for region in self.regions
        ):
            raise ValueError("image_edit_region_outside_canvas")
        if (
            self.operation == "replace_regions"
            and sum(region.width * region.height for region in self.regions) >= width * height
        ):
            raise ValueError("image_edit_requires_preserved_pixels")
        return width, height


@dataclass(frozen=True, slots=True)
class ComposedImageEdit:
    png: bytes
    width: int
    height: int
    preserved_pixels: int
    sha256: str
    generated_resized: bool


def compose_preserved_edit(
    request: CreativeImageEditInput, source: ReviewImage, generated: ReviewImage
) -> ComposedImageEdit:
    """Use generated pixels only inside the approved change; never regenerate preserved pixels."""
    width, height = request.output_size(source)
    resized = (generated.width, generated.height) != (width, height)
    with (
        Image.open(io.BytesIO(source.data)) as original_image,
        Image.open(io.BytesIO(generated.data)) as generated_image,
    ):
        original = original_image.convert("RGBA")
        edited = generated_image.convert("RGBA")
        if resized:
            edited = edited.resize((width, height), Image.Resampling.LANCZOS)
        if request.operation == "extend_top":
            edited.paste(original, (0, request.top_pixels))
            preserved = _pixels(edited.crop((0, request.top_pixels, width, height))) == _pixels(
                original
            )
            preserved_pixels = source.width * source.height
        else:
            result = original.copy()
            for region in request.regions:
                box = (region.x, region.y, region.x + region.width, region.y + region.height)
                result.paste(edited.crop(box), (region.x, region.y))
            edited = result
            preserved_pixels = width * height - sum(
                region.width * region.height for region in request.regions
            )
            # Compare every unchanged row interval, not only a hash of an intended mask.
            preserved = True
            for y in range(height):
                intervals = sorted(
                    (r.x, r.x + r.width) for r in request.regions if r.y <= y < r.y + r.height
                )
                left = 0
                for start, end in (*intervals, (width, width)):
                    box = (left, y, start, y + 1)
                    preserved = preserved and _pixels(edited.crop(box)) == _pixels(
                        original.crop(box)
                    )
                    left = end
        if not preserved:
            raise ValueError("image_edit_preservation_failed")
        output = io.BytesIO()
        edited.save(output, format="PNG")
    png = output.getvalue()
    return ComposedImageEdit(png, width, height, preserved_pixels, sha256(png).hexdigest(), resized)
