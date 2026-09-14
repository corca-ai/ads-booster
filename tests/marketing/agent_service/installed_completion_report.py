from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

type ReportValue = (
    str | int | float | bool | Sequence[ReportValue] | Mapping[str, ReportValue] | None
)


def write_json(path: Path, value: Mapping[str, ReportValue]) -> None:
    _ = path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
