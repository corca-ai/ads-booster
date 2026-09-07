"""Creative lineage privacy, tamper and selective invalidation boundaries."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.creative_work import AssetParent, CreativeAsset, CreativeScope
from ads_booster.marketing.agent_service.creative_assets import SqliteCreativeAssetRepository

if TYPE_CHECKING:
    from pathlib import Path


def _asset(root: Path, asset_id: str, revision: int = 1, **changes: object) -> CreativeAsset:
    filename = f"{asset_id}-{revision}.png"
    payload = f"synthetic fixture {asset_id} {revision}".encode()
    _ = (root / filename).write_bytes(payload)
    return CreativeAsset.model_validate(
        {
            "asset_id": asset_id,
            "revision": revision,
            "scope": {"workspace_id": "trace", "product_id": "trace"},
            "kind": "background_asset",
            "relative_path": filename,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "source": "synthetic fixture",
            "use_terms": "test only",
            "data_permission": "synthetic",
            "permission_evidence": "fixture contains no personal data",
            "origin": "human_reported",
            **changes,
        }
    )


def _parent(asset: CreativeAsset) -> tuple[AssetParent, ...]:
    return (AssetParent(asset_id=asset.asset_id, revision=asset.revision, sha256=asset.sha256),)


def test_selective_locale_revision_then_original_invalidation_survives_restart(
    tmp_path: Path,
) -> None:
    repo = SqliteCreativeAssetRepository(tmp_path / "assets.db", tmp_path)
    original = _asset(tmp_path, "original")
    ja = _asset(tmp_path, "ja", parents=_parent(original), locale="ja-JP")
    en = _asset(tmp_path, "en", parents=_parent(original), locale="en-US")
    mockup = _asset(tmp_path, "mockup", parents=_parent(ja), kind="phone_mockup")
    for asset in (original, ja, en, mockup):
        repo.add(asset, actor_scope=asset.scope)
    ja2 = _asset(tmp_path, "ja", 2, parents=_parent(original), locale="ja-JP")
    repo.add(ja2, actor_scope=ja2.scope)
    assert not repo.is_stale(en.scope, "en")
    assert repo.is_stale(mockup.scope, "mockup")
    original2 = _asset(tmp_path, "original", 2)
    repo.add(original2, actor_scope=original2.scope)
    reopened = SqliteCreativeAssetRepository(tmp_path / "assets.db", tmp_path)
    assert reopened.is_stale(ja.scope, "ja")
    assert reopened.is_stale(en.scope, "en")
    assert reopened.get(original.scope, "original", 1) == original
    with pytest.raises(ValueError, match="parent"):
        reopened.add(_asset(tmp_path, "new", parents=_parent(original)), actor_scope=original.scope)


def test_scope_before_file_read_and_private_read_only_shared(tmp_path: Path) -> None:
    repo = SqliteCreativeAssetRepository(tmp_path / "assets.db", tmp_path)
    shared = _asset(tmp_path, "shared")
    private_scope = CreativeScope(
        workspace_id="trace", product_id="trace", member_id="a", session_id="s"
    )
    private = _asset(tmp_path, "private", scope=private_scope)
    repo.add(shared, actor_scope=shared.scope)
    repo.add(private, actor_scope=private_scope)
    assert repo.get(private_scope, "shared", target_scope=shared.scope) == shared
    with pytest.raises(ValueError, match="scope_write_denied"):
        repo.add(shared, actor_scope=private_scope)
    with pytest.raises(ValueError, match="parent_missing"):
        repo.add(_asset(tmp_path, "promoted", parents=_parent(private)), actor_scope=shared.scope)
    (tmp_path / private.relative_path).unlink()
    for actor in (
        shared.scope,
        private_scope.model_copy(update={"member_id": "b"}),
        private_scope.model_copy(update={"session_id": "other"}),
        private_scope.model_copy(update={"workspace_id": "other"}),
    ):
        with pytest.raises(ValueError, match="scope_denied"):
            _ = repo.get(actor, "private", target_scope=private_scope)


def test_root_and_digest_and_immutable_revision(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    repo = SqliteCreativeAssetRepository(tmp_path / "assets.db", root)
    asset = _asset(root, "a")
    repo.add(asset, actor_scope=asset.scope)
    repo.add(asset, actor_scope=asset.scope)
    with pytest.raises(ValueError, match="revision_conflict"):
        repo.add(asset.model_copy(update={"source": "changed"}), actor_scope=asset.scope)
    with pytest.raises(ValueError, match="outside_root"):
        repo.add(
            asset.model_copy(update={"relative_path": "../outside.png"}), actor_scope=asset.scope
        )
    outside = tmp_path / "outside.png"
    _ = outside.write_text("outside")
    (root / "link.png").symlink_to(outside)
    with pytest.raises(ValueError, match="outside_root"):
        repo.add(asset.model_copy(update={"relative_path": "link.png"}), actor_scope=asset.scope)
    _ = (root / asset.relative_path).write_text("tampered")
    with pytest.raises(ValueError, match="digest_mismatch"):
        _ = repo.get(asset.scope, asset.asset_id)


def test_human_report_and_locale_qa_never_assert_product_proof(tmp_path: Path) -> None:
    capture = _asset(tmp_path, "capture", kind="native_trace_capture")
    assert not capture.product_proof_verified
    with pytest.raises(ValueError, match="not_system_verification"):
        _ = _asset(
            tmp_path,
            "bad",
            qa=[
                {
                    "method": "deterministic",
                    "status": "passed",
                    "evidence": "said done",
                    "reviewer": "a",
                }
            ],
        )
    with pytest.raises(ValueError, match="qa_locale_mismatch"):
        _ = _asset(
            tmp_path,
            "bad-locale",
            locale="ja-JP",
            qa=[
                {
                    "locale": "en-US",
                    "method": "human_review",
                    "status": "passed",
                    "evidence": "read",
                    "reviewer": "a",
                }
            ],
        )
    with pytest.raises(ValueError, match="worker_receipt_required"):
        _ = _asset(tmp_path, "bad-receipt", origin="worker_receipt")
