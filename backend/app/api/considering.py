from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import Field, ValidationError
from sqlalchemy import select

from app.api.items import create_uploaded_item
from app.api.outfits import OutfitResponse, _check_studio_kill_switch, outfit_response
from app.api.product_import import router as product_import_router
from app.database import DbSession
from app.models.considering import ItemPurchase
from app.models.item import ClothingItem
from app.models.user import User
from app.schemas.considering import (
    ConsideringCreate,
    ConsideringEdit,
    ConsideringPreviewRequest,
    ConsideringResponse,
    ConsideringSuggestRequest,
    PassRequest,
    PurchaseFields,
)
from app.services.ai_service import AIDisabledError
from app.services.considering_preview_service import ConsideringPreviewService
from app.services.considering_service import ConsideringService, download_image
from app.services.recommendation_service import (
    AIRecommendationError,
    InsufficientWardrobeError,
    RecommendationService,
)
from app.utils.auth import get_current_user
from app.utils.rate_limit import rate_limit_by_user

router = APIRouter(prefix="/considering", tags=["Considering"])
router.include_router(product_import_router)
ShelfUser = Annotated[User, Depends(get_current_user)]


@router.get("", response_model=list[ConsideringResponse])
async def list_considering(
    db: DbSession, current_user: ShelfUser, state: Literal["considering", "passed"] = "considering"
):
    purchases = (
        await db.scalars(
            select(ItemPurchase)
            .join(ClothingItem, ClothingItem.id == ItemPurchase.item_id)
            .where(
                ClothingItem.user_id == current_user.id,
                ItemPurchase.state == state,
            )
            .order_by(ItemPurchase.created_at.desc())
        )
    ).all()
    service = ConsideringService(db)
    return [await service.response(current_user.id, purchase) for purchase in purchases]


@router.post("", response_model=ConsideringResponse, status_code=201)
async def create_considering(data: ConsideringCreate, db: DbSession, current_user: ShelfUser):
    await rate_limit_by_user(str(current_user.id), "considering", max_requests=5, window_seconds=60)
    uploads = []
    try:
        for url in data.image_urls:
            uploads.append(await download_image(str(url)))
        purchase = ItemPurchase(
            **data.model_dump(
                include={"source_url", "price", "currency", "planned_size"}, mode="json"
            )
        )
        await create_uploaded_item(
            db,
            current_user,
            uploads[0],
            type=data.type,
            name=data.name,
            brand=data.brand,
            purchase=purchase,
            additional_uploads=uploads[1:],
        )
        return await ConsideringService(db).response(current_user.id, purchase)
    finally:
        for upload in uploads:
            await upload.close()


class ImageImportFields(PurchaseFields):
    name: str | None = Field(None, max_length=100)
    brand: str | None = Field(None, max_length=100)


@router.post("/upload", response_model=ConsideringResponse, status_code=201)
async def upload_considering(
    db: DbSession,
    current_user: ShelfUser,
    image: UploadFile = File(...),
    metadata: str = Form("{}", max_length=10000),
):
    await rate_limit_by_user(str(current_user.id), "considering", max_requests=5, window_seconds=60)
    try:
        data = ImageImportFields.model_validate_json(metadata)
    except ValidationError as error:
        raise HTTPException(422, "Invalid product details") from error
    purchase = ItemPurchase(
        **data.model_dump(include={"source_url", "price", "currency", "planned_size"}, mode="json")
    )
    await create_uploaded_item(
        db, current_user, image, name=data.name, brand=data.brand, purchase=purchase
    )
    return await ConsideringService(db).response(current_user.id, purchase)


@router.patch("/{item_id}", response_model=ConsideringResponse)
async def edit_considering(
    item_id: UUID, data: ConsideringEdit, db: DbSession, current_user: ShelfUser
):
    service = ConsideringService(db)
    purchase = await service.get(current_user.id, item_id, lock=True)
    if purchase.state == "bought":
        raise HTTPException(409, "Item already bought")
    fields = data.model_dump(exclude_unset=True, mode="json")
    if "name" in fields:
        item = await db.get(ClothingItem, item_id)
        item.name = fields.pop("name")
    for field, value in fields.items():
        setattr(purchase, field, value)
    await db.flush()
    return await service.response(current_user.id, purchase)


@router.post("/{item_id}/bought", response_model=ConsideringResponse)
async def buy_considering(item_id: UUID, db: DbSession, current_user: ShelfUser):
    return await ConsideringService(db).transition(current_user.id, item_id, "bought")


@router.post("/{item_id}/passed", response_model=ConsideringResponse)
async def pass_considering(
    item_id: UUID, data: PassRequest, db: DbSession, current_user: ShelfUser
):
    return await ConsideringService(db).transition(current_user.id, item_id, "passed", data.reason)


@router.post("/{item_id}/restore", response_model=ConsideringResponse)
async def restore_considering(item_id: UUID, db: DbSession, current_user: ShelfUser):
    return await ConsideringService(db).transition(current_user.id, item_id, "considering")


@router.post("/{item_id}/outfits", response_model=OutfitResponse, status_code=201)
async def create_considering_preview(
    item_id: UUID, data: ConsideringPreviewRequest, db: DbSession, current_user: ShelfUser
):
    _check_studio_kill_switch()
    await rate_limit_by_user(
        str(current_user.id), "considering_preview", max_requests=20, window_seconds=60
    )
    outfit = await ConsideringPreviewService(db).create(current_user, item_id, data.companion_ids)
    return await outfit_response(db, outfit, viewer_id=current_user.id)


@router.post("/{item_id}/suggestions", response_model=list[OutfitResponse], status_code=201)
async def suggest_considering_outfits(
    item_id: UUID, data: ConsideringSuggestRequest, db: DbSession, current_user: ShelfUser
):
    await rate_limit_by_user(str(current_user.id), "suggest", max_requests=10, window_seconds=60)
    service = RecommendationService(db)
    service._defer_commit = True
    try:
        outfits = await service.generate_recommendations(
            user=current_user,
            occasion=data.occasion,
            considering_anchor_id=item_id,
            count=3,
        )
    except AIDisabledError:
        raise HTTPException(503, "Internal AI is disabled") from None
    except (InsufficientWardrobeError, ValueError) as error:
        raise HTTPException(400, str(error)) from None
    except AIRecommendationError as error:
        raise HTTPException(503, str(error)) from None
    return [await outfit_response(db, outfit, viewer_id=current_user.id) for outfit in outfits]
