"""The execution kernel is expected to be replaced; this pins its blast radius.

`agent/runs` and `connectors/` are the durable Agent kernel and the Trace connector. Code
that produces candidates — the instruction engine, the background judge, the local
composition, and the Web layer in front of them — must reach them only through the three
adapters in `candidate_generation/kernel/`. If this test fails, an import escaped an
adapter and swapping the kernel just became a wider change than it should be.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_SOURCE_ROOT: Final = Path(__file__).resolve().parents[2] / "src" / "ads_booster"
_KERNEL_PACKAGE: Final = _SOURCE_ROOT / "candidate_generation" / "kernel"
_KERNEL_MODULES: Final = ("ads_booster.agent.runs", "ads_booster.connectors")

# Directories whose modules must stay free of kernel imports. `web/app.py` is the
# composition root and `web/queue.py` owns Agent runs of its own, so both are named
# exceptions rather than silent ones.
_GUARDED: Final = (
    _SOURCE_ROOT / "candidate_generation",
    _SOURCE_ROOT / "search",
)
_GUARDED_FILES: Final = (_SOURCE_ROOT / "web" / "candidate_router.py",)
_EXEMPT: Final = (_KERNEL_PACKAGE,)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            found.append(node.module)
        elif isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
    return found


def _guarded_files() -> list[Path]:
    files = list(_GUARDED_FILES)
    for directory in _GUARDED:
        files.extend(
            path
            for path in sorted(directory.rglob("*.py"))
            if not any(path.is_relative_to(exempt) for exempt in _EXEMPT)
        )
    return files


def test_only_the_kernel_adapters_import_the_execution_kernel() -> None:
    # Given every module that produces candidates, outside the three adapters
    guarded = _guarded_files()
    assert guarded, "the guarded set must not be empty or this test proves nothing"

    # When their imports are read
    leaked = {
        path.relative_to(_SOURCE_ROOT).as_posix(): sorted(
            module for module in _imports(path) if module.startswith(_KERNEL_MODULES)
        )
        for path in guarded
    }

    # Then none of them names the kernel or a connector
    assert {path: modules for path, modules in leaked.items() if modules} == {}


def test_the_kernel_adapters_are_the_three_named_coupling_points() -> None:
    # Given the adapter package
    modules = sorted(path.stem for path in _KERNEL_PACKAGE.glob("*.py") if path.stem != "__init__")

    # Then it holds exactly one module per coupling point. Adding a fourth is a design
    # decision, not an accident, so it should have to be made here first.
    assert modules == ["background_seam", "candidate_batch", "image_stage"]


def test_the_connector_does_not_reach_back_into_candidate_generation() -> None:
    """The connector may share the draft schema, but never our behaviour.

    `candidate_generation.models` holds `CandidateDraft`, the shape both generators
    produce; the connector's tool schema is derived from it and that is a data contract.
    Anything else — the engine, the judge, the factories — would make replacing the
    connector a change to candidate generation too.
    """
    # Given the Trace connector, which the kernel replacement will rewrite
    connector = sorted((_SOURCE_ROOT / "connectors").rglob("*.py"))
    assert connector

    # When its imports are read
    leaked = {
        path.relative_to(_SOURCE_ROOT).as_posix(): sorted(
            module
            for module in _imports(path)
            if module.startswith("ads_booster.candidate_generation")
            and module != "ads_booster.candidate_generation.models"
        )
        for path in connector
    }

    # Then the dependency runs one way only: candidate generation composes the connector,
    # never the reverse, so replacing the connector cannot drag the judge along with it.
    assert {path: modules for path, modules in leaked.items() if modules} == {}
