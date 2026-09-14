"""Resolve receipt-bound image results to scoped, verified Slack attachments."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ads_booster.contracts.creative_work import CreativeScope
from ads_booster.contracts.trace_post import TracePostSuccess
from ads_booster.creative.creative_asset_links import asset_links
from ads_booster.creative.creative_assets import SqliteCreativeAssetRepository
from ads_booster.tools.image_generation import CAPABILITY, read_artifact

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.channels.slack_conversations import Conversation
    from ads_booster.contracts.agent_run import AgentRecord
    from ads_booster.transport.json_types import JsonObject

IMAGE_CAPABILITIES = frozenset({CAPABILITY, "creative.trace_post"})
_COUNTRIES = {"kr": "한국", "jp": "일본", "tw": "대만"}
_ROLES = {"final": "배경화면", "scene": "사용 장면"}
_MAX_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SlackImage:
    key: str
    data: bytes
    filename: str
    title: str


def image_result(
    root: Path,
    database: Path,
    record: AgentRecord,
    output: JsonObject,
    conversation: Conversation,
) -> tuple[list[SlackImage], str]:
    if conversation.private:
        msg = "slack_image_scope_invalid"
        raise ValueError(msg)
    if record.payload.get("capability_id") == CAPABILITY:
        digest = str(output.get("artifact_sha256", ""))
        return [
            SlackImage(digest, read_artifact(root, digest), "marketing-image.png", "마케팅 이미지")
        ], ""
    if output.get("schema_version") != "trace.trace-post-success.v1":
        return [], ""
    result = TracePostSuccess.model_validate(output)
    repository = SqliteCreativeAssetRepository(database, database.parent / "artifacts")
    scope = CreativeScope(workspace_id=conversation.tenant_id, product_id="trace")
    images: list[SlackImage] = []
    for item in result.assets:
        reference = item.asset
        with asset_links(database) as db:
            linked = cast(
                "tuple[int] | None",
                db.execute(
                    """SELECT 1 FROM creative_run_assets
                WHERE tenant_id=? AND run_id=? AND asset_id=? AND revision=?""",
                    (conversation.tenant_id, record.run_id, reference.asset_id, reference.revision),
                ).fetchone(),
            )
        if linked is None:
            msg = "slack_image_run_binding_missing"
            raise ValueError(msg)
        asset = repository.get(scope, reference.asset_id, reference.revision)
        if asset is None or asset.sha256 != reference.sha256:
            msg = "slack_image_asset_mismatch"
            raise ValueError(msg)
        path = (repository.artifact_root / asset.relative_path).resolve(strict=True)
        if not path.is_relative_to(repository.artifact_root) or path.stat().st_size > _MAX_BYTES:
            msg = "slack_image_file_invalid"
            raise ValueError(msg)
        with path.open("rb") as stream:
            data = stream.read(_MAX_BYTES + 1)
        if len(data) > _MAX_BYTES or hashlib.sha256(data).hexdigest() != reference.sha256:
            msg = "slack_image_asset_mismatch"
            raise ValueError(msg)
        filename = f"trace-post-{item.country}-{item.role}.png"
        images.append(
            SlackImage(
                f"{reference.sha256}:{filename}",
                data,
                filename,
                f"Trace · {_COUNTRIES[item.country]} · {_ROLES[item.role]}",
            )
        )
    captions = "\n\n".join(
        f"*{_COUNTRIES[item.country]}*\n{item.text}\n답글 링크: {item.reply_link}\n{item.tutorial}"
        for item in result.captions
    )
    return images, captions
