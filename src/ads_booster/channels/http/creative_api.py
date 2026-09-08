"""Authenticated real image intake; byte validation is never visual or product proof."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import os
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, cast
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError
from pydantic import Field, TypeAdapter, ValidationError

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.creative_work import AssetParent, CreativeAsset, CreativeScope
from ads_booster.contracts.models import ContractModel, Identifier
from ads_booster.creative.creative_asset_links import asset_links as _links
from ads_booster.creative.creative_assets import SqliteCreativeAssetRepository
from ads_booster.agent.service.work_continuation import continue_work
from ads_booster.transport.json_types import JsonObject, JsonValue

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.agent.service.application import MarketingAgentService
    from ads_booster.channels.http.oauth import OAuthIdentity

_MAX_IMAGE = 512 * 1024
_MAX_READBACK_IMAGE = 10 * 1024 * 1024
_MAX_BODY = 1024 * 1024
_MAX_PIXELS = 16_000_000
_MAX_LIST = 100
_ROUTE = re.compile(
    r"^/v1/runs/([A-Za-z0-9][A-Za-z0-9._:-]{0,159})/assets(?:/([A-Za-z0-9][A-Za-z0-9._-]{0,79}))?$"
)
_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
Text = Annotated[str, Field(min_length=1, max_length=2000)]


class UploadCreativeAsset(ContractModel):
    asset_id: Identifier
    revision: Annotated[int, Field(ge=1)] = 1
    resume: bool = True
    image_base64: Annotated[str, Field(min_length=1, max_length=700000)]
    kind: Literal["native_trace_capture", "edited_promotion", "background_asset", "phone_mockup"]
    source: Text
    use_terms: Text
    data_permission: Literal["synthetic", "explicitly_permitted"]
    permission_evidence: Text
    preserve: Annotated[tuple[Text, ...], Field(max_length=32)] = ()
    change: Annotated[tuple[Text, ...], Field(max_length=32)] = ()
    locale: Annotated[str, Field(min_length=2, max_length=35)] | None = None
    parents: Annotated[tuple[AssetParent, ...], Field(max_length=16)] = ()


def dispatch_creative(  # noqa: PLR0913, PLR0911 - authenticated HTTP routing boundaries.
    method: str,
    path: str,
    body: bytes,
    *,
    identity: OAuthIdentity,
    service: MarketingAgentService,
    artifact_root: Path,
    now: datetime,
) -> tuple[int, JsonObject] | None:
    route = _ROUTE.fullmatch(urlsplit(path).path)
    if route is None:
        return None
    run_id, asset_id = route.groups()
    # Tenant check precedes parsing, image decoding, directory creation and database setup.
    if service.repository.get(identity.tenant_id, run_id) is None:
        return 404, {"error": "agent_run_not_found"}
    if len(body) > _MAX_BODY:
        return 413, {"error": "creative_upload_too_large"}
    try:
        scope = CreativeScope(workspace_id=identity.tenant_id, product_id="trace")
        if method == "POST" and asset_id is None:
            upload = UploadCreativeAsset.model_validate_json(body)
            return _upload(upload, scope, identity, service, artifact_root, run_id, now)
        if method == "GET" and asset_id:
            return _get(scope, service, artifact_root, run_id, asset_id)
        if method == "GET":
            return _list(scope, service, artifact_root, run_id)
    except ValidationError:
        return 400, {"error": "creative_upload_invalid"}
    except (ValueError, OSError) as error:
        code = str(error)
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,100}", code):
            code = "creative_upload_failed"
        return 409 if "conflict" in code or "safe_boundary" in code else 400, {"error": code}

    return 405, {"error": "creative_method_not_allowed"}


def _list(
    scope: CreativeScope, service: MarketingAgentService, root: Path, run_id: str
) -> tuple[int, JsonObject]:
    if scope.workspace_id.startswith("slack-private-"):
        return 403, {"error": "creative_private_chat_not_projected"}
    repository = SqliteCreativeAssetRepository(service.repository.database_path, root)
    with _links(service.repository.database_path) as connection:
        rows = cast(
            "list[tuple[str, int]]",
            connection.execute(
                """SELECT asset_id,MAX(revision) FROM creative_run_assets
                WHERE tenant_id=? AND run_id=? GROUP BY asset_id
                ORDER BY MAX(rowid) DESC LIMIT ?""",
                (scope.workspace_id, run_id, _MAX_LIST),
            ).fetchall(),
        )
    assets: list[JsonValue] = []
    for asset_id, revision in rows:
        asset = repository.describe(scope, asset_id, revision)
        if asset is not None:
            assets.append(
                {
                    **asset.model_dump(mode="json"),
                    "stale": repository.is_stale(scope, asset_id, revision),
                }
            )
    return 200, {
        "run_id": run_id,
        "assets": assets,
        "limit": _MAX_LIST,
        "limit_reached": len(rows) == _MAX_LIST,
        "verification": "stored_metadata_only_use_individual_readback_for_verified_bytes",
    }


def _image_bytes(value: str) -> tuple[bytes, str]:
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("creative_image_base64_invalid") from error
    if len(data) > _MAX_IMAGE:
        raise ValueError("creative_image_too_large")
    try:
        with Image.open(io.BytesIO(data)) as opened:
            if opened.format not in {"PNG", "JPEG"}:
                raise ValueError("creative_image_format_unsupported")
            if opened.width * opened.height > _MAX_PIXELS:
                raise ValueError("creative_image_dimensions_exceeded")
            extension = "png" if opened.format == "PNG" else "jpg"
            opened.verify()
        with Image.open(io.BytesIO(data)) as decoded:
            _ = decoded.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("creative_image_invalid") from error
    return data, extension


def _store_bytes(root: Path, tenant: str, data: bytes, extension: str) -> str:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = root.resolve(strict=True)
    tenant_key = hashlib.sha256(tenant.encode()).hexdigest()
    directory = root / tenant_key
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or not directory.resolve(strict=True).is_relative_to(root):
        raise ValueError("creative_artifact_outside_root")
    digest = hashlib.sha256(data).hexdigest()
    target = directory / f"{digest}.{extension}"
    # Only fully written immutable bytes become visible at the content address.
    with tempfile.NamedTemporaryFile(dir=directory, prefix=".upload-", delete=False) as temporary:
        temporary_path = temporary.name
        _ = temporary.write(data)
        temporary.flush()
        os.fsync(temporary.fileno())
    try:
        try:
            os.link(temporary_path, target, follow_symlinks=False)
        except FileExistsError:
            if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError("creative_artifact_digest_mismatch") from None
    finally:
        Path(temporary_path).unlink()
    return str(target.relative_to(root))


def _upload(  # noqa: PLR0913,PLR0917 - explicit identity, Run and artifact ownership.
    upload: UploadCreativeAsset,
    scope: CreativeScope,
    identity: OAuthIdentity,
    service: MarketingAgentService,
    root: Path,
    run_id: str,
    now: datetime,
) -> tuple[int, JsonObject]:
    data, extension = _image_bytes(upload.image_base64)
    request_digest = contract_sha256(upload.model_dump(mode="json"))
    with service.execution_lock:
        with _links(service.repository.database_path) as connection:
            prior = cast(
                "tuple[str, str] | None",
                connection.execute(
                    """SELECT request_sha256,actor_id FROM creative_run_assets
                WHERE tenant_id=? AND run_id=? AND asset_id=? AND revision=?""",
                    (identity.tenant_id, run_id, upload.asset_id, upload.revision),
                ).fetchone(),
            )
            if prior and (prior[0] != request_digest or prior[1] != identity.principal_id):
                raise ValueError("creative_upload_idempotency_conflict")
        relative = _store_bytes(root, identity.tenant_id, data, extension)
        asset = CreativeAsset.model_validate(
            {
                **upload.model_dump(exclude={"image_base64", "resume"}),
                "scope": scope.model_dump(),
                "relative_path": relative,
                "sha256": hashlib.sha256(data).hexdigest(),
                "origin": "human_reported",
            }
        )
        repository = SqliteCreativeAssetRepository(service.repository.database_path, root)
        repository.add(asset, actor_scope=scope)
        with _links(service.repository.database_path) as connection:
            _ = connection.execute(
                "INSERT OR IGNORE INTO creative_run_assets VALUES(?,?,?,?,?,?)",
                (
                    identity.tenant_id,
                    run_id,
                    asset.asset_id,
                    asset.revision,
                    request_digest,
                    identity.principal_id,
                ),
            )
        # Canonical continuation owns planning; ingestion never directly runs model/image tools.
        _ = continue_work(
            service,
            identity.tenant_id,
            run_id,
            event_id=f"creative-upload:{asset.asset_id}:{asset.revision}",
            actor_id=identity.principal_id,
            action="revise" if upload.resume else "pause",
            note="사람이 작업한 이미지를 받았습니다. 검수를 재개할 수 있습니다.",
            inputs={
                "asset": _metadata(asset),
                "byte_validation": "PNG/JPEG decoded; digest verified",
                "visual_verification": "not_performed",
                "product_proof_verified": False,
            },
            now=now,
        )
    return 201, {
        "asset": _metadata(asset),
        "run_id": run_id,
        "resume_required": not upload.resume,
        "visual_verification": "not_performed",
    }


def _metadata(asset: CreativeAsset) -> JsonObject:
    return _JSON.validate_python(asset.model_dump(mode="json", exclude={"relative_path", "scope"}))


def _get(
    scope: CreativeScope, service: MarketingAgentService, root: Path, run_id: str, asset_id: str
) -> tuple[int, JsonObject]:
    with _links(service.repository.database_path) as connection:
        row = cast(
            "tuple[int] | None",
            connection.execute(
                """SELECT revision FROM creative_run_assets
            WHERE tenant_id=? AND run_id=? AND asset_id=?
            ORDER BY revision DESC LIMIT 1""",
                (scope.workspace_id, run_id, asset_id),
            ).fetchone(),
        )
    if row is None:
        return 404, {"error": "creative_asset_not_found"}
    repository = SqliteCreativeAssetRepository(service.repository.database_path, root)
    asset = repository.get(scope, asset_id, row[0])
    if asset is None:
        return 404, {"error": "creative_asset_not_found"}
    with (root / asset.relative_path).open("rb") as stream:
        data = stream.read(_MAX_READBACK_IMAGE + 1)
    if len(data) > _MAX_READBACK_IMAGE:
        raise ValueError("creative_readback_image_too_large")
    if hashlib.sha256(data).hexdigest() != asset.sha256:
        raise ValueError("creative_artifact_digest_mismatch")
    return 200, {
        "asset": _metadata(asset),
        "image_base64": base64.b64encode(data).decode(),
        "stale": repository.is_stale(scope, asset_id, row[0]),
        "visual_verification": "not_performed",
        "product_proof_verified": False,
    }
