from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import AgentBudget, AgentGoal
from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.agent.service.application import MarketingAgentService
from ads_booster.channels.http.jobs import AgentJobs, WebJob
from ads_booster.agent.service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.agent.runtime import SqliteSessionStore
from tests.marketing.agent_service.test_http_api import StopReasoning

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 9, 7, tzinfo=UTC)


def jobs(root: Path) -> AgentJobs:
    db = root / "agent.sqlite3"
    return AgentJobs(
        MarketingAgentService(
            SqliteAgentRunRepository(db),
            ToolRegistry(()),
            StopReasoning(),
            {},
            SqliteSessionStore(db),
        )
    )


def test_async_admission_is_durable_idempotent_and_tenant_scoped(tmp_path: Path) -> None:
    owner = jobs(tmp_path)
    job = WebJob(
        job_id="job1",
        run_id="run1",
        action="create",
        goal=AgentGoal(objective="Find useful evidence", success_criteria=("Sources",), context={}),
        budget=AgentBudget(max_tool_calls=3, max_cost_units=10),
    )
    assert owner.enqueue("team", "member", job)["state"] == "pending"
    assert owner.service.repository.get("team", "run1") is None
    with pytest.raises(ValueError, match="not_found"):
        _ = owner.status("other", "job1")
    with pytest.raises(ValueError, match="idempotency"):
        _ = owner.enqueue("team", "different", job)
    restarted = jobs(tmp_path)
    restarted.recover()
    assert restarted.work_once(now=NOW)
    assert restarted.status("team", "job1")["state"] == "done"
    assert restarted.enqueue("team", "member", job)["state"] == "done"
    assert not restarted.work_once(now=NOW)
    assert len(restarted.service.repository.list_runs("team")) == 1
