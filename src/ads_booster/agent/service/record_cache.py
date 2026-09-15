from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Lock

from ads_booster.contracts.agent_run import AgentRecord


@dataclass(slots=True)
class RecordValidationCache:
    """Cache exact validated database JSON; callers never share mutable payloads."""

    max_bytes: int = 8 * 1024 * 1024
    max_entries: int = 256
    _entries: OrderedDict[str, tuple[AgentRecord, int]] = field(default_factory=OrderedDict)
    _bytes: int = 0
    _lock: Lock = field(default_factory=Lock)

    def parse(self, raw: str) -> AgentRecord:
        with self._lock:
            entry = self._entries.get(raw)
            if entry is not None:
                self._entries.move_to_end(raw)
                record = entry[0]
            else:
                record = AgentRecord.model_validate_json(raw)
                size = len(raw.encode("utf-8"))
                if size <= self.max_bytes and self.max_entries > 0:
                    while self._entries and (
                        self._bytes + size > self.max_bytes
                        or len(self._entries) >= self.max_entries
                    ):
                        _, (_, removed_size) = self._entries.popitem(last=False)
                        self._bytes -= removed_size
                    self._entries[raw] = (record, size)
                    self._bytes += size
        return record.model_copy(deep=True)
