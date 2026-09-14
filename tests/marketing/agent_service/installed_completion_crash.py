from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ads_booster.agent.service.task_progress import project_task
from tests.marketing.agent_service import test_application as fixtures
from tests.marketing.agent_service.installed_completion_report import write_json
from tests.marketing.agent_service.test_application import NOW, AskThenStopReasoning

if TYPE_CHECKING:
    from pathlib import Path


def reservation_crash(root: Path, *, restart: bool) -> None:
    database = root / "reservation-crash.sqlite3"
    service = fixtures.build_service(database, AskThenStopReasoning(stop=True))
    if restart:
        before = service.repository.get("trace", "run-one")
        assert before is not None
        checkpoint = project_task(before, service.repository.records("trace", before.run_id))
        assert checkpoint.checkpoint.assessment_calls == 1
        assert checkpoint.checkpoint.decision_calls == 2
        final = service.drive("trace", "run-one", now=NOW)
        after = project_task(final, service.repository.records("trace", final.run_id))
        write_json(
            root / "reservation-restarted.json",
            {
                "pid": os.getpid(),
                "before": checkpoint.checkpoint.model_dump(mode="json"),
                "after": after.checkpoint.model_dump(mode="json"),
                "run": final.model_dump(mode="json"),
            },
        )
        assert final.state.value == "completed"
        assert after.checkpoint.assessment_calls == 2
        assert after.checkpoint.decision_calls == 3
        return

    def crash(point: str) -> None:
        if point == "assessment_reserved":
            write_json(root / "reservation-crashed.json", {"pid": os.getpid(), "point": point})
            os._exit(73)

    service.fault_hook = crash
    _ = service.create(fixtures.run_request(), now=NOW)
    message = "reservation_crash_hook_not_reached"
    raise AssertionError(message)
