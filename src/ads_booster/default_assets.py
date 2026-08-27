from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def default_iphone_ui_path() -> Path:
    return Path(str(files("ads_booster").joinpath("assets/iphone-ui.png")))


def default_candidate_context_path() -> Path:
    """Return the packaged starter context shared by local and hosted generation."""
    return Path(str(files("ads_booster").joinpath("assets/context")))
