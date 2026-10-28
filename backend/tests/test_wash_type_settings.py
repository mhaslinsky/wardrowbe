from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.main import app
from app.models.item import ClothingItem, ItemStatus
from app.models.preference import UserPreference
from app.models.user import User
from app.services.item_service import ItemService
from app.services.trip_service import reuse_limit
from app.services.undo_wear_service import UndoWearService
from app.services.wear_service import WearService
from app.utils.auth import get_current_user
from app.utils.washing import wash_interval
from tests.test_wear_service import source

PREFERENCES = "/api/v1/users/me/preferences"
ITEMS = "/api/v1/items"


async def garment(db_session, user, interval=None, wears=0):
    item = ClothingItem(
        user_id=user.id,
        type="socks",
        image_path="test.jpg",
        status=ItemStatus.ready,
        wash_interval=interval,
        wears_since_wash=wears,
    )
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    return item


async def setting(client, values):
    response = await client.patch(PREFERENCES, json={"wash_intervals": values})
    assert response.status_code == 200, response.text
    assert response.json()["wash_intervals"] == values


@pytest.mark.parametrize("interval", [2, 999])
async def test_user_setting_list_detail_and_isolation(client, db_session, test_user, interval):
    app.dependency_overrides[get_current_user] = lambda: test_user
    item = await garment(db_session, test_user)
    await setting(client, {"socks": interval})
    db_session.expunge_all()
    detail = await client.get(f"{ITEMS}/{item.id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["effective_wash_interval"] == interval
    listing = await client.get(ITEMS)
    assert listing.status_code == 200, listing.text
    assert listing.json()["items"][0]["effective_wash_interval"] == interval
    other = User(
        id=uuid4(), external_id=str(uuid4()), email=f"{uuid4()}@example.com", display_name="Other"
    )
    db_session.add(other)
    await db_session.commit()
    other_item = await garment(db_session, other)
    app.dependency_overrides[get_current_user] = lambda: other
    db_session.expunge_all()
    detail = await client.get(f"{ITEMS}/{other_item.id}")
    assert detail.json()["effective_wash_interval"] == 1
    app.dependency_overrides[get_current_user] = lambda: test_user
    detail = await client.get(f"{ITEMS}/{item.id}")
    assert detail.json()["effective_wash_interval"] == interval


async def test_override_reset_and_next_wear(client, db_session, test_user):
    app.dependency_overrides[get_current_user] = lambda: test_user
    item = await garment(db_session, test_user, interval=5, wears=1)
    await setting(client, {"socks": 2})
    db_session.expunge_all()
    detail = await client.get(f"{ITEMS}/{item.id}")
    assert detail.json()["effective_wash_interval"] == 5
    assert detail.json()["wears_since_wash"] == 1
    cleared = await client.patch(f"{ITEMS}/{item.id}", json={"wash_interval": None})
    assert cleared.json()["effective_wash_interval"] == 2
    wear = await client.post(f"{ITEMS}/{item.id}/wear", json={"worn_at": "2026-10-28"})
    assert wear.status_code == 200, wear.text
    assert wear.json()["needs_wash"] is True
    await setting(client, {})
    db_session.expunge_all()
    detail = await client.get(f"{ITEMS}/{item.id}")
    assert detail.json()["effective_wash_interval"] == 1
    assert detail.json()["wears_since_wash"] == 2
    assert detail.json()["needs_wash"] is True


async def test_never_does_not_mark_dirty(client, db_session, test_user):
    app.dependency_overrides[get_current_user] = lambda: test_user
    item = await garment(db_session, test_user, wears=1000)
    await setting(client, {"socks": 999})
    db_session.expunge_all()
    worn = await client.post(f"{ITEMS}/{item.id}/wear", json={"worn_at": "2026-10-28"})
    assert worn.status_code == 200, worn.text
    assert worn.json()["wears_since_wash"] == 1001
    assert worn.json()["needs_wash"] is False


@pytest.mark.parametrize(
    "values",
    [
        {"socks": 0},
        {"socks": 31},
        {"socks": True},
        {"socks": "2"},
        {"socks": 2.5},
        {"socks": None},
        {"imaginary": 2},
        [],
        None,
    ],
)
async def test_malformed_setting_rejected(client, test_user, values):
    app.dependency_overrides[get_current_user] = lambda: test_user
    response = await client.patch(PREFERENCES, json={"wash_intervals": values})
    assert response.status_code == 422, response.text


async def test_missing_and_reset_preferences(client, test_user):
    app.dependency_overrides[get_current_user] = lambda: test_user
    response = await client.get(PREFERENCES)
    assert response.json()["wash_intervals"] == {}
    assert response.json()["wash_interval_defaults"]["socks"] == 1
    await setting(client, {"socks": 2, "underwear": 3})
    await setting(client, {"underwear": 3})
    response = await client.post(f"{PREFERENCES}/reset")
    assert response.status_code == 200, response.text
    assert response.json()["wash_intervals"] == {}


async def test_list_has_no_per_item_preference_queries(client, db_session, test_user):
    app.dependency_overrides[get_current_user] = lambda: test_user
    await setting(client, {"socks": 2})
    counts = []
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(db_session.bind.sync_engine, "before_cursor_execute", capture)
    try:
        for count in (1, 5):
            for _ in range(count - (counts[-1][0] if counts else 0)):
                await garment(db_session, test_user)
            db_session.expunge_all()
            statements.clear()
            response = await client.get(ITEMS)
            assert response.status_code == 200, response.text
            assert len(response.json()["items"]) == count
            assert all(entry["effective_wash_interval"] == 2 for entry in response.json()["items"])
            counts.append((count, len(statements)))
        assert counts[0][1] == counts[1][1]
        assert any("wash_intervals" in statement for statement in statements)
        print(f"list query counts: {counts}")
    finally:
        event.remove(db_session.bind.sync_engine, "before_cursor_execute", capture)


def test_trip_uses_type_setting_and_override():
    item = SimpleNamespace(
        type="socks", wash_interval=None, type_wash_interval=2, wears_since_wash=0
    )
    assert reuse_limit(item) == 2
    item.type_wash_interval = 999
    assert reuse_limit(item) == float("inf")
    item.wash_interval = 5
    assert reuse_limit(item) == 5


async def test_type_change_refreshes_setting(client, db_session, test_user):
    app.dependency_overrides[get_current_user] = lambda: test_user
    await setting(client, {"socks": 2, "shirt": 4})
    item = await garment(db_session, test_user)
    response = await client.patch(f"{ITEMS}/{item.id}", json={"type": "shirt"})
    assert response.status_code == 200, response.text
    assert response.json()["effective_wash_interval"] == 4


@pytest.mark.parametrize("interval", [2, 999])
async def test_undo_uses_type_setting(db_session, test_user, interval):
    db_session.add(UserPreference(user_id=test_user.id, wash_intervals={"socks": interval}))
    await db_session.commit()
    item = await garment(db_session, test_user)
    item_id = item.id
    today = date(2026, 10, 28)
    await ItemService(db_session).record_wears(test_user.id, [item_id], today)
    outfit = await source(db_session, test_user, [item_id], day=today)
    await WearService(db_session).log_worn(test_user, outfit, today, caller="test")
    await db_session.commit()
    await UndoWearService(db_session).undo(test_user, outfit.id, None)
    await db_session.commit()
    db_session.expunge_all()
    item = await db_session.scalar(select(ClothingItem).where(ClothingItem.id == item_id))
    assert item.wears_since_wash == 1
    assert item.needs_wash is False


def test_unqueried_item_falls_back_to_builtin():
    item = ClothingItem(type="socks", wash_interval=None)
    assert wash_interval(item) == 1
