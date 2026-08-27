import base64
import binascii
import io
from hashlib import sha256
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Annotated, Final
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from PIL import Image, UnidentifiedImageError

from ads_booster.web.schemas import (
    AssetCreateRequest,
    AssetResponse,
    AssetUpdateRequest,
    AssetUploadRequest,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ads_booster.web.auth import CurrentPrincipal, Principal
from ads_booster.workspace import (
    AssetCreate,
    AssetId,
    AssetRecord,
    ScopedRecordNotFoundError,
    SqliteWorkspaceStore,
    UnsafeAssetPathError,
    WorkspaceId,
)

_MAX_REFERENCE_BYTES: Final = 25 * 1024 * 1024
_IMAGE_FORMATS: Final = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}


def _response(record: AssetRecord) -> AssetResponse:
    return AssetResponse(
        workspace_id=record.workspace_id,
        asset_id=record.asset_id,
        context_id=record.context_id,
        filename=record.filename,
        media_type=record.media_type,
        relative_path=record.relative_path,
        sha256=record.sha256,
        size_bytes=record.size_bytes,
        created_at=record.created_at,
    )


def _value(payload: AssetCreateRequest | AssetUpdateRequest) -> AssetCreate:
    return AssetCreate(
        context_id=payload.context_id,
        filename=payload.filename,
        media_type=payload.media_type,
        relative_path=payload.relative_path,
        sha256=payload.sha256,
        size_bytes=payload.size_bytes,
    )


def _not_found(error: ScopedRecordNotFoundError) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{error.record_type} not found")


def _asset_response(operation: Callable[[], AssetRecord]) -> AssetResponse:
    try:
        return _response(operation())
    except ScopedRecordNotFoundError as error:
        raise _not_found(error) from error
    except UnsafeAssetPathError as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "asset path must stay under workspace assets",
        ) from error


def _upload_asset(
    store: SqliteWorkspaceStore,
    workspace_id: WorkspaceId,
    payload: AssetUploadRequest,
) -> AssetResponse:
    filename = _safe_filename(payload.filename)
    image_format = _IMAGE_FORMATS.get(payload.media_type)
    if image_format is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "reference asset must be a JPEG, PNG, or WebP image",
        )
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "reference image encoding is invalid",
        ) from error
    if not content or len(content) > _MAX_REFERENCE_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "reference image must be 25 MB or smaller",
        )
    _verify_uploaded_image(content, image_format)
    relative_path = f"assets/{uuid4().hex}-{filename}"
    destination = store.database_path.parent / relative_path
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_bytes(content)
        destination.chmod(0o600)
        record = store.create_asset(
            workspace_id,
            AssetCreate(
                context_id=payload.context_id,
                filename=filename,
                media_type=payload.media_type,
                relative_path=relative_path,
                sha256=sha256(content).hexdigest(),
                size_bytes=len(content),
            ),
        )
    except (OSError, ScopedRecordNotFoundError, UnsafeAssetPathError) as error:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "reference image could not be stored",
        ) from error
    return _response(record)


def _verify_uploaded_image(content: bytes, image_format: str) -> None:
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format != image_format:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "reference image media type does not match its bytes",
                )
            _ = image.verify()
    except (OSError, UnidentifiedImageError, SyntaxError) as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "reference image is unreadable",
        ) from error


def build_asset_router(
    store: SqliteWorkspaceStore,
    current_principal: CurrentPrincipal,
) -> APIRouter:
    router = APIRouter(prefix="/api/assets", tags=["assets"])

    @router.get("", response_model=list[AssetResponse])
    def list_assets(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> list[AssetResponse]:
        return [_response(record) for record in store.list_assets(principal.workspace_id)]

    @router.post("", response_model=AssetResponse, status_code=status.HTTP_201_CREATED)
    def create_asset(
        payload: AssetCreateRequest,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> AssetResponse:
        return _asset_response(lambda: store.create_asset(principal.workspace_id, _value(payload)))

    @router.post("/upload", response_model=AssetResponse, status_code=status.HTTP_201_CREATED)
    def upload_asset(
        payload: AssetUploadRequest,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> AssetResponse:
        return _upload_asset(store, principal.workspace_id, payload)

    @router.get("/{asset_id}", response_model=AssetResponse)
    def get_asset(
        asset_id: AssetId,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> AssetResponse:
        return _asset_response(lambda: store.get_asset(principal.workspace_id, asset_id))

    @router.put("/{asset_id}", response_model=AssetResponse)
    def update_asset(
        asset_id: AssetId,
        payload: AssetUpdateRequest,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> AssetResponse:
        return _asset_response(
            lambda: store.update_asset(principal.workspace_id, asset_id, _value(payload))
        )

    @router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_asset(
        asset_id: AssetId,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> Response:
        try:
            store.delete_asset(principal.workspace_id, asset_id)
        except ScopedRecordNotFoundError as error:
            raise _not_found(error) from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    _ = (list_assets, create_asset, upload_asset, get_asset, update_asset, delete_asset)
    return router


def _safe_filename(value: str) -> str:
    path = PurePosixPath(value)
    if value != path.name or "\\" in value or value in {".", ".."}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "asset filename is invalid")
    return value
