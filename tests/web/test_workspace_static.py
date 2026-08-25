from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_workspace_static_behavior_contract() -> None:
    # Given
    script = Path(__file__).with_name("workspace_static_behavior.mjs")
    node = shutil.which("node")
    assert node is not None

    # When
    result = subprocess.run(  # noqa: S603
        (node, str(script)),
        capture_output=True,
        check=False,
        text=True,
    )

    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "workspace static behavior: 22 passed"
