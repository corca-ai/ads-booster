from __future__ import annotations

# pyright: reportUnnecessaryComparison=false
from enum import StrEnum
from typing import TYPE_CHECKING, assert_never

if TYPE_CHECKING:
    from ads_booster.contracts import WallpaperStyle


class WallpaperSlider(StrEnum):
    CELL_OPACITY = "cell_opacity"
    IMAGE_SCALE = "image_scale"
    IMAGE_BRIGHTNESS = "image_brightness"
    IMAGE_BLUR = "image_blur"
    IMAGE_DIMMING = "image_dimming"


def normalized_slider_value(slider: WallpaperSlider, value: float) -> str:
    match slider:
        case WallpaperSlider.CELL_OPACITY | WallpaperSlider.IMAGE_DIMMING:
            normalized = value / 100
        case WallpaperSlider.IMAGE_SCALE:
            normalized = (value - 0.5) / 1.5
        case WallpaperSlider.IMAGE_BRIGHTNESS:
            normalized = value / 200
        case WallpaperSlider.IMAGE_BLUR:
            normalized = value / 50
        case unreachable:
            assert_never(unreachable)
    return f"{normalized:.12f}".rstrip("0").rstrip(".")


def normalized_background_slider_values(
    style: WallpaperStyle,
) -> tuple[tuple[str, str], ...]:
    return (
        (
            "lockScreenWallpaperBackgroundScale",
            normalized_slider_value(WallpaperSlider.IMAGE_SCALE, style.image_scale),
        ),
        (
            "lockScreenWallpaperBackgroundBrightness",
            normalized_slider_value(WallpaperSlider.IMAGE_BRIGHTNESS, style.image_brightness),
        ),
        (
            "lockScreenWallpaperBackgroundBlur",
            normalized_slider_value(WallpaperSlider.IMAGE_BLUR, style.image_blur),
        ),
        (
            "lockScreenWallpaperBackgroundDimming",
            normalized_slider_value(WallpaperSlider.IMAGE_DIMMING, style.image_dimming),
        ),
    )
