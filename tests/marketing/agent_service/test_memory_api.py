"""HTTP memory authority is authenticated, with local SQLite and no external writes."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.contracts.agent_memory import MemoryAccess, MemoryScope
from ads_booster.learning.memory import SQLiteMemoryStore
from ads_booster.channels.http.memory_api import (
    dispatch_memory,
    planner_memory_projection,
)
from ads_booster.channels.http.oauth import OAuthIdentity
from ads_booster.transport.json_types import JsonObject, JsonValue

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 9, 7, tzinfo=UTC)
IDENTITY = OAuthIdentity(tenant_id="team", principal_id="alice")
_STR = TypeAdapter(str)
_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


def draft(note_id: str = "preference") -> JsonObject:
    return {
        "note_id": note_id,
        "category": "preference",
        "domain": "asset_format",
        "text": "calendar needs quiet background",
        "source_ref": "synthetic:review",
        "source_sha256": "a" * 64,
        "expires_at": (NOW + timedelta(days=1)).isoformat(),
    }


def call(  # noqa: PLR0913 - HTTP boundary inputs and server-issued reviewer permission.
    store: SQLiteMemoryStore,
    method: str,
    path: str,
    payload: JsonObject | None = None,
    *,
    identity: OAuthIdentity = IDENTITY,
    allow_review: bool = False,
) -> tuple[int, JsonObject]:
    result = dispatch_memory(
        method,
        path,
        json.dumps(payload or {}).encode(),
        identity=identity,
        store=store,
        now=NOW,
        allow_review=allow_review,
    )
    assert result is not None
    return result


def test_create_and_review_queue_preserve_derived_identity(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    status, created = call(
        store, "POST", "/v1/memories?product_id=trace&campaign_id=japan", draft()
    )
    assert status == 201
    created_note = _OBJECT.validate_python(created["note"])
    assert created_note["author_id"] == "alice"
    assert created_note["stage"] == "candidate"
    assert created_note["scope"] == {
        "workspace_id": "team",
        "product_id": "trace",
        "campaign_id": "japan",
        "work_id": "",
        "member_id": "",
        "session_id": "",
    }
    status, queue = call(store, "GET", "/v1/memories?campaign_id=japan&stage=candidate&limit=1")
    assert status == 200
    assert len(TypeAdapter(list[JsonObject]).validate_python(queue["notes"])) == 1
    assert queue["can_review"] is False
    assert call(store, "GET", "/v1/memories/preference")[0] == 404
    assert call(store, "GET", "/v1/memories/preference?campaign_id=japan")[0] == 200


def test_body_and_query_cannot_forge_scope_or_reviewer(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    forged: list[tuple[str, JsonValue]] = [
        ("workspace_id", "other"),
        ("member_id", "bob"),
        ("session_id", "s1"),
        ("author_id", "bob"),
        ("can_review", True),
        ("stage", "approved"),
        ("scope", {"workspace_id": "other"}),
    ]
    for key, value in forged:
        assert call(store, "POST", "/v1/memories", {**draft(), key: value})[0] == 400
    for parameter in ["workspace_id=other", "member_id=bob", "can_review=true"]:
        assert call(store, "GET", "/v1/memories?" + parameter)[0] == 400
    assert call(store, "GET", "/v1/memories?product_id=trace&product_id=other")[0] == 409
    assert call(store, "GET", "/v1/memories")[1]["notes"] == []


def test_review_requires_trusted_permission_and_exact_digest(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    _, created = call(store, "POST", "/v1/memories", draft())
    review = {"expected_sha256": created["sha256"], "stage": "review"}
    assert call(store, "POST", "/v1/memories/preference/review", review)[0] == 403
    status, reviewed = call(
        store, "POST", "/v1/memories/preference/review", review, allow_review=True
    )
    assert status == 200
    assert (
        call(
            store,
            "POST",
            "/v1/memories/preference/review",
            {"expected_sha256": created["sha256"], "stage": "approved"},
            allow_review=True,
        )[0]
        == 409
    )
    status, approved = call(
        store,
        "POST",
        "/v1/memories/preference/review",
        {"expected_sha256": reviewed["sha256"], "stage": "approved"},
        allow_review=True,
    )
    assert status == 200
    assert approved["authority"] == "data_only_not_execution_approval"
    result = call(store, "POST", "/v1/memories/select", {"query": "calendar", "run_id": "run-1"})
    assert result[0] == 200
    assert len(TypeAdapter(list[JsonObject]).validate_python(result[1]["notes"])) == 1


def test_cross_tenant_and_author_delete_boundaries(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    _, created = call(store, "POST", "/v1/memories", draft())
    other_team = OAuthIdentity(tenant_id="other", principal_id="alice")
    assert call(store, "GET", "/v1/memories", identity=other_team)[1]["notes"] == []
    assert call(store, "GET", "/v1/memories/preference", identity=other_team)[0] == 404
    digest = {"expected_sha256": created["sha256"]}
    bob = OAuthIdentity(tenant_id="team", principal_id="bob")
    assert call(store, "DELETE", "/v1/memories/preference", digest, identity=bob)[0] == 409
    status, deleted = call(store, "DELETE", "/v1/memories/preference", digest)
    assert status == 200
    assert deleted["history_retained"] is True
    assert call(store, "GET", "/v1/memories/preference")[0] == 404


def test_projection_is_bounded_data_and_dispatch_is_optional(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    _, created = call(store, "POST", "/v1/memories", draft())
    digest = _STR.validate_python(created["sha256"])
    for stage in ["review", "approved"]:
        _, updated = call(
            store,
            "POST",
            "/v1/memories/preference/review",
            {"expected_sha256": digest, "stage": stage},
            allow_review=True,
        )
        digest = _STR.validate_python(updated["sha256"])
    access = MemoryAccess(
        scope=MemoryScope(workspace_id="team", product_id="trace"), actor_id="alice"
    )
    projection = planner_memory_projection(
        store, access, run_id="run-1", objective="calendar", now=NOW
    )
    assert "untrusted data" in _STR.validate_python(projection["instruction_boundary"])
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM agent_memory_selections").fetchone()[0] == 1
    assert dispatch_memory("GET", "/v1/runs", b"", identity=IDENTITY, store=store, now=NOW) is None
    response = dispatch_memory(
        "POST", "/v1/memories", b"x" * 24001, identity=IDENTITY, store=store, now=NOW
    )
    assert response is not None
    assert response[0] == 413


def test_replayed_draft_after_restart_preserves_creation_time(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite"
    store = SQLiteMemoryStore(path)
    first = call(store, "POST", "/v1/memories", draft())
    replay = dispatch_memory(
        "POST",
        "/v1/memories",
        json.dumps(draft()).encode(),
        identity=IDENTITY,
        store=SQLiteMemoryStore(path),
        now=NOW + timedelta(minutes=5),
    )
    assert replay == first
    changed = dispatch_memory(
        "POST",
        "/v1/memories",
        json.dumps({**draft(), "text": "changed"}).encode(),
        identity=IDENTITY,
        store=SQLiteMemoryStore(path),
        now=NOW + timedelta(minutes=5),
    )
    assert changed is not None
    assert changed[0] == 409
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM agent_memory_history").fetchone()[0] == 1
