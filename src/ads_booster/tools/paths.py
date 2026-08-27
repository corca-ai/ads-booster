from __future__ import annotations

from pathlib import Path


def resolve_workspace_path(workspace: Path, relative_path: str) -> Path | None:
    candidate = Path(relative_path)
    if candidate.is_absolute():
        return None
    try:
        root = workspace.resolve()
        resolved = (root / candidate).resolve(strict=False)
    except OSError:
        return None
    if not resolved.is_relative_to(root):
        return None
    if _contains_symlink(resolved, root):
        return None
    return resolved


def _contains_symlink(path: Path, root: Path) -> bool:
    current = path
    while current != root:
        if current.is_symlink():
            return True
        current = current.parent
    return root.is_symlink()
