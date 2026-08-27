from __future__ import annotations

# pyright: reportUnnecessaryComparison=false
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, ClassVar, Final, Literal, Self, assert_never
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.models import ContractModel, Identifier, Sha256Digest

HexColor = Annotated[str, Field(pattern=r"^#[A-F0-9]{6}$")]
WallpaperTitle = Annotated[str, Field(min_length=1, max_length=160)]
MAX_WALLPAPER_EVENTS: Final = 8
NON_UTC_DATETIME: Final = "non_utc_datetime"
NON_UTC_DATETIME_MESSAGE: Final = "timestamps must be timezone-aware UTC datetimes"
PARTIAL_EVENT_TIME_RANGE: Final = "partial_event_time_range"
PARTIAL_EVENT_TIME_RANGE_MESSAGE: Final = (
    "events must provide both starts_at and ends_at or neither"
)
MISSING_EVENT_TIME_RANGE: Final = "missing_event_time_range"
MISSING_EVENT_TIME_RANGE_MESSAGE: Final = "non-all-day events require starts_at and ends_at"
INVALID_EVENT_TIME_RANGE: Final = "invalid_event_time_range"
INVALID_EVENT_TIME_RANGE_MESSAGE: Final = "ends_at must be later than starts_at"
WALLPAPER_LAYOUT_COMPONENT_COUNT: Final = "wallpaper_layout_component_count"
WALLPAPER_LAYOUT_COMPONENT_COUNT_MESSAGE: Final = (
    "component count must match the declared wallpaper row layout"
)
DUPLICATE_WALLPAPER_REFERENCE_ID: Final = "duplicate_wallpaper_reference_id"
DUPLICATE_WALLPAPER_REFERENCE_ID_MESSAGE: Final = "reference_ids must be unique"
WALLPAPER_EVENT_COUNT: Final = "wallpaper_event_count"
WALLPAPER_EVENT_COUNT_MESSAGE: Final = "wallpaper plans must contain between one and eight events"
UNKNOWN_IANA_TIME_ZONE: Final = "unknown_iana_time_zone"
UNKNOWN_IANA_TIME_ZONE_MESSAGE: Final = "time_zone must be a known IANA time zone"


def require_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise PydanticCustomError(
            NON_UTC_DATETIME,
            NON_UTC_DATETIME_MESSAGE,
        )
    return value


UtcDateTime = Annotated[datetime, AfterValidator(require_utc_datetime)]


def require_iana_time_zone(value: str) -> str:
    try:
        _ = ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise PydanticCustomError(
            UNKNOWN_IANA_TIME_ZONE,
            UNKNOWN_IANA_TIME_ZONE_MESSAGE,
        ) from error
    return value


IanaTimeZone = Annotated[
    str,
    Field(min_length=1, max_length=128),
    AfterValidator(require_iana_time_zone),
]


class WallpaperContract(ContractModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid", strict=True)


class WallpaperTextColor(StrEnum):
    WHITE = "white"
    BLACK = "black"


class WallpaperHeaderColor(StrEnum):
    AUTO = "auto"
    WHITE = "white"
    BLACK = "black"


class WallpaperFontSize(StrEnum):
    EXTRA_SMALL = "extraSmall"
    SMALL = "small"
    NORMAL = "normal"
    LARGE = "large"
    EXTRA_LARGE = "extraLarge"


class WallpaperCellHeight(StrEnum):
    MIN = "min"
    SHORT = "short"
    NORMAL = "normal"
    TALL = "tall"
    MAXIMUM = "maximum"


class WallpaperCellColor(StrEnum):
    BLACK = "#000000"
    WHITE = "#FFFFFF"
    ROSE = "#E08080"
    AMBER = "#D0A850"
    GREEN = "#60B878"
    TEAL = "#50B8B8"
    BLUE = "#6080C8"
    PURPLE = "#9060C0"
    PINK = "#C858A0"
    CORAL = "#D06878"
    SLATE = "#88A0A8"


class WallpaperLayout(StrEnum):
    ONE_BY_ONE = "one_by_one"
    TWO_BY_ONE = "two_by_one"
    TWO_TOP_ONE_BOTTOM = "two_top_one_bottom"
    TWO_BY_TWO = "two_by_two"

    @property
    def component_count(self) -> int:
        match self:
            case WallpaperLayout.ONE_BY_ONE:
                return 1
            case WallpaperLayout.TWO_BY_ONE:
                return 2
            case WallpaperLayout.TWO_TOP_ONE_BOTTOM:
                return 3
            case WallpaperLayout.TWO_BY_TWO:
                return 4
            case unreachable:
                assert_never(unreachable)


class WallpaperEvent(WallpaperContract):
    title: WallpaperTitle
    starts_at: UtcDateTime | None
    ends_at: UtcDateTime | None
    is_all_day: bool
    color: HexColor

    @model_validator(mode="after")
    def require_unambiguous_time_range(self) -> Self:
        if self.starts_at is None and self.ends_at is None:
            if not self.is_all_day:
                raise PydanticCustomError(
                    MISSING_EVENT_TIME_RANGE,
                    MISSING_EVENT_TIME_RANGE_MESSAGE,
                )
            return self
        if self.starts_at is None or self.ends_at is None:
            raise PydanticCustomError(
                PARTIAL_EVENT_TIME_RANGE,
                PARTIAL_EVENT_TIME_RANGE_MESSAGE,
            )
        if self.starts_at >= self.ends_at:
            raise PydanticCustomError(
                INVALID_EVENT_TIME_RANGE,
                INVALID_EVENT_TIME_RANGE_MESSAGE,
            )
        return self


class WallpaperComponent(WallpaperContract):
    title: WallpaperTitle
    events: Annotated[tuple[WallpaperEvent, ...], Field(min_length=1, max_length=4)]


class WallpaperRow(WallpaperContract):
    layout: WallpaperLayout
    components: Annotated[tuple[WallpaperComponent, ...], Field(min_length=1, max_length=4)]

    @model_validator(mode="after")
    def require_layout_component_count(self) -> Self:
        if len(self.components) != self.layout.component_count:
            raise PydanticCustomError(
                WALLPAPER_LAYOUT_COMPONENT_COUNT,
                WALLPAPER_LAYOUT_COMPONENT_COUNT_MESSAGE,
            )
        return self


class WallpaperStyle(WallpaperContract):
    text_color: WallpaperTextColor
    header_color: WallpaperHeaderColor
    cell_color: WallpaperCellColor
    font_size: WallpaperFontSize
    cell_opacity: Annotated[float, Field(ge=0, le=100)]
    cell_blur: bool
    cell_height: WallpaperCellHeight
    allow_two_line_title: bool
    image_scale: Annotated[float, Field(ge=0.5, le=2)]
    image_brightness: Annotated[float, Field(ge=0, le=200)]
    image_blur: Annotated[float, Field(ge=0, le=50)]
    image_dimming: Annotated[float, Field(ge=0, le=100)]


class WallpaperPlan(WallpaperContract):
    schema_version: Literal["trace.wallpaper-plan.v1"]
    request_id: Identifier
    time_zone: IanaTimeZone
    background_query: Annotated[str, Field(min_length=1, max_length=500)]
    reference_ids: Annotated[tuple[Identifier, ...], Field(max_length=16)]
    style: WallpaperStyle
    rows: Annotated[tuple[WallpaperRow, ...], Field(min_length=1, max_length=4)]

    @model_validator(mode="after")
    def require_unique_references_and_one_to_eight_events(self) -> Self:
        if len(set(self.reference_ids)) != len(self.reference_ids):
            raise PydanticCustomError(
                DUPLICATE_WALLPAPER_REFERENCE_ID,
                DUPLICATE_WALLPAPER_REFERENCE_ID_MESSAGE,
            )
        event_count = sum(
            len(component.events) for row in self.rows for component in row.components
        )
        if not 1 <= event_count <= MAX_WALLPAPER_EVENTS:
            raise PydanticCustomError(
                WALLPAPER_EVENT_COUNT,
                WALLPAPER_EVENT_COUNT_MESSAGE,
            )
        return self


class WallpaperExportManifest(WallpaperContract):
    schema_version: Literal["trace.wallpaper-export-manifest.v1"]
    request_sha256: Sha256Digest
    export_nonce: Sha256Digest
    bundle_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9.-]+$")]
    device_udid: Annotated[str, Field(pattern=r"^[A-F0-9-]{36}$")]
    role: Literal["trace_wallpaper"]
    artifact_sha256: Sha256Digest
    width: Annotated[int, Field(gt=0, le=8192)]
    height: Annotated[int, Field(gt=0, le=8192)]


__all__ = [
    "WallpaperCellColor",
    "WallpaperCellHeight",
    "WallpaperComponent",
    "WallpaperContract",
    "WallpaperEvent",
    "WallpaperExportManifest",
    "WallpaperFontSize",
    "WallpaperHeaderColor",
    "WallpaperLayout",
    "WallpaperPlan",
    "WallpaperRow",
    "WallpaperStyle",
    "WallpaperTextColor",
]
