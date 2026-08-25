from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from PIL import Image, ImageChops, ImageDraw, ImageOps

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class CanvasSize:
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class CompositionLayers:
    background: Path
    trace_components: Path
    iphone_ui: Path | None


@dataclass(frozen=True, slots=True)
class LayerRegion:
    left: int
    top: int
    width: int
    height: int
    corner_radius: int


@dataclass(frozen=True, slots=True)
class LayerCompositionError(Exception):
    path: Path
    message: str

    @override
    def __str__(self) -> str:
        return f"{self.message}: {self.path}"


def normalize_ai_ui_layer(
    source: Path,
    destination: Path,
    canvas: CanvasSize,
) -> None:
    with Image.open(source) as raw:
        layer = raw.convert("RGBA")
        red, green, blue, source_alpha = layer.split()
        brightest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        lifted = ImageChops.add(
            brightest,
            Image.new("L", brightest.size, 0),
            offset=115,
        )
        visible_ui = lifted.convert("1", dither=Image.Dither.NONE).convert("L")
        layer.putalpha(ImageChops.multiply(source_alpha, visible_ui))
        normalized = ImageOps.fit(
            layer,
            (canvas.width, canvas.height),
            method=Image.Resampling.LANCZOS,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        normalized.save(destination, format="PNG")


def extract_component_regions(
    source: Path,
    destination: Path,
    regions: tuple[LayerRegion, ...],
) -> None:
    with Image.open(source) as raw:
        screenshot = raw.convert("RGBA")
        extracted = Image.new("RGBA", screenshot.size, (0, 0, 0, 0))
        for region in regions:
            box = (
                region.left,
                region.top,
                region.left + region.width,
                region.top + region.height,
            )
            component = screenshot.crop(box)
            mask = Image.new("L", (region.width, region.height), 0)
            draw = ImageDraw.Draw(mask)
            draw.rounded_rectangle(
                (0, 0, region.width - 1, region.height - 1),
                radius=region.corner_radius,
                fill=255,
            )
            component.putalpha(ImageChops.multiply(component.getchannel("A"), mask))
            extracted.alpha_composite(
                component,
                dest=(region.left, region.top),
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        extracted.save(destination, format="PNG")


def compose_marketing_image(
    layers: CompositionLayers,
    destination: Path,
    canvas: CanvasSize,
) -> None:
    size = (canvas.width, canvas.height)
    with (
        Image.open(layers.background) as raw_background,
        Image.open(layers.trace_components) as raw_components,
    ):
        background = ImageOps.fit(
            raw_background.convert("RGBA"),
            size,
            method=Image.Resampling.LANCZOS,
        )
        components = ImageOps.fit(
            raw_components.convert("RGBA"),
            size,
            method=Image.Resampling.LANCZOS,
        )
        _require_sparse_overlay(components, layers.trace_components)
        composed = Image.alpha_composite(background, components)
        if layers.iphone_ui is not None:
            with Image.open(layers.iphone_ui) as raw_ui:
                iphone_ui = ImageOps.fit(
                    raw_ui.convert("RGBA"),
                    size,
                    method=Image.Resampling.LANCZOS,
                )
            _require_sparse_overlay(iphone_ui, layers.iphone_ui)
            composed = Image.alpha_composite(composed, iphone_ui)
        destination.parent.mkdir(parents=True, exist_ok=True)
        composed.save(destination, format="PNG")


def _require_alpha(image: Image.Image, path: Path) -> None:
    if "A" not in image.getbands():
        raise LayerCompositionError(
            path=path,
            message="compositing layer must contain an alpha channel",
        )


def _require_sparse_overlay(image: Image.Image, path: Path) -> None:
    _require_alpha(image, path)
    histogram = image.getchannel("A").histogram()
    transparent_pixels = histogram[0]
    total_pixels = sum(histogram)
    visible_pixels = total_pixels - transparent_pixels
    if visible_pixels == 0 or transparent_pixels * 5 < total_pixels:
        raise LayerCompositionError(
            path=path,
            message="overlay must be visible and leave at least 20% transparent canvas",
        )
