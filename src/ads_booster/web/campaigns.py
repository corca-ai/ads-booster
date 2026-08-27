from hashlib import sha256
from pathlib import Path  # noqa: TC003
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import TypeAdapter, ValidationError

from ads_booster.automation import (
    CampaignCreate,
    CampaignId,
    CampaignNotFoundError,
    CampaignRecord,
    CampaignRevisionError,
    CampaignStore,
)
from ads_booster.contracts.generation import (
    GenerationReferenceImage,
    PersonaProfile,
    PromotionMaterial,
)
from ads_booster.web.auth import CurrentPrincipal, Principal  # noqa: TC001
from ads_booster.web.schemas import CampaignCreateRequest, CampaignStopRequest  # noqa: TC001
from ads_booster.workspace import (
    AssetRecord,
    ContextKind,
    ScopedRecordNotFoundError,
    SqliteWorkspaceStore,
)

type ImageMediaType = Literal["image/jpeg", "image/png", "image/webp"]
_IMAGE_MEDIA_TYPE: TypeAdapter[ImageMediaType] = TypeAdapter(ImageMediaType)


def build_campaign_router(
    root: Path,
    workspace_store: SqliteWorkspaceStore,
    campaign_store: CampaignStore,
    current_principal: CurrentPrincipal,
) -> APIRouter:
    router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])

    @router.get("", response_model=list[CampaignRecord])
    def list_campaigns(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> tuple[CampaignRecord, ...]:
        return campaign_store.list_workspace(principal.workspace_id)

    @router.post("", response_model=CampaignRecord, status_code=status.HTTP_201_CREATED)
    def create_campaign(
        payload: CampaignCreateRequest,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> CampaignRecord:
        try:
            persona_record = workspace_store.get_context(
                principal.workspace_id,
                payload.persona_context_id,
            )
            promotion_record = workspace_store.get_context(
                principal.workspace_id,
                payload.promotion_context_id,
            )
            if persona_record.kind is not ContextKind.PERSONA:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "persona context required",
                )
            if promotion_record.kind is not ContextKind.PROMOTION:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "promotion context required",
                )
            persona = PersonaProfile.model_validate_json(persona_record.body)
            promotion = PromotionMaterial.model_validate_json(promotion_record.body)
            references = tuple(
                _reference(root, workspace_store.get_asset(principal.workspace_id, asset_id))
                for asset_id in payload.reference_asset_ids
            )
        except ScopedRecordNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "campaign input not found") from error
        except ValidationError as error:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "campaign context JSON is invalid",
            ) from error
        return campaign_store.create(
            CampaignCreate(
                workspace_id=principal.workspace_id,
                name=payload.name,
                persona=persona,
                promotion_material=promotion,
                reference_images=references,
                reference_date=payload.reference_date,
                device=payload.device,
                variation_count=payload.variation_count,
            )
        )

    @router.post("/{campaign_id}/stop", response_model=CampaignRecord)
    def stop_campaign(
        campaign_id: CampaignId,
        payload: CampaignStopRequest,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> CampaignRecord:
        try:
            return campaign_store.stop(
                principal.workspace_id,
                campaign_id,
                expected_revision=payload.expected_revision,
            )
        except CampaignNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "campaign not found") from error
        except CampaignRevisionError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, "campaign revision conflict") from error

    _ = (create_campaign, list_campaigns, stop_campaign)
    return router


def _reference(
    root: Path,
    record: AssetRecord,
) -> GenerationReferenceImage:
    media_type = _image_media_type(record.media_type)
    asset_root = (root / "assets").resolve()
    path = (root / record.relative_path).resolve()
    if not path.is_relative_to(asset_root) or not path.is_file():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "reference image is unavailable")
    try:
        content = path.read_bytes()
    except OSError as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "reference image is unavailable",
        ) from error
    if len(content) != record.size_bytes or sha256(content).hexdigest() != record.sha256:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "reference image provenance does not match",
        )
    return GenerationReferenceImage(
        reference_id=record.asset_id,
        relative_path=record.relative_path,
        media_type=media_type,
        sha256=record.sha256,
    )


def _image_media_type(value: str) -> ImageMediaType:
    try:
        return _IMAGE_MEDIA_TYPE.validate_python(value)
    except ValidationError as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "reference asset must be a JPEG, PNG, or WebP image",
        ) from error
