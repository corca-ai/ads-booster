import os
from pathlib import Path

import pytest

from trace_capture.capture.capture_safety import path_has_symlink_component


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [
        (Path(os.sep, "var"), Path(os.sep, "private", "var")),
        (Path(os.sep, "tmp"), Path(os.sep, "private", "tmp")),
    ],
)
def test_path_safety_when_macos_standard_alias_is_used(
    alias: Path,
    canonical: Path,
) -> None:
    # Given a standard macOS filesystem alias rather than a user-controlled redirect
    if not alias.is_symlink() or alias.resolve() != canonical:
        pytest.skip("macOS standard alias is unavailable on this host")

    # When a capture path is checked below that alias
    result = path_has_symlink_component(alias / "trace-capture" / "output.png")

    # Then the trusted system alias is accepted while explicit workspace symlinks remain rejected
    assert result is False
