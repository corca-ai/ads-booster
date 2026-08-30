from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def default_candidate_context_path() -> Path:
    """Return the packaged marketing context every generation run reads.

    The corpus travels inside the wheel rather than beside the checkout because the Mac
    worker is installed from a release archive and has no repository to read from.
    """
    return Path(str(files("ads_booster").joinpath("assets/context")))
