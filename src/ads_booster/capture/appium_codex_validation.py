from __future__ import annotations

import re
from hashlib import sha256
from typing import TYPE_CHECKING, Final, Literal

from pydantic import Field

from ads_booster.capture.capture_safety import CaptureAdapterError, path_has_symlink_component
from ads_booster.contracts import ErrorCode
from ads_booster.contracts.models import ContractModel

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.capture.codex_appium_job import CodexAppiumJobContract
    from ads_booster.providers.codex_cli import (
        CodexAppiumReadyState,
        CodexAppiumSavedState,
    )

_TIME_PREFIX: Final = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d\s+(.+)$")
# How many requested rows the screen has to actually show before a wallpaper counts as
# built. It is not the number requested: Trace folds what does not fit into a "+N" badge,
# so a week of twenty rows renders four and a badge, and demanding all twenty is a
# condition no screen can meet. What still has to hold is that the rows on screen are the
# rows we asked for, which the checks below cover; this is the floor that separates a
# folded list from an empty panel.
MINIMUM_RENDERED_TRACE_ITEMS: Final = 3


class CodexAppiumJobResult(ContractModel):
    status: Literal["completed", "failed"]
    session_id: str | None = Field(min_length=1, max_length=200)
    session_closed: bool
    error_code: str | None = Field(pattern=r"^[a-z0-9_]+$")


def validate_execution_paths(
    contract: CodexAppiumJobContract,
    job_root: Path,
    background: Path,
    output: Path,
) -> None:
    for path in (job_root, background, output):
        if not path.is_absolute() or path_has_symlink_component(path):
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message="Codex Appium job paths must be absolute and symlink-free",
            )
    if not job_root.is_dir() or not output.is_relative_to(job_root):
        raise CaptureAdapterError(
            code=ErrorCode.SCENE_CAPTURE_FAILED,
            message="Codex Appium workspace is unavailable or output is outside it",
        )
    expected_background = job_root / contract.prepared_background.path
    if background != expected_background or not background.is_file():
        raise CaptureAdapterError(
            code=ErrorCode.INPUT_ASSET_MISSING,
            message="prepared background path does not match the v2 job",
        )
    try:
        background_digest = sha256(background.read_bytes()).hexdigest()
        job_root.chmod(0o700)
    except OSError as error:
        raise CaptureAdapterError(
            code=ErrorCode.INPUT_ASSET_MISSING,
            message="prepared background could not be verified",
        ) from error
    if background_digest != contract.prepared_background.sha256:
        raise CaptureAdapterError(
            code=ErrorCode.INPUT_ASSET_MISSING,
            message="prepared background digest does not match the v2 job",
        )


def expected_trace_item_titles(
    contract: CodexAppiumJobContract,
) -> tuple[str, ...]:
    """The titles the saved wallpaper has to show, one per requested row.

    The job carries the title as its own field now, so there is no prefix left to strip:
    a row's title is what the screen must render, whether the row is timed or all-day.
    """
    trace_items = contract.context.promotion_material.trace_items or ()
    return tuple(item.title for item in trace_items)


def rendered_titles_are_credible(
    rendered: tuple[str, ...],
    expected: tuple[str, ...],
) -> bool:
    """Whether the rows Codex reports on screen could be the rows we asked for.

    Two claims are checked rather than one. Every reported row must be one we requested,
    which is what stops a screen built from somebody else's data passing; and enough rows
    must be reported to tell a folded list from a panel that came out empty because its
    calendar was never selected.
    """
    # Never ask for more rows than were requested: a three-row request renders three.
    required = min(MINIMUM_RENDERED_TRACE_ITEMS, len(expected))
    if len(rendered) < required:
        return False
    return set(rendered) <= set(expected)


def result_matches_ready(
    result: CodexAppiumJobResult,
    ready: CodexAppiumReadyState,
) -> bool:
    return result.session_id == ready.session_id


def require_saved_state(
    saved: CodexAppiumSavedState,
    ready: CodexAppiumReadyState,
) -> str:
    if saved.session_id != ready.session_id:
        raise CaptureAdapterError(
            code=ErrorCode.SCENE_CAPTURE_FAILED,
            message="Codex Appium saved marker does not match its ready marker",
        )
    return saved.session_id


def require_completed_result(
    result: CodexAppiumJobResult,
    ready: CodexAppiumReadyState,
    saved: CodexAppiumSavedState,
) -> None:
    if result.status != "completed" or result.session_id is None or not result.session_closed:
        raise CaptureAdapterError(
            code=ErrorCode.SCENE_CAPTURE_FAILED,
            message=f"Codex Appium job did not complete: {result.error_code or result.status}",
        )
    if result.session_id != saved.session_id or saved.session_id != ready.session_id:
        raise CaptureAdapterError(
            code=ErrorCode.SCENE_CAPTURE_FAILED,
            message="Codex Appium completion does not match its saved marker",
        )


__all__ = [
    "CodexAppiumJobResult",
    "expected_trace_item_titles",
    "require_completed_result",
    "require_saved_state",
    "result_matches_ready",
    "validate_execution_paths",
]
