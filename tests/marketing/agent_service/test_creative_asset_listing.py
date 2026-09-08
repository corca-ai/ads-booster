# pyright: reportPrivateUsage=false
"""Authenticated collection discovery exposes only current same-work asset metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ads_booster.contracts.creative_work import CreativeScope
from ads_booster.marketing.agent_service.creative_api import dispatch_creative
from ads_booster.marketing.agent_service.creative_assets import SqliteCreativeAssetRepository
from ads_booster.marketing.agent_service.oauth import OAuthIdentity
from tests.marketing.agent_service.test_creative_api import IDENTITY, NOW, _body, _service

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject


def test_listing_discovers_latest_linked_revision_without_image_bytes(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)

    def call(
        method: str, body: bytes = b"", identity: OAuthIdentity = IDENTITY
    ) -> tuple[int, JsonObject] | None:
        return dispatch_creative(
            method,
            "/v1/runs/work/assets",
            body,
            identity=identity,
            service=service,
            artifact_root=tmp_path / "assets",
            now=NOW,
        )

    for revision in (1, 2):
        result = call("POST", _body(revision=revision, resume=False))
        assert result is not None
        assert result[0] == 201
    result = call("GET")
    assert result is not None
    assert result[0] == 200
    assets = result[1]["assets"]
    assert isinstance(assets, list)
    assert len(assets) == 1
    assert isinstance(assets[0], dict)
    assert assets[0]["revision"] == 2
    assert assets[0]["origin"] == "human_reported"
    assert assets[0]["stale"] is False
    assert "image_base64" not in assets[0]
    repository = SqliteCreativeAssetRepository(
        service.repository.database_path, tmp_path / "assets"
    )
    scope = CreativeScope(workspace_id="trace", product_id="trace")
    original = repository.get(scope, "background")
    assert original is not None
    repository.add(
        original.model_copy(update={"asset_id": "unlinked", "revision": 1}), actor_scope=scope
    )
    assert call("GET") == result
    # Metadata discovery is deliberately not byte verification; delivery still verifies it.
    _ = (repository.artifact_root / original.relative_path).write_bytes(b"corrupted")
    assert call("GET") == result
    readback = dispatch_creative(
        "GET",
        "/v1/runs/work/assets/background",
        b"",
        identity=IDENTITY,
        service=service,
        artifact_root=tmp_path / "assets",
        now=NOW,
    )
    assert readback is not None
    assert readback[0] == 400
    denied = call("GET", identity=OAuthIdentity("other", "member"))
    assert denied is not None
    assert denied[0] == 404
