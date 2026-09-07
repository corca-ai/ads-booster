from __future__ import annotations

import json
from typing import TYPE_CHECKING

from tests.marketing.agent_service.test_http_api import (
    NOW,
    _api,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from pathlib import Path


def test_http_continuation_and_prepared_delivery_are_same_run_and_no_execution(
    tmp_path: Path,
) -> None:
    api = _api(tmp_path)
    created = api.dispatch(
        "POST",
        "/v1/runs",
        authorization="Bearer secret",
        now=NOW,
        body=json.dumps(
            {
                "run_id": "small-job",
                "goal": {"objective": "背景の修正", "success_criteria": ["review"]},
                "budget": {"max_tool_calls": 4, "max_cost_units": 10},
            }
        ).encode(),
    )
    assert created.status == 202
    body = json.dumps(
        {"event_id": "followup-1", "note": "캐릭터를 유지해줘", "action": "revise"}
    ).encode()
    continued = api.dispatch(
        "POST", "/v1/runs/small-job/continuation", authorization="Bearer secret", body=body, now=NOW
    )
    assert continued.status == 200
    assert len(api.service.repository.list_runs("trace")) == 1
    packet = {
        "proposal_id": "edit-plan",
        "rationale": "보존 범위를 확인하고 제작 검토",
        "target": {
            "kind": "production",
            "input_sha256": "a" * 64,
            "instructions": "윗쪽 여백만 늘려줘",
            "preserve": ["캐릭터"],
            "change": ["위 여백"],
            "max_cost_units": 4,
        },
    }
    proposal = api.dispatch(
        "POST",
        "/v1/runs/small-job/delivery",
        authorization="Bearer secret",
        body=json.dumps(packet).encode(),
        now=NOW,
    )
    assert proposal.status == 201
    assert isinstance(proposal.body, dict)
    assert proposal.body["external_execution_enabled"] is False
    read = api.dispatch(
        "GET", "/v1/runs/small-job/delivery/edit-plan", authorization="Bearer secret", now=NOW
    )
    assert read.body == proposal.body
    denied = api.dispatch(
        "GET", "/v1/runs/small-job/delivery/edit-plan", authorization=None, now=NOW
    )
    assert denied.status == 401
