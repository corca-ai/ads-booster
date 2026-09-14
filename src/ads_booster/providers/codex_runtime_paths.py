"""Read grants for the installed Codex runtime, excluding login and configuration."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from pydantic import TypeAdapter

from ads_booster.transport.json_types import JsonObject

_PACKAGE: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


def runtime_read_paths(executable: Path) -> tuple[Path, ...]:
    resolved = executable.resolve()
    paths = [executable.absolute(), resolved]
    if resolved.name not in {"codex", "codex.js"}:
        return tuple(dict.fromkeys(paths))
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    # Codex creates this invocation's random arg0 directory after process start.
    paths.append((home / "tmp/arg0").resolve())
    if resolved.parent.name == "bin":
        root = resolved.parent.parent
        if (root / "codex-package.json").is_file():
            paths.extend((resolved.parent, root / "codex-resources", root / "codex-path"))
        manifest = root / "package.json"
        if manifest.is_file():
            package = _PACKAGE.validate_json(manifest.read_text())
            if package.get("name") == "@openai/codex":
                paths.append(root)
                for parent in (root / "node_modules/@openai", root.parent):
                    paths.extend(p for p in parent.glob("codex-*") if p.is_dir())
                node = shutil.which("node")
                if node:
                    paths.extend((Path(node).absolute(), Path(node).resolve()))
    return tuple(dict.fromkeys(paths))
