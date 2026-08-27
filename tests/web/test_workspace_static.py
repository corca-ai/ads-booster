from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

_INVOCATION = re.compile(r"^await test\w+\(\);$", re.MULTILINE)
_REPORT = re.compile(r"^workspace static behavior: (\d+) passed$")


def test_workspace_static_behavior_contract() -> None:
    # Given
    script = Path(__file__).with_name("workspace_static_behavior.mjs")
    node = shutil.which("node")
    assert node is not None
    declared = len(_INVOCATION.findall(script.read_text(encoding="utf-8")))

    # When
    result = subprocess.run(  # noqa: S603
        (node, str(script)),
        capture_output=True,
        check=False,
        text=True,
    )

    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    reported = _REPORT.match(result.stdout.strip())
    assert reported is not None, result.stdout
    # Comparing against the file rather than a literal keeps a newly added test from being
    # silently skipped, without a total that has to be edited by hand every time.
    assert int(reported.group(1)) == declared
    assert declared > 0
