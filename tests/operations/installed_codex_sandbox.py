"""Exercise the installed Linux sandbox without credentials or model/image calls."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from ads_booster.providers.codex_image_edit import image_edit_command


def main() -> None:
    assert sys.platform == "linux", "Linux execution is required"
    executable = Path(shutil.which("codex") or "")
    assert executable.is_file(), "Installed Codex is required"
    with TemporaryDirectory(prefix="trace-sandbox-proof-", dir=Path.home()) as directory:
        root = Path(directory)
        workspace = root / "job"
        workspace.mkdir()
        _ = (workspace / "SKILL.md").write_text("frozen skill fixture")
        secret = root / "outside.txt"
        _ = secret.write_text("private fixture")
        command = image_edit_command(
            executable, workspace=workspace, allow_shell=True, read_paths=(Path(sys.base_prefix),)
        )
        config = [
            value for index, value in enumerate(command) if index and command[index - 1] == "-c"
        ]
        args = [str(executable), "sandbox", "linux"]
        for value in config:
            args.extend(("-c", value))
        script = (
            "from pathlib import Path; "
            "assert Path('SKILL.md').read_text() == 'frozen skill fixture'; "
            "Path('result.txt').write_text('helper executed'); "
            "print('TRACE_SANDBOX_OK')"
        )
        result = subprocess.run(  # noqa: S603 - installed trusted binary and fixed no-model probe.
            [*args, "--", str(Path(sys.executable).resolve()), "-c", script],
            cwd=workspace,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"sandbox command failed: {result.stderr[-2000:]}"
        assert "TRACE_SANDBOX_OK" in result.stdout
        assert (workspace / "result.txt").read_text() == "helper executed"
        denied = subprocess.run(  # noqa: S603 - installed trusted binary and fixed no-model probe.
            [*args, "--", "/bin/cat", str(secret)],
            cwd=workspace,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert denied.returncode != 0
        assert "private fixture" not in denied.stdout
        print(  # noqa: T201 - CI proof receipt.
            json.dumps(
                {
                    "linux_sandbox": "passed",
                    "frozen_skill_read": True,
                    "python_helper": True,
                    "unrelated_read_denied": True,
                    "model_calls": 0,
                }
            )
        )


if __name__ == "__main__":
    main()
