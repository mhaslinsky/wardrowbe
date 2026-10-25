from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.main import app
from app.models.item import ItemHistory
from app.models.user import User
from app.services.recommendation_service import RecommendationService
from app.services.wear_service import WearService
from app.utils.auth import get_current_user
from app.utils.timezone import get_user_today
from tests.test_ha_actions import LAUNDRY_DONE, TOKEN, _ha_settings, _item
from tests.test_wear_service import source


async def flag(client, item):
    response = await client.post(f"/api/v1/items/{item.id}/in-the-wash")
    assert response.status_code == 200, response.text
    assert response.json()["needs_wash"] is True


async def test_flag_without_wear_and_wash_zero_wears(client, db_session, test_user):
    app.dependency_overrides[get_current_user] = lambda: test_user
    item = await _item(db_session, test_user, "shirt")
    await flag(client, item)
    await flag(client, item)
    await db_session.refresh(item)
    assert item.wear_count == item.wears_since_wash == 0
    assert item.last_worn_at is None
    assert (
        await db_session.scalar(
            select(func.count()).select_from(ItemHistory).where(ItemHistory.item_id == item.id)
        )
        == 0
    )
    washed = await client.post(f"/api/v1/items/{item.id}/wash", json={})
    assert washed.status_code == 200, washed.text
    assert washed.json()["needs_wash"] is False
    await db_session.refresh(item)
    assert item.manually_needs_wash is False
    again = await client.post(f"/api/v1/items/{item.id}/wash", json={})
    assert again.status_code == 400


@pytest.mark.parametrize("laundry,keep_wash", [(False, False), (True, False), (True, True)])
async def test_manual_flag_survives_wear_and_undo(
    client, db_session, test_user, laundry, keep_wash
):
    app.dependency_overrides[get_current_user] = lambda: test_user
    item = await _item(db_session, test_user, "shirt")
    item.wash_interval = 10
    await db_session.commit()
    await flag(client, item)
    today = get_user_today(test_user)
    outfit = await source(db_session, test_user, [item.id], day=today)
    outfit_id, item_id = outfit.id, item.id
    await WearService(db_session).log_worn(test_user, outfit, today, caller="test")
    await db_session.commit()
    await db_session.refresh(item)
    assert item.needs_wash is True
    if laundry:
        with _ha_settings(test_user):
            washed = await client.post(LAUNDRY_DONE, headers={"X-HA-Token": TOKEN})
        assert washed.status_code == 200, washed.text
        assert washed.json()["washed"] == 1
        await db_session.refresh(item)
        assert item.needs_wash is False
        await db_session.commit()
    undone = await client.post(
        f"/api/v1/outfits/{outfit_id}/undo-wear",
        json={"washed_item_ids": [str(item_id)] if keep_wash else []},
    )
    assert undone.status_code == 200, undone.text
    await db_session.refresh(item)
    assert item.needs_wash is (not keep_wash)
    assert item.manually_needs_wash is (not keep_wash)
    assert item.wears_since_wash == item.wear_count == 0


async def test_manual_flag_excluded_from_recommendations(client, db_session, test_user):
    app.dependency_overrides[get_current_user] = lambda: test_user
    item = await _item(db_session, test_user, "shirt")
    service = RecommendationService(db_session)
    assert item in await service.get_candidate_items(test_user, None, "casual", None, [])
    await flag(client, item)
    assert await service.get_candidate_items(test_user, None, "casual", None, []) == []


@pytest.mark.parametrize("item_id", ["missing", "malformed"])
async def test_manual_flag_invalid_item(client, test_user, item_id):
    app.dependency_overrides[get_current_user] = lambda: test_user
    identifier = str(uuid4()) if item_id == "missing" else "bad-id"
    response = await client.post(f"/api/v1/items/{identifier}/in-the-wash")
    assert response.status_code == (404 if item_id == "missing" else 422)


async def test_manual_flag_cannot_change_another_users_item(client, db_session, test_user):
    item = await _item(db_session, test_user, "shirt")
    other_user = User(id=uuid4(), email="other-manual-wash@example.test")
    app.dependency_overrides[get_current_user] = lambda: other_user
    response = await client.post(f"/api/v1/items/{item.id}/in-the-wash")
    assert response.status_code == 404
    await db_session.refresh(item)
    assert item.needs_wash is False
