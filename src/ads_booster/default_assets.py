from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def default_iphone_ui_path() -> Path:
    return Path(str(files("ads_booster").joinpath("assets/iphone-ui.png")))


def default_trace_components_path() -> Path:
    """Return the packaged Trace component layer the local fallback composition merges.

    This is a fixture, not a capture: it shows the Trace widget as it looks, but it does
    not carry the candidate's own schedule or clock. Only the native Appium path renders
    those, which is why the fallback records itself as a fallback.
    """
    return Path(str(files("ads_booster").joinpath("assets/trace-components.png")))


def default_candidate_context_path() -> Path:
    """Return the packaged starter context shared by local and hosted generation."""
    return Path(str(files("ads_booster").joinpath("assets/context")))
