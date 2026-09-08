from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def default_candidate_context_path() -> Path:
    """Return the marketing context corpus packaged with the installed distribution."""
    return Path(str(files("ads_booster").joinpath("assets/context")))
