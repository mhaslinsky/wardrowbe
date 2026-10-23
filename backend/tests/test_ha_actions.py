from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.config import get_settings
from app.models.item import ClothingItem, ItemHistory, ItemStatus, WashHistory
from app.models.outfit import Outfit, OutfitItem, OutfitSource, OutfitStatus, UserFeedback

TOKEN = "ha-test-token-0123456789abcdef"
WORE_TODAY = "/api/v1/ha/wore-today"
LAUNDRY_DONE = "/api/v1/ha/laundry-done"


@contextmanager
def _ha_settings(user, token=TOKEN):
    configured = get_settings().model_copy(
        update={"wardrowbe_ha_token": token, "ha_user_email": user.email}
    )
    with patch("app.api.ha.settings", configured):
        yield


@contextmanager
def _clock(instant: datetime):
    with patch("app.utils.timezone.datetime") as mock_datetime:
        mock_datetime.now.return_value = instant
        yield


async def _item(db_session, user, item_type, *, needs_wash=False, is_archived=False):
    item = ClothingItem(
        id=uuid4(),
        user_id=user.id,
        type=item_type,
        image_path=f"test/{item_type}.jpg",
        status=ItemStatus.ready,
        primary_color="blue",
        needs_wash=needs_wash,
        wears_since_wash=3 if needs_wash else 0,
        is_archived=is_archived,
    )
    db_session.add(item)
    await db_session.commit()
    return item


async def _outfit(db_session, user, scheduled_for, *, source=OutfitSource.scheduled, items=()):
    outfit = Outfit(
        user_id=user.id,
        occasion="casual",
        scheduled_for=scheduled_for,
        source=source,
        status=OutfitStatus.sent,
    )
    db_session.add(outfit)
    await db_session.flush()
    for position, item in enumerate(items):
        db_session.add(OutfitItem(outfit_id=outfit.id, item_id=item.id, position=position))
    await db_session.commit()
    return outfit


async def _wear_count(db_session, outfit_id):
    return await db_session.scalar(
        select(func.count()).select_from(ItemHistory).where(ItemHistory.outfit_id == outfit_id)
    )


async def _feedback(db_session, outfit_id):
    return await db_session.scalar(
        select(UserFeedback)
        .where(UserFeedback.outfit_id == outfit_id)
        .execution_options(populate_existing=True)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [WORE_TODAY, LAUNDRY_DONE])
@pytest.mark.parametrize(
    ("server_token", "headers"),
    [
        (TOKEN, {}),
        (TOKEN, {"X-HA-Token": ""}),
        (TOKEN, {"X-HA-Token": "wrong-token"}),
        (None, {"X-HA-Token": TOKEN}),
        (None, {}),
    ],
    ids=["no-header", "empty-header", "wrong-token", "server-unset", "both-unset"],
)
async def test_a_call_without_the_right_token_is_refused_and_changes_nothing(
    client: AsyncClient, db_session, test_user, path, server_token, headers
):
    shirt = await _item(db_session, test_user, "shirt", needs_wash=True)
    outfit = await _outfit(db_session, test_user, date.today(), items=[shirt])

    with _ha_settings(test_user, token=server_token):
        response = await client.post(path, json={}, headers=headers)

    assert response.status_code == 401
    assert response.json() == {"ok": False, "error": "unauthorized"}
    assert await _feedback(db_session, outfit.id) is None
    assert await _wear_count(db_session, outfit.id) == 0
    await db_session.refresh(shirt)
    assert shirt.needs_wash is True


@pytest.mark.asyncio
async def test_wore_today_logs_todays_scheduled_outfit_once(
    client: AsyncClient, db_session, test_user
):
    shirt = await _item(db_session, test_user, "shirt")
    jeans = await _item(db_session, test_user, "jeans")
    today = date(2026, 10, 22)
    outfit = await _outfit(db_session, test_user, today, items=[shirt, jeans])

    with _ha_settings(test_user), _clock(datetime(2026, 10, 22, 15, 0, tzinfo=UTC)):
        first = await client.post(WORE_TODAY, json={}, headers={"X-HA-Token": TOKEN})
        second = await client.post(WORE_TODAY, json={}, headers={"X-HA-Token": TOKEN})

    assert first.status_code == 200
    assert first.json() == {
        "ok": True,
        "outfit_id": str(outfit.id),
        "date": "2026-10-22",
        "already_logged": False,
    }
    assert second.status_code == 200
    assert second.json() == {**first.json(), "already_logged": True}

    feedback = await _feedback(db_session, outfit.id)
    assert feedback.worn_at == today
    assert feedback.accepted is True
    assert feedback.actually_worn is True
    await db_session.refresh(outfit)
    assert outfit.status == OutfitStatus.accepted
    assert await _wear_count(db_session, outfit.id) == 2
    await db_session.refresh(shirt)
    assert shirt.wear_count == 1


@pytest.mark.asyncio
async def test_wore_today_without_a_scheduled_outfit_today_is_404(
    client: AsyncClient, db_session, test_user
):
    shirt = await _item(db_session, test_user, "shirt")
    today = date(2026, 10, 22)
    yesterdays = await _outfit(db_session, test_user, today - timedelta(days=1), items=[shirt])
    manual = await _outfit(db_session, test_user, today, source=OutfitSource.manual, items=[shirt])

    with _ha_settings(test_user), _clock(datetime(2026, 10, 22, 15, 0, tzinfo=UTC)):
        response = await client.post(WORE_TODAY, json={}, headers={"X-HA-Token": TOKEN})

    assert response.status_code == 404
    assert response.json() == {"ok": False, "error": "no_outfit_today"}
    for outfit in (yesterdays, manual):
        assert await _feedback(db_session, outfit.id) is None
        assert await _wear_count(db_session, outfit.id) == 0


@pytest.mark.asyncio
async def test_wore_today_reports_an_already_worn_outfit_instead_of_logging_another(
    client: AsyncClient, db_session, test_user
):
    shirt = await _item(db_session, test_user, "shirt")
    today = date(2026, 10, 22)
    worn = await _outfit(db_session, test_user, today, items=[shirt])
    other = await _outfit(db_session, test_user, today, items=[shirt])
    db_session.add(UserFeedback(outfit_id=worn.id, accepted=True, worn_at=today))
    await db_session.commit()

    with _ha_settings(test_user), _clock(datetime(2026, 10, 22, 15, 0, tzinfo=UTC)):
        response = await client.post(WORE_TODAY, json={}, headers={"X-HA-Token": TOKEN})

    assert response.status_code == 200
    assert response.json()["outfit_id"] == str(worn.id)
    assert response.json()["already_logged"] is True
    assert await _feedback(db_session, other.id) is None
    assert await _wear_count(db_session, other.id) == 0


@pytest.mark.asyncio
async def test_laundry_done_washes_dirty_clothes_but_not_footwear_or_archived_items(
    client: AsyncClient, db_session, test_user
):
    shirt = await _item(db_session, test_user, "shirt", needs_wash=True)
    jeans = await _item(db_session, test_user, "jeans", needs_wash=True)
    footwear = [
        await _item(db_session, test_user, footwear_type, needs_wash=True)
        for footwear_type in ("shoes", "sneakers", "boots", "sandals")
    ]
    archived = await _item(db_session, test_user, "shirt", needs_wash=True, is_archived=True)
    clean = await _item(db_session, test_user, "t-shirt")

    with _ha_settings(test_user), _clock(datetime(2026, 10, 22, 15, 0, tzinfo=UTC)):
        first = await client.post(LAUNDRY_DONE, json={}, headers={"X-HA-Token": TOKEN})
        second = await client.post(LAUNDRY_DONE, json={}, headers={"X-HA-Token": TOKEN})

    assert first.status_code == 200
    assert first.json() == {"ok": True, "washed": 2}
    assert second.json() == {"ok": True, "washed": 0}

    washed = await db_session.scalars(
        select(WashHistory.item_id).where(
            WashHistory.item_id.in_(
                [item.id for item in (shirt, jeans, *footwear, archived, clean)]
            )
        )
    )
    assert sorted(washed.all()) == sorted([shirt.id, jeans.id])
    for item in (shirt, jeans):
        await db_session.refresh(item)
        assert (item.needs_wash, item.wears_since_wash) == (False, 0)
        assert item.last_washed_at == date(2026, 10, 22)
    for item in (*footwear, archived):
        await db_session.refresh(item)
        assert item.needs_wash is True


@pytest.mark.asyncio
async def test_today_is_the_users_local_date_not_utc(client: AsyncClient, db_session, test_user):
    test_user.timezone = "America/New_York"
    await db_session.commit()
    shirt = await _item(db_session, test_user, "shirt", needs_wash=True)
    local_today = await _outfit(db_session, test_user, date(2026, 10, 22), items=[shirt])
    utc_today = await _outfit(db_session, test_user, date(2026, 10, 23), items=[shirt])
    # 03:30 UTC on the 23rd is 11:30 PM on the 22nd in New York.
    late_evening = datetime(2026, 10, 23, 3, 30, tzinfo=UTC)

    with _ha_settings(test_user), _clock(late_evening):
        wore = await client.post(WORE_TODAY, json={}, headers={"X-HA-Token": TOKEN})
        laundry = await client.post(LAUNDRY_DONE, json={}, headers={"X-HA-Token": TOKEN})

    assert wore.status_code == 200
    assert wore.json()["outfit_id"] == str(local_today.id)
    assert wore.json()["date"] == "2026-10-22"
    assert await _feedback(db_session, utc_today.id) is None
    assert laundry.json() == {"ok": True, "washed": 1}
    await db_session.refresh(shirt)
    assert shirt.last_washed_at == date(2026, 10, 22)
