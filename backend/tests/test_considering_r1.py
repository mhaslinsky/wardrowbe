import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.models.considering import ItemPurchase
from app.models.item import ClothingItem, ItemStatus
from app.services.considering_service import download_image
from app.services.external_outfit_service import ExternalOutfitService
from app.services.pairing_service import PairingService
from app.services.recommendation_service import RecommendationService
from app.services.weather_service import DailyForecast
from app.utils.timezone import get_user_today

pytestmark = pytest.mark.asyncio
PATH = "/api/v1/considering"


async def purchase(db, user, state="considering"):
    item = ClothingItem(
        user_id=user.id,
        type="shirt",
        image_path="test.jpg",
        status=ItemStatus.ready,
        name="Original",
    )
    db.add(item)
    await db.flush()
    row = ItemPurchase(
        item_id=item.id, state=state, passed_reason="Original reason" if state == "passed" else None
    )
    db.add(row)
    await db.commit()
    return item, row


async def saved_options(db, user, day, item):
    service = ExternalOutfitService(db)
    for _option in range(3):
        await service.create_suggestion(
            user, item_ids=[item.id], occasion="casual", scheduled_for=day
        )
    await db.commit()


@pytest.mark.parametrize("state", ["considering", "passed"])
async def test_saved_daily_options_exclude_shelf_items(
    client, auth_headers, db_session, test_user, state
):
    item, _ = await purchase(db_session, test_user, state)
    day = get_user_today(test_user) - timedelta(days=1)
    await saved_options(db_session, test_user, day, item)
    response = await client.post(
        "/api/v1/outfits/suggest-options",
        headers=auth_headers,
        json={"date": day.isoformat(), "occasion": "casual"},
    )
    assert response.status_code == 200
    assert response.json() == []
    assert await RecommendationService(db_session)._day_suggestions(test_user.id, day) == []


async def test_shelf_options_do_not_fill_daily_quota(db_session, test_user):
    item, _ = await purchase(db_session, test_user)
    test_user.location_lat, test_user.location_lon = 40, -74
    day = get_user_today(test_user)
    await saved_options(db_session, test_user, day, item)
    service = RecommendationService(db_session)
    service.weather_service.get_daily_forecast = AsyncMock(
        return_value=[DailyForecast(day.isoformat(), 10, 22, 20, "sunny", 0)]
    )
    with patch.object(service, "generate_recommendations", AsyncMock(return_value=[])) as generate:
        assert await service.top_up_day(test_user, day, "casual") == []
    assert generate.await_count == 1
    assert generate.await_args.kwargs["count"] == 3


@pytest.mark.parametrize("state", ["considering", "passed"])
async def test_external_pairing_accepts_shelf_source(
    client, auth_headers, db_session, test_user, state
):
    item, _ = await purchase(db_session, test_user, state)
    partner = ClothingItem(
        user_id=test_user.id, type="pants", image_path="partner.jpg", status=ItemStatus.ready
    )
    db_session.add(partner)
    await db_session.commit()
    response = await client.post(
        f"/api/v1/pairings/item/{item.id}", headers=auth_headers, json={"items": [str(partner.id)]}
    )
    assert response.status_code == 201, response.text
    assert response.json()["source_item"]["id"] == str(item.id)
    assert await PairingService(db_session).get_source_item(test_user.id, item.id) is None


@pytest.mark.parametrize(
    "state,action",
    [
        ("considering", "restore"),
        ("passed", "bought"),
        ("passed", "passed"),
        ("bought", "bought"),
        ("bought", "passed"),
        ("bought", "restore"),
    ],
)
async def test_illegal_transitions_keep_state_and_reason(
    client, auth_headers, db_session, test_user, state, action
):
    item, row = await purchase(db_session, test_user, state)
    reason = row.passed_reason
    response = await client.post(
        f"{PATH}/{item.id}/{action}", headers=auth_headers, json={"reason": "Replacement reason"}
    )
    assert response.status_code == 409
    await db_session.refresh(row)
    assert row.state == state
    assert row.passed_reason == reason


async def test_bought_purchase_cannot_be_edited(client, auth_headers, db_session, test_user):
    item, row = await purchase(db_session, test_user, "bought")
    response = await client.patch(
        f"{PATH}/{item.id}", headers=auth_headers, json={"name": "Changed", "planned_size": "XL"}
    )
    assert response.status_code == 409
    await db_session.refresh(item)
    await db_session.refresh(row)
    assert item.name == "Original"
    assert row.planned_size is None


async def test_overall_download_deadline_stops_a_waiting_body(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    actual_time = loop.time
    elapsed = 0

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await release.wait()
            yield b"body"

    def respond(request):
        return httpx.Response(200, headers={"content-type": "image/png"}, stream=Body())

    real_client = httpx.AsyncClient
    with (
        patch(
            "asyncio.BaseEventLoop.getaddrinfo",
            AsyncMock(return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
        ),
        patch(
            "app.services.considering_service.httpx.AsyncClient",
            side_effect=lambda **kwargs: real_client(
                transport=httpx.MockTransport(respond), **kwargs
            ),
        ),
    ):
        monkeypatch.setattr(loop, "time", lambda: actual_time() + elapsed)
        task = asyncio.create_task(download_image("https://shop.example/image"))
        try:
            await entered.wait()
            elapsed = 21
            loop.call_soon(release.set)
            with pytest.raises(HTTPException) as error:
                await task
            assert error.value.status_code == 422
        finally:
            monkeypatch.setattr(loop, "time", actual_time)
            release.set()
            if not task.done():
                task.cancel()
