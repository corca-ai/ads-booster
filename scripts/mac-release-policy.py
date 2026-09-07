# ruff: noqa: INP001
"""Select release publication independently of mandatory Mac compatibility checks."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import cast


def release_requested(event: str, base: str) -> bool:
    if event == "workflow_dispatch":
        return True  # Explicit release/resume; the release-state guard still validates ownership.
    if not re.fullmatch(r"[0-9a-f]{40}", base) or base == "0" * 40:
        message = (
            "A real event base commit is required; use workflow_dispatch for an initial release"
        )
        raise ValueError(message)
    # Git is runner-provided; the revision is validated above and never interpreted by a shell.
    previous = subprocess.check_output(  # noqa: S603
        ["git", "show", f"{base}:pyproject.toml"],  # noqa: S607
        text=True,
    )
    current = tomllib.loads(Path("pyproject.toml").read_text())
    return cast("str", current["project"]["version"]) != cast(
        "str", tomllib.loads(previous)["project"]["version"]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--event", required=True, choices=["pull_request", "push", "workflow_dispatch"]
    )
    _ = parser.add_argument("--base", default="")
    args = parser.parse_args()
    requested = release_requested(cast("str", args.event), cast("str", args.base))
    _ = sys.stdout.write("true\n" if requested else "false\n")


if __name__ == "__main__":
    main()
