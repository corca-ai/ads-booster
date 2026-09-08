"""Real authenticated API and durable queue; provider is an explicit failing fake."""

from __future__ import annotations

import json
from contextlib import closing
from typing import TYPE_CHECKING, cast

import pytest

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.marketing.agent_service.creative_image_edit import ImageEditJob
from ads_booster.marketing.agent_service.http_api import MarketingAgentApi
from tests.marketing.agent_service.test_creative_image_edit import approve, setup

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("reviewer", [False, True])
def test_api_current_reviewer_and_exact_abandonment(tmp_path: Path, reviewer: bool) -> None:
    tool, provider = setup(tmp_path)
    provider.fail = True
    approve(tool)
    _ = tool.work_once()
    with closing(tool._db()) as db:  # pyright: ignore[reportPrivateUsage]
        job = ImageEditJob.model_validate_json(
            cast("str", db.execute("SELECT data FROM image_edit_jobs").fetchone()[0])
        )
    api = MarketingAgentApi(
        tool.service, "tenant-a", "reviewer", "local-token", approval_authorizer=lambda _: reviewer
    )
    route = f"/v1/runs/run-one/image-edits/{job.operation_id}"
    assert api.dispatch("GET", route, authorization=None).status == 401
    status = api.dispatch("GET", route, authorization="Bearer local-token")
    assert status.status == 200
    request = {
        "invocation_sha256": contract_sha256(job.invocation),
        "note": "Cannot verify provider outcome; stop tracking this operation.",
    }
    forged = api.dispatch(
        "POST",
        route + "/abandon",
        authorization="Bearer local-token",
        body=json.dumps({**request, "can_review": True}).encode(),
    )
    assert forged.status in (400, 403, 409, 422)
    response = api.dispatch(
        "POST",
        route + "/abandon",
        authorization="Bearer local-token",
        body=json.dumps(request).encode(),
    )
    assert response.status == (200 if reviewer else 403)
    assert provider.calls == 1


def test_abandonment_completion_crash_retries_only_projection(tmp_path: Path) -> None:
    tool, provider = setup(tmp_path)
    provider.fail = True
    approve(tool)
    _ = tool.work_once()
    with closing(tool._db()) as db:  # pyright: ignore[reportPrivateUsage]
        job = ImageEditJob.model_validate_json(
            cast("str", db.execute("SELECT data FROM image_edit_jobs").fetchone()[0])
        )

    def fail(*_args: str) -> None:
        message = "projection_failed"
        raise RuntimeError(message)

    tool.on_completed = fail
    with pytest.raises(RuntimeError):
        _ = tool.abandon(
            "tenant-a",
            "run-one",
            job.operation_id,
            invocation_sha256=contract_sha256(job.invocation),
            reviewer_id="reviewer",
            note="Unknown; abandon",
        )
    tool.on_completed = None
    _ = tool.work_once()
    status = tool.operation_status("tenant-a", "run-one", job.operation_id)
    assert status["canonical_settled"] is True
    assert status["outcome_unknown"] is True
    assert provider.calls == 1


def test_abandonment_rejects_queued_and_other_tenant(tmp_path: Path) -> None:
    tool, provider = setup(tmp_path)
    approve(tool)
    with closing(tool._db()) as db:  # pyright: ignore[reportPrivateUsage]
        job = ImageEditJob.model_validate_json(
            cast("str", db.execute("SELECT data FROM image_edit_jobs").fetchone()[0])
        )
    with pytest.raises(ValueError, match="image_edit_not_uncertain"):
        _ = tool.abandon(
            "tenant-a",
            "run-one",
            job.operation_id,
            invocation_sha256=contract_sha256(job.invocation),
            reviewer_id="reviewer",
            note="Not yet started",
        )
    with pytest.raises(ValueError, match="image_edit_operation_not_found"):
        _ = tool.operation_status("other-tenant", "run-one", job.operation_id)
    assert provider.calls == 0
