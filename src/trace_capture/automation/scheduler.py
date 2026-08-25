from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from trace_capture.automation.models import QueueRecord
    from trace_capture.automation.store import AutomationQueue


@dataclass(frozen=True, slots=True)
class QueueScheduler:
    queue: AutomationQueue
    worker_id: str
    lease_seconds: float

    def poll(self, now: datetime) -> QueueRecord | None:
        return self.queue.claim_due(
            worker_id=self.worker_id,
            now=now,
            lease_seconds=self.lease_seconds,
        )
