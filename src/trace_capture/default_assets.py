from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def default_iphone_ui_path() -> Path:
    return Path(str(files("trace_capture").joinpath("assets/iphone-ui.png")))


def default_trace_components_path() -> Path:
    return Path(str(files("trace_capture").joinpath("assets/trace-components.png")))


def default_candidate_context_path() -> Path:
    """Return the packaged starter context shared by local and hosted generation."""
    return Path(str(files("trace_capture").joinpath("assets/context")))
