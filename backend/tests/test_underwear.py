import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from app.api.trips import TripRequest
from app.models.item import ClothingItem, ItemStatus
from app.models.outfit import OutfitSource
from app.services.inspiration_service import match_slots, parse_analysis
from app.services.item_scorer import score_items
from app.services.learning_service import slot_composition
from app.services.pairing_service import PairingService
from app.services.preview_service import snapshot
from app.services.recommendation_service import RecommendationService
from app.services.trip_service import TripService
from app.utils.clothing import _slots_for_type, canonical_item_order, remove_legwear
from app.utils.garment_vocabulary import DEFAULT_WASH_INTERVALS, ITEM_ROLE, ROLES, TYPES
from tests.test_item_scorer import _weather


def garment(kind):
    return SimpleNamespace(
        id=uuid4(),
        type=kind,
        subtype=None,
        needs_wash=False,
        medium_path=None,
        image_path=f"{kind}.jpg",
        notes=None,
        colors=[],
        primary_color=None,
        tags={},
    )


def test_underwear_vocabulary_and_tights_are_distinct():
    assert "underwear" in TYPES and "underwear" in ROLES
    assert ITEM_ROLE["underwear"] == "underwear"
    assert DEFAULT_WASH_INTERVALS["underwear"] == 1
    assert ITEM_ROLE["tights"] == "legwear"
    assert DEFAULT_WASH_INTERVALS["tights"] == 1


def test_tagging_prompt_keeps_athletic_underwear_out_of_tights():
    prompt = (Path(__file__).parents[1] / "app/prompts/clothing_analysis.txt").read_text()
    assert "underwear → boxer-briefs, briefs, boxers, trunks, compression, long-underwear" in prompt
    assert "including athletic and compression underwear, as underwear, never tights" in prompt


def test_underwear_has_no_outfit_slot_or_learning_signal():
    underwear, tights = garment("underwear"), garment("tights")
    assert _slots_for_type("underwear") == frozenset()
    assert slot_composition([None, "", "underwear", "UNDERWEAR", "tights"]) == {"legwear": "tights"}
    assert slot_composition(["underwear"]) == {}
    types = {underwear.id: underwear.type, tights.id: tights.type}
    assert canonical_item_order(list(types), types) == [tights.id, underwear.id]
    assert remove_legwear(list(types), types, set()) == [underwear.id]


@pytest.mark.parametrize("kinds", [[], ["underwear"], ["underwear", "shirt", "socks", "tights"]])
async def test_daily_candidates_exclude_underwear_without_changing_other_types(kinds):
    items = [garment(kind) for kind in kinds]
    db = SimpleNamespace(execute=AsyncMock(return_value=Mock()))
    db.execute.return_value.scalars.return_value.all.return_value = items
    result = await RecommendationService(db).get_candidate_items(
        SimpleNamespace(id=uuid4()), None, "casual", None, []
    )
    assert [item.type for item in result] == [kind for kind in kinds if kind != "underwear"]


async def test_pairing_rejects_underwear_source_and_candidate():
    underwear, shirt = garment("underwear"), garment("shirt")
    db = SimpleNamespace(execute=AsyncMock(return_value=Mock()))
    db.execute.return_value.scalar_one_or_none.return_value = underwear
    db.execute.return_value.scalars.return_value.all.return_value = [underwear, shirt]
    service = PairingService(db)
    assert await service.get_source_item(uuid4(), underwear.id) is None
    assert await service.get_available_items(SimpleNamespace(id=uuid4()), uuid4()) == [shirt]
    db.execute.return_value.scalar_one_or_none.return_value = shirt
    assert await service.get_source_item(uuid4(), shirt.id) is shirt


@pytest.mark.parametrize("count", [1, 25])
def test_scorer_drops_underwear_before_small_wardrobe_and_mandatory_paths(count):
    items = [garment("underwear") for _ in range(count)]
    assert (
        score_items(
            items,
            None,
            "casual",
            None,
            date(2026, 10, 26),
            "fall",
            None,
            {},
            {},
            mandatory_item_ids={item.id for item in items},
        )
        == []
    )


def test_inspiration_never_matches_underwear_even_when_provider_selects_it():
    underwear = garment("underwear")
    analysis = parse_analysis(
        json.dumps(
            {
                "slots": [
                    {
                        "type": "underwear",
                        "color": "black",
                        "fit": "regular",
                        "pattern": "solid",
                        "item_id": str(underwear.id),
                        "match_strength": 1,
                    }
                ],
                "allowed_item_ids": [str(underwear.id)],
            }
        )
    )
    slots, chosen = match_slots(analysis, [underwear])
    assert chosen == []
    assert slots[0].no_match and slots[0].item_id is None


def test_preview_excludes_underwear_before_reading_images_and_hashing():
    underwear = garment("underwear")
    shirt = garment("shirt")
    links = [
        SimpleNamespace(item_id=item.id, item=item, layer_type="base")
        for item in [underwear, shirt]
    ]
    outfit = SimpleNamespace(items=links, style_notes=None)
    with patch("app.services.preview_service.ImageService") as images:
        images.return_value.get_image_path.return_value.is_file.return_value = True
        with patch("app.services.preview_service.jpeg", return_value=b"shirt") as encode:
            key, photos, records = snapshot(outfit)
            encode.assert_called_once_with("shirt.jpg", 800)
            assert photos == [b"shirt"]
            assert [record["type"] for record in records] == ["shirt"]
            outfit.items = [links[1]]
            assert snapshot(outfit)[0] == key
            outfit.items = [links[0]]
            assert snapshot(outfit)[1:] == ([], [])


async def test_trip_uses_candidates_without_underwear(db_session, test_user):
    wardrobe = [
        ClothingItem(
            user_id=test_user.id, type=kind, image_path="test.jpg", status=ItemStatus.ready
        )
        for kind in ["shirt", "jeans", "underwear", "tights"]
    ]
    db_session.add_all(wardrobe)
    await db_session.commit()
    underwear_id = wardrobe[2].id
    weather = _weather()

    async def generate(service, **kwargs):
        candidates = await service.get_candidate_items(
            test_user, weather, "casual", None, kwargs["exclude_items"]
        )
        assert underwear_id not in {item.id for item in candidates}
        return [
            await service._materialize_outfit(
                {"items": [1, 2]},
                test_user,
                weather,
                "casual",
                OutfitSource.on_demand,
                {1: wardrobe[0].id, 2: wardrobe[1].id},
                scheduled_date=kwargs["scheduled_date"],
            )
        ]

    request = TripRequest(
        destination="Paris",
        start_date=date(2027, 5, 1),
        end_date=date(2027, 5, 1),
        occasions=["casual"],
    )
    with (
        patch(
            "app.services.trip_service.TripWeather.for_dates",
            AsyncMock(return_value=[(weather, "forecast")]),
        ),
        patch("app.services.trip_service.get_user_today", return_value=date(2026, 10, 26)),
        patch.object(RecommendationService, "generate_recommendations", generate),
    ):
        trip = await TripService(db_session).plan(test_user, request)
    assert str(underwear_id) not in {entry["item_id"] for entry in trip.packing}


async def test_materialized_outfit_drops_underwear_even_when_mandatory(db_session, test_user):
    items = [
        ClothingItem(
            user_id=test_user.id, type=kind, image_path="test.jpg", status=ItemStatus.ready
        )
        for kind in ["shirt", "jeans", "underwear"]
    ]
    db_session.add_all(items)
    await db_session.commit()
    outfit = await RecommendationService(db_session)._materialize_outfit(
        {"items": [1, 2, 3]},
        test_user,
        _weather(),
        "casual",
        OutfitSource.on_demand,
        {index: item.id for index, item in enumerate(items, 1)},
        mandatory_item_ids={items[2].id},
    )
    assert {entry.item_id for entry in outfit.items} == {items[0].id, items[1].id}


async def test_forced_underwear_is_rejected_before_provider_call(db_session, test_user):
    underwear = ClothingItem(
        user_id=test_user.id, type="underwear", image_path="test.jpg", status=ItemStatus.ready
    )
    db_session.add(underwear)
    await db_session.commit()
    test_user.preferences = None
    service = RecommendationService(db_session)
    with (
        patch.object(service, "_get_today_rejected_item_ids", AsyncMock(return_value=[])),
        patch.object(service, "_get_recent_outfit_notes", AsyncMock(return_value=[])),
        patch.object(service, "get_candidate_items", AsyncMock(return_value=[])),
        patch("app.services.recommendation_service.AIService") as provider,
    ):
        with pytest.raises(ValueError, match="selected items"):
            await service.generate_recommendations(
                test_user,
                "casual",
                weather_override=_weather(),
                include_items=[underwear.id],
            )
        assert provider.return_value.mock_calls == []
