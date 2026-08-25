from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from trace_capture.providers.models import ProviderReasoningLevel


def reasoning_options(levels: tuple[ProviderReasoningLevel, ...]) -> tuple[Option, ...]:
    return tuple(
        Option(
            f"{level.effort} · {level.description}" if level.description else level.effort,
            id=level.effort,
        )
        for level in levels
    )


def reasoning_index(
    levels: tuple[ProviderReasoningLevel, ...],
    current_effort: str | None,
    default_effort: str | None,
) -> int:
    return next(
        (index for index, level in enumerate(levels) if level.effort == current_effort),
        next(
            (index for index, level in enumerate(levels) if level.effort == default_effort),
            0,
        ),
    )
