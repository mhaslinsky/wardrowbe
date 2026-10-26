from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.models import ClothingItem, Outfit, OutfitItem
from app.models.item import ItemStatus
from app.services.recommendation_service import RecommendationService
from app.utils.timezone import get_user_today
from tests.test_day_options import day_generation as day_generation
from tests.test_day_options import forecast, prepare
from tests.test_selfie import _jpeg


async def test_saved_option_retyped_underwear_is_replaced(db_session, test_user, day_generation):
    await prepare(db_session, test_user)
    day = get_user_today(test_user)
    service = RecommendationService(db_session)
    forecast(service, day)
    existing = await service.top_up_day(test_user, day, "casual")
    shirt = next(link.item for link in existing[0].items if link.item.type == "shirt")
    shirt.type = "underwear"
    await db_session.commit()
    assert await service._day_suggestions(test_user.id, day) == []
    shirt.type = "shirt"
    await db_session.commit()
    stale = existing[0]
    underwear = ClothingItem(
        user_id=test_user.id, type="underwear", image_path="test.jpg", status=ItemStatus.ready
    )
    db_session.add(underwear)
    await db_session.flush()
    db_session.add(OutfitItem(outfit_id=stale.id, item_id=underwear.id, position=5))
    await db_session.commit()
    result = await service.top_up_day(test_user, day, "casual")
    assert len(result) == 3 and stale.id not in {outfit.id for outfit in result}
    assert day_generation[-1]["count"] == 1


async def test_selfie_candidates_never_include_underwear(
    client, test_user, auth_headers, db_session
):
    db_session.add_all(
        [
            ClothingItem(
                user_id=test_user.id, type=kind, image_path="test.jpg", status=ItemStatus.ready
            )
            for kind in ["shirt", "underwear"]
        ]
    )
    await db_session.commit()

    async def recognise(ai, photo, items):
        assert [item.type for item in items] == ["shirt"]
        return items

    with patch("app.api.selfie.match_selfie", recognise):
        response = await client.post(
            "/api/v1/selfie",
            headers=auth_headers,
            files={"image": ("selfie.jpg", _jpeg(), "image/jpeg")},
        )
    assert response.status_code == 200 and response.json()["considered"] == 1


@pytest.mark.parametrize("pairing", [False, True])
async def test_external_outfit_rejects_underwear(
    client, test_user, auth_headers, db_session, pairing
):
    items = [
        ClothingItem(
            user_id=test_user.id, type=kind, image_path="test.jpg", status=ItemStatus.ready
        )
        for kind in ["shirt", "underwear"]
    ]
    db_session.add_all(items)
    await db_session.commit()
    before = await db_session.scalar(select(func.count()).select_from(Outfit))
    path = f"/api/v1/pairings/item/{items[0].id}" if pairing else "/api/v1/outfits/suggestions"
    response = await client.post(
        path,
        headers=auth_headers,
        json={
            "items": [str(item.id) for item in items],
            **({} if pairing else {"occasion": "casual"}),
        },
    )
    assert response.status_code == 422, response.text
    assert "Underwear" in response.json()["detail"]
    assert await db_session.scalar(select(func.count()).select_from(Outfit)) == before
