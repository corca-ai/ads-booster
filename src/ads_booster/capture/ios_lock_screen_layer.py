from __future__ import annotations

from PIL import Image

from ads_booster.capture.capture_safety import CaptureAdapterError
from ads_booster.contracts import ErrorCode


def compose_ios_lock_screen_layer(wallpaper: Image.Image, layer: Image.Image) -> Image.Image:
    if wallpaper.size != layer.size:
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="ImageGen iOS UI layer dimensions must match the Trace wallpaper",
        )
    if not _is_opaque(wallpaper):
        raise CaptureAdapterError(
            code=ErrorCode.EXPORT_INVALID,
            message="Trace wallpaper must be fully opaque before iOS UI composition",
        )
    return Image.alpha_composite(wallpaper.convert("RGBA"), layer.convert("RGBA")).convert("RGB")


def _is_opaque(image: Image.Image) -> bool:
    alpha_histogram = image.convert("RGBA").getchannel("A").histogram()
    return alpha_histogram[255] == image.width * image.height
