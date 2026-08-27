from __future__ import annotations

# pyright: reportUnknownMemberType=false
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    from datetime import datetime

_DATE_FONTS: Final = (
    Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
    Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
    Path("/System/Library/Fonts/SFNSRounded.ttf"),
)
_TIME_FONTS: Final = (
    Path("/System/Library/Fonts/SFNSRounded.ttf"),
    Path("/System/Library/Fonts/HelveticaNeue.ttc"),
)
_WEEKDAYS: Final = {
    "de": ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"),
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "fr": ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."),
    "ja": ("月", "火", "水", "木", "金", "土", "日"),
    "ko": ("월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"),
    "pt": ("seg.", "ter.", "qua.", "qui.", "sex.", "sáb.", "dom."),
    "zh": ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"),
}


@dataclass(frozen=True, slots=True)
class SystemUiRenderRequest:
    source: Path
    destination: Path
    reference_date: datetime
    locale: str


def render_system_ui(request: SystemUiRenderRequest) -> None:
    with Image.open(request.source) as raw:
        layer = raw.convert("RGB")
    width, height = layer.size
    draw = ImageDraw.Draw(layer)
    draw.rectangle(
        (int(width * 0.06), int(height * 0.075), int(width * 0.94), int(height * 0.25)),
        fill=(0, 0, 0),
    )
    date_label = _date_label(request.reference_date, request.locale)
    draw.text(
        (width // 2, int(height * 0.105)),
        date_label,
        anchor="mm",
        fill=(255, 255, 255),
        font=_font(_DATE_FONTS, max(12, int(height * 0.028))),
    )
    draw.text(
        (width // 2, int(height * 0.18)),
        request.reference_date.strftime("%H:%M"),
        anchor="mm",
        fill=(255, 255, 255),
        font=_font(_TIME_FONTS, max(24, int(height * 0.112))),
    )
    request.destination.parent.mkdir(parents=True, exist_ok=True)
    layer.save(request.destination, format="PNG")


def _date_label(reference_date: datetime, locale: str) -> str:
    language = locale.replace("_", "-").partition("-")[0].lower()
    weekdays = _WEEKDAYS.get(language, _WEEKDAYS["en"])
    weekday = weekdays[reference_date.weekday()]
    if language == "ko":
        return f"{reference_date.month}월 {reference_date.day}일 {weekday}"
    if language == "ja":
        return f"{reference_date.month}月 {reference_date.day}日 {weekday}曜日"
    if language == "zh":
        return f"{reference_date.month}月{reference_date.day}日 {weekday}"
    if language == "de":
        return f"{weekday}, {reference_date.day:02d}.{reference_date.month:02d}."
    if language in {"fr", "pt"}:
        return f"{weekday} {reference_date.day:02d}/{reference_date.month:02d}"
    return f"{weekday}, {reference_date.month:02d}/{reference_date.day:02d}"


def _font(candidates: tuple[Path, ...], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in candidates:
        try:
            return ImageFont.truetype(str(path), size=size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


__all__ = ["SystemUiRenderRequest", "render_system_ui"]
