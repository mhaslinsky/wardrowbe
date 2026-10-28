import json
from pathlib import Path
from uuid import UUID, uuid4

from PIL import ImageOps
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.item import ClothingItem
from app.models.outfit import Outfit, OutfitItem, OutfitSource, OutfitStatus
from app.models.user import User
from app.services.ai_service import AIService, require_internal_ai
from app.services.ai_telemetry_service import CallContext
from app.services.image_service import ImageService
from app.services.item_scorer import get_season, score_items
from app.services.preference_service import PreferenceService
from app.services.recommendation_service import RecommendationService
from app.services.studio_service import validate_item_ownership
from app.utils.clothing import canonical_item_order, repair_top_layers
from app.utils.garment_vocabulary import ITEM_ROLE, TYPES, is_outfit_type
from app.utils.photo_urls import image_url
from app.utils.prompts import load_prompt
from app.utils.timezone import get_user_today


class InspirationSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(min_length=1, max_length=50)
    color: str = Field(min_length=1, max_length=50)
    fit: str = Field(min_length=1, max_length=50)
    pattern: str = Field(min_length=1, max_length=50)
    item_id: UUID | None
    match_strength: float = Field(ge=0, le=1, allow_inf_nan=False)
    no_match: bool = False


class InspirationAnalysis(BaseModel):
    slots: list[InspirationSlot] = Field(min_length=1, max_length=20)
    allowed_item_ids: list[UUID]


def parse_analysis(text: str) -> InspirationAnalysis:
    clean = text.strip()
    if clean.startswith("```") and clean.endswith("```"):
        clean = clean.partition("\n")[2].rsplit("```", 1)[0]
    return InspirationAnalysis.model_validate_json(clean)


def match_slots(
    analysis: InspirationAnalysis, candidates: list[ClothingItem]
) -> tuple[list[InspirationSlot], list[UUID]]:
    available = {
        item.id: item
        for item in candidates
        if item.id in analysis.allowed_item_ids and is_outfit_type(item.type)
    }
    selected = []
    for slot in analysis.slots:
        slot.type = slot.type.strip().lower()
        item = available.get(slot.item_id)
        if (
            slot.no_match
            or item is None
            or slot.match_strength < 0.5
            or slot.item_id in selected
            or ITEM_ROLE.get(slot.type) is None
            or ITEM_ROLE.get(slot.type) != ITEM_ROLE.get(item.type.strip().lower())
        ):
            slot.item_id, slot.match_strength, slot.no_match = None, 0, True
        else:
            selected.append(item.id)
            slot.no_match = False
    types = {item.id: item.type.strip().lower() for item in available.values()}
    subtypes = {item.id: item.subtype for item in available.values()}
    repaired = repair_top_layers(selected, list(available), types, subtypes)
    for slot in analysis.slots:
        if slot.item_id and slot.item_id not in repaired:
            slot.item_id, slot.match_strength, slot.no_match = None, 0, True
    return analysis.slots, canonical_item_order(repaired, types)


class InspirationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    def image_path(self, user_id: UUID, outfit_id: UUID) -> Path:
        return Path(get_settings().storage_path) / "inspiration" / str(user_id) / f"{outfit_id}.jpg"

    def cleanup_image(self, outfit: Outfit) -> None:
        if (outfit.ai_raw_response or {}).get("inspiration"):
            self.image_path(outfit.user_id, outfit.id).unlink(missing_ok=True)

    async def find(self, user_id: UUID, outfit_id: UUID) -> Outfit | None:
        return await self.db.scalar(
            select(Outfit).where(
                Outfit.id == outfit_id,
                Outfit.user_id == user_id,
                Outfit.ai_raw_response.has_key("inspiration"),
            )
        )

    def response(self, outfit: Outfit) -> dict:
        data = outfit.ai_raw_response["inspiration"]
        photo_url = f"/photos/inspiration/{outfit.id}/image"
        items = [{**item, "image_url": image_url(item["image_path"])} for item in data["items"]]
        return {"id": str(outfit.id), **data, "items": items, "image_url": photo_url}

    async def create(self, user: User, image_bytes: bytes) -> dict:
        require_internal_ai("vision")
        with ImageService()._open_bounded(image_bytes, ".jpg") as original:
            if original.format not in {"JPEG", "MPO", "PNG", "WEBP"}:
                raise ValueError("Unsupported image format")
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((1600, 1600))
        preferences = await PreferenceService(self.db).get_preferences(user.id)
        recommendations = RecommendationService(self.db)
        if user.location_lat is None or user.location_lon is None:
            raise ValueError("Set your location first")
        weather = await recommendations.weather_service.get_current_weather(
            float(user.location_lat), float(user.location_lon)
        )
        candidates = await recommendations.get_candidate_items(
            user, weather, "casual", preferences, []
        )
        today = get_user_today(user)
        scored = score_items(
            candidates,
            weather,
            "casual",
            preferences,
            today,
            get_season(today.month, float(user.location_lat)),
            None,
            {},
            {},
        )
        ranks = {entry.item.id: rank for rank, entry in enumerate(scored)}
        candidates.sort(key=lambda item: ranks.get(item.id, len(ranks)))
        wardrobe = [
            {
                "id": str(item.id),
                "type": item.type,
                "subtype": item.subtype,
                "color": item.primary_color,
                "pattern": item.pattern,
                "tags": item.tags,
            }
            for item in candidates
        ]
        notes = await recommendations._get_recent_outfit_notes(user)
        prompt = (
            load_prompt("inspiration")
            + "\nTypes: "
            + json.dumps([item_type for item_type in TYPES if is_outfit_type(item_type)])
        )
        prompt += (
            "\n<WARDROBE>\n"
            + json.dumps(wardrobe).replace("<", "\\u003c").replace(">", "\\u003e")
            + "\n</WARDROBE>"
        )
        prompt += "\n" + recommendations._format_wearer_preferences(preferences, notes)
        ai = AIService(
            endpoints=preferences.ai_endpoints if preferences else None,
            telemetry=CallContext(user.id, "inspiration"),
        )
        path = self.image_path(user.id, uuid4())
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format="JPEG", quality=90)
        try:
            encoded = ai._preprocess_image(path)
            text, error, _ = await ai._call_with_fallback(
                [
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                            },
                        ],
                    },
                ],
                "inspiration",
                use_vision_model=True,
            )
            if not text:
                raise RuntimeError("Image analysis failed") from error
            slots, item_ids = match_slots(parse_analysis(text), candidates)
            if item_ids:
                await validate_item_ownership(self.db, user.id, item_ids)
            outfit = Outfit(
                user_id=user.id,
                occasion="casual",
                name="Inspiration",
                scheduled_for=None,
                source=OutfitSource.external,
                status=OutfitStatus.pending,
            )
            self.db.add(outfit)
            await self.db.flush()
            for position, item_id in enumerate(item_ids):
                self.db.add(OutfitItem(outfit_id=outfit.id, item_id=item_id, position=position))
            outfit.ai_raw_response = {
                "inspiration": {
                    "slots": [slot.model_dump(mode="json") for slot in slots],
                    "items": [
                        {
                            "id": str(item.id),
                            "name": item.name or item.type,
                            "image_path": item.thumbnail_path or item.image_path,
                        }
                        for item_id in item_ids
                        for item in candidates
                        if item.id == item_id
                    ],
                }
            }
            target = self.image_path(user.id, outfit.id)
            path.rename(target)
            try:
                await self.db.commit()
            except Exception:
                target.unlink(missing_ok=True)
                raise
            return self.response(outfit)
        finally:
            path.unlink(missing_ok=True)
