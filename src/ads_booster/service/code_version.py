"""The git revision the running service was imported from.

A workspace screen is reloaded in a second; the API process behind it is not. Three times
in one day the two disagreed and the difference was read as a bug in the new code rather
than as an old process still holding the port. Printing the revision at startup makes the
running code answerable from the log alone.

This asks git about the package source rather than the working directory, because the
service is normally started from somewhere else. Outside a checkout there is no answer to
give, and a startup banner is not the place to complain about that: every failure is
silence.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Final

_TIMEOUT_SECONDS: Final = 2.0


def code_version_line(source: Path | None = None) -> str | None:
    """Describe the checked-out revision, or None when there is nothing to describe.

    `source` is the directory to ask about; it defaults to this package's own, which is the
    only one the answer is about.
    """
    git = shutil.which("git")
    if git is None:
        return None
    directory = Path(__file__).resolve().parent if source is None else source
    revision = _ask(git, directory, "--short")
    branch = _ask(git, directory, "--abbrev-ref")
    if revision is None or branch is None:
        return None
    return f"Code: {revision} ({branch})"


def _ask(git: str, directory: Path, form: str) -> str | None:
    """Read one description of HEAD, or None for anything that is not an answer."""
    try:
        result = subprocess.run(  # noqa: S603
            [git, "-C", str(directory), "rev-parse", form, "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except OSError, subprocess.SubprocessError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None
