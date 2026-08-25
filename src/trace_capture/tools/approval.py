from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, slots=True)
class InteractiveApproval:
    input_fn: Callable[[str], str]
    output_fn: Callable[[str], None]

    def request(self, action: str, detail: str) -> bool:
        answer = self.input_fn(f"Approve {action}: {detail} [y/N] ")
        approved = answer.strip().lower() in {"y", "yes"}
        if not approved:
            self.output_fn(f"Denied: {action}")
        return approved


@dataclass(frozen=True, slots=True)
class DenyApproval:
    def request(self, action: str, detail: str) -> bool:
        _ = (action, detail)
        return False
