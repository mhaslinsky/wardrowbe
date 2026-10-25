from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.main import app
from app.models.item import ItemHistory, WashHistory
from app.services.wear_service import WearService
from app.utils.auth import get_current_user
from app.utils.timezone import get_user_today
from tests.test_ha_actions import LAUNDRY_DONE, TOKEN, WORE_TODAY, _ha_settings, _item
from tests.test_wear_service import source


async def _snapshot(db_session, items):
    result = {}
    for item in items:
        await db_session.refresh(item)
        wear_rows = (
            await db_session.execute(
                select(
                    ItemHistory.id,
                    ItemHistory.outfit_id,
                    ItemHistory.worn_at,
                    ItemHistory.created_at,
                )
                .where(ItemHistory.item_id == item.id)
                .order_by(ItemHistory.id)
            )
        ).all()
        wash_rows = (
            await db_session.execute(
                select(WashHistory.id, WashHistory.washed_at, WashHistory.created_at)
                .where(WashHistory.item_id == item.id)
                .order_by(WashHistory.id)
            )
        ).all()
        result[item.id] = {
            "wear_count": item.wear_count,
            "last_worn_at": item.last_worn_at,
            "last_washed_at": item.last_washed_at,
            "wears_since_wash": item.wears_since_wash,
            "needs_wash": item.needs_wash,
            "wear_rows": wear_rows,
            "wash_rows": wash_rows,
        }
    return result


@pytest.mark.parametrize("wear_route", ["outfits", "ha"])
async def test_undo_after_ha_laundry(client, db_session, test_user, wear_route):
    app.dependency_overrides[get_current_user] = lambda: test_user
    today = get_user_today(test_user)
    yesterday = today - timedelta(days=1)
    items = [await _item(db_session, test_user, kind) for kind in ("shirt", "jeans")]
    item_ids = [item.id for item in items]
    for item in items:
        item.wash_interval = 1
    previous = await source(db_session, test_user, item_ids, day=yesterday)
    await WearService(db_session).log_worn(test_user, previous, yesterday, caller="test")
    await db_session.commit()
    before = await _snapshot(db_session, items)
    for state in before.values():
        assert state["wear_count"] == state["wears_since_wash"] == 1
        assert state["last_worn_at"] == yesterday
        assert state["last_washed_at"] is None
        assert state["needs_wash"] is True
        assert len(state["wear_rows"]) == 1
        assert state["wash_rows"] == []
    outfit = await source(db_session, test_user, item_ids, day=today, kind="scheduled")
    outfit_id = outfit.id
    await db_session.commit()

    with (
        _ha_settings(test_user),
        patch("app.api.outfits.clear_suggestions", new_callable=AsyncMock),
        patch("app.api.outfits._run_learning_safely", new_callable=AsyncMock),
        patch("app.api.outfits.LearningService.recompute_learning_profile", new_callable=AsyncMock),
        patch("app.api.ha.LearningService.process_feedback", new_callable=AsyncMock),
    ):
        if wear_route == "ha":
            wore = await client.post(WORE_TODAY, headers={"X-HA-Token": TOKEN})
            assert wore.status_code == 200, wore.text
            assert wore.json()["outfit_id"] == str(outfit_id)
        else:
            wore = await client.post(f"/api/v1/outfits/{outfit_id}/wore", json={"date": str(today)})
            assert wore.status_code == 200, wore.text
        worn = await _snapshot(db_session, items)
        for state in worn.values():
            assert state["wear_count"] == state["wears_since_wash"] == 2
            assert state["last_worn_at"] == today
            assert state["last_washed_at"] is None
            assert state["needs_wash"] is True
            assert len(state["wear_rows"]) == 2
        await db_session.commit()
        laundry = await client.post(LAUNDRY_DONE, headers={"X-HA-Token": TOKEN})
        assert laundry.status_code == 200, laundry.text
        assert laundry.json() == {"ok": True, "washed": 2}
        washed = await _snapshot(db_session, items)
        for item_id, state in washed.items():
            assert state["wear_count"] == 2
            assert state["last_worn_at"] == state["last_washed_at"] == today
            assert state["wears_since_wash"] == 0
            assert state["needs_wash"] is False
            assert state["wear_rows"] == worn[item_id]["wear_rows"]
            assert len(state["wash_rows"]) == 1
            assert state["wash_rows"][0].washed_at == today
        await db_session.commit()

        undo_url = f"/api/v1/outfits/{outfit_id}/undo-wear"
        refused = await client.post(undo_url)
        assert refused.status_code == 409, refused.text
        detail = refused.json()["detail"]
        assert detail["error_code"] == "WASH_SINCE_WEAR"
        assert {entry["id"] for entry in detail["items"]} == {str(value) for value in item_ids}
        assert len(detail["items"]) == 2
        assert all(entry["washed_at"] == str(today) for entry in detail["items"])
        assert await _snapshot(db_session, items) == washed
        await db_session.commit()

        confirmed_id, unconfirmed_id = item_ids
        undone = await client.post(undo_url, json={"washed_item_ids": [str(confirmed_id)]})
        assert undone.status_code == 200, undone.text
        assert undone.json()["status"] == "skipped"
        after = await _snapshot(db_session, items)
        assert after[unconfirmed_id] == before[unconfirmed_id]
        assert after[confirmed_id] == {
            **washed[confirmed_id],
            "wear_count": before[confirmed_id]["wear_count"],
            "last_worn_at": before[confirmed_id]["last_worn_at"],
            "wear_rows": before[confirmed_id]["wear_rows"],
        }
