"""Quiescence shared by HTTP, queue recovery, delivery and scheduled work."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

UPDATE_PROTOCOL = 1


@dataclass
class MaintenanceGate:
    path: Path | None = None
    release: str = "unmanaged"
    active: int = 0
    lock: Lock = field(default_factory=Lock)

    @contextmanager
    def work(self) -> Generator[bool]:
        with self.lock:
            admitted = self.path is None or not self.path.exists()
            if admitted:
                self.active += 1
        try:
            yield admitted
        finally:
            if admitted:
                with self.lock:
                    self.active -= 1

    def health(self) -> dict[str, str | int | bool]:
        with self.lock:
            return {
                "update_protocol": UPDATE_PROTOCOL,
                "release": self.release,
                "maintenance": self.path is not None and self.path.exists(),
                "active": self.active,
            }
