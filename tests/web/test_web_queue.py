from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
from PIL import Image
from pydantic import TypeAdapter

from trace_capture.automation import (
    AutomationQueue,
    CampaignRecord,
    CampaignState,
    QueueRecord,
    QueueState,
)
from trace_capture.automation.models import QueueCompletion
from trace_capture.web.app import create_app
from trace_capture.workspace import (
    AssetCreate,
    ContextCreate,
    ContextKind,
    ProvisionedMember,
    ProvisionedWorkspace,
    SqliteWorkspaceStore,
)

if TYPE_CHECKING:
    from pathlib import Path


def _login(client: TestClient, workspace: ProvisionedWorkspace, member: ProvisionedMember) -> None:
    response = client.post(
        "/api/auth/login",
        json={
            "workspace_id": workspace.workspace.workspace_id,
            "member_id": member.member.member_id,
            "workspace_code": workspace.access_code,
            "member_code": member.invite_code,
        },
    )
    assert response.status_code == 200


def _bundle(request_id: str = "manual-web") -> dict[str, object]:
    return {
        "schema_version": "trace.marketing-context.v1",
        "request_id": request_id,
        "persona": {
            "persona_id": "persona-web",
            "country": "KR",
            "locale": "ko-KR",
            "age_group": "25-34",
            "occupation": "Designer",
            "traits": ["precise"],
            "interests": ["productivity"],
        },
        "promotion_material": {
            "promotion_material_id": "promo-web",
            "feature": "Trace lock screen",
            "concept": "quiet focus",
            "tone": ["calm"],
        },
        "reference_date": "2026-08-24T00:00:00Z",
        "device": {
            "kind": "simulator",
            "udid": "E1FB798D-79E6-4B25-A987-D298A4FD122A",
            "platform_version": "26.5",
            "device_name": "iPhone 17 Pro",
        },
    }


def test_authenticated_member_can_enqueue_and_list_workspace_queue(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    client = TestClient(create_app(tmp_path, session_secret=b"s" * 32), base_url="https://test")
    _login(client, workspace, member)

    # When
    created = client.post(
        "/api/queue",
        json={"idempotency_key": "manual-web", "bundle": _bundle()},
    )
    listed = client.get("/api/queue")

    # Then
    assert created.status_code == 201
    created_record = QueueRecord.model_validate_json(created.content)
    listed_records = TypeAdapter(tuple[QueueRecord, ...]).validate_json(listed.content)
    assert created_record.state is QueueState.SUBMITTED
    assert listed.status_code == 200
    assert tuple(record.queue_id for record in listed_records) == (created_record.queue_id,)


def test_authenticated_member_can_start_generation_from_a_marketing_context(
    tmp_path: Path,
) -> None:
    # Given an authenticated member and a typed marketing context
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    client = TestClient(create_app(tmp_path, session_secret=b"s" * 32), base_url="https://test")
    _login(client, workspace, member)

    # When the member submits the generation context
    response = client.post("/api/generation", json={"bundle": _bundle("automatic-web")})

    # Then the service immediately persists a submitted generation queue record
    assert response.status_code == 201
    record = QueueRecord.model_validate_json(response.content)
    assert record.idempotency_key == "automatic-web"
    assert record.state is QueueState.SUBMITTED
    listed = client.get("/api/queue")
    listed_records = TypeAdapter(tuple[QueueRecord, ...]).validate_json(listed.content)
    assert tuple(item.queue_id for item in listed_records) == (record.queue_id,)


def test_authenticated_member_can_start_continuous_campaign_from_saved_contexts(
    tmp_path: Path,
) -> None:
    # Given saved persona, promotion, and reference records in an authenticated workspace
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    persona = store.create_context(
        workspace.workspace.workspace_id,
        ContextCreate(
            kind=ContextKind.PERSONA,
            title="Japanese student",
            body=json.dumps(
                {
                    "persona_id": "student",
                    "country": "JP",
                    "locale": "ja-JP",
                    "age_group": "20s",
                    "occupation": "university_student",
                    "traits": ["focused"],
                    "interests": ["study"],
                },
                ensure_ascii=False,
            ),
        ),
    )
    promotion = store.create_context(
        workspace.workspace.workspace_id,
        ContextCreate(
            kind=ContextKind.PROMOTION,
            title="Exam week",
            body=json.dumps(
                {
                    "promotion_material_id": "exam",
                    "feature": "lock_screen_schedule",
                    "concept": "exam_week",
                    "tone": ["calm"],
                    "trace_items": ["統計学 2限", "レポート提出", "ゼミ準備"],
                },
                ensure_ascii=False,
            ),
        ),
    )
    reference_path = tmp_path / "assets" / "exam-desk.png"
    reference_path.parent.mkdir()
    Image.new("RGB", (4, 6), (80, 120, 160)).save(reference_path, format="PNG")
    reference_content = reference_path.read_bytes()
    reference = store.create_asset(
        workspace.workspace.workspace_id,
        AssetCreate(
            context_id=None,
            filename="exam-desk.png",
            media_type="image/png",
            relative_path="assets/exam-desk.png",
            sha256=sha256(reference_content).hexdigest(),
            size_bytes=len(reference_content),
        ),
    )
    client = TestClient(create_app(tmp_path, session_secret=b"s" * 32), base_url="https://test")
    _login(client, workspace, member)

    # When the member starts an unlimited campaign from those saved records
    created = client.post(
        "/api/campaigns",
        json={
            "name": "Exam week variations",
            "persona_context_id": persona.context_id,
            "promotion_context_id": promotion.context_id,
            "reference_asset_ids": [reference.asset_id],
            "reference_date": "2026-08-25T00:00:00Z",
            "device": _bundle()["device"],
            "variation_count": None,
        },
    )
    listed = client.get("/api/campaigns")

    # Then the server freezes the selected context and exposes an active durable campaign
    assert created.status_code == 201, created.text
    campaign = CampaignRecord.model_validate_json(created.content)
    assert campaign.state is CampaignState.ACTIVE
    assert campaign.persona.persona_id == "student"
    assert campaign.promotion_material.trace_items == (
        "統計学 2限",
        "レポート提出",
        "ゼミ準備",
    )
    assert tuple(item.reference_id for item in campaign.reference_images) == (reference.asset_id,)
    assert TypeAdapter(tuple[CampaignRecord, ...]).validate_json(listed.content) == (campaign,)


def test_review_endpoint_accepts_fixture_result_and_rejects_stale_revision(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    queue = AutomationQueue(tmp_path)
    client = TestClient(create_app(tmp_path, session_secret=b"s" * 32), base_url="https://test")
    _login(client, workspace, member)
    created_response = client.post(
        "/api/queue",
        json={"idempotency_key": "review-web", "bundle": _bundle("review-web")},
    )
    created = QueueRecord.model_validate_json(created_response.content)
    claimed = queue.claim_due(worker_id="fixture", now=datetime.now(UTC), lease_seconds=30)
    assert claimed is not None
    running = queue.start(
        claimed.queue_id,
        worker_id="fixture",
        expected_revision=claimed.revision,
        now=datetime.now(UTC),
    )
    review = queue.finish(
        running,
        completion=QueueCompletion(
            state=QueueState.REVIEW,
            artifact_path="fixtures/final.png",
            artifact_sha256="a" * 64,
        ),
        now=datetime.now(UTC),
    )
    assert created.queue_id == review.queue_id

    # When
    accepted = client.post(
        f"/api/queue/{review.queue_id}/review",
        json={"accepted": True, "expected_revision": review.revision},
    )
    stale = client.post(
        f"/api/queue/{review.queue_id}/review",
        json={"accepted": False, "expected_revision": review.revision},
    )

    # Then
    assert accepted.status_code == 200
    assert QueueRecord.model_validate_json(accepted.content).state is QueueState.ACCEPTED
    assert stale.status_code == 409


def test_queue_routes_require_authentication(tmp_path: Path) -> None:
    # Given
    client = TestClient(create_app(tmp_path, session_secret=b"s" * 32))

    # When / Then
    assert client.get("/api/queue").status_code == 401
    assert client.post("/api/queue", json={}).status_code == 401
