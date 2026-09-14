"""Reentrant per-Run serialization without holding unrelated model calls."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock, RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator


@dataclass(slots=True)
class RunLocks:
    _guard: Lock = field(default_factory=Lock)
    _entries: dict[tuple[str, str], tuple[RLock, int]] = field(default_factory=dict)

    @contextmanager
    def hold(self, tenant_id: str, run_id: str) -> Generator[None]:
        key = (tenant_id, run_id)
        with self._guard:
            lock, references = self._entries.get(key, (RLock(), 0))
            self._entries[key] = (lock, references + 1)
        try:
            with lock:
                yield
        finally:
            with self._guard:
                _, references = self._entries[key]
                if references == 1:
                    del self._entries[key]
                else:
                    self._entries[key] = (lock, references - 1)
