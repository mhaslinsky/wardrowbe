from datetime import date
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.api import items as items_api
from app.main import app
from app.models.item import ClothingItem, ItemStatus
from app.models.preference import UserPreference
from app.models.user import User
from app.services.item_service import ItemService
from app.services.undo_wear_service import UndoWearService
from app.services.wear_service import WearService
from app.utils.auth import get_current_user
from app.utils.garment_vocabulary import DEFAULT_WASH_INTERVALS
from app.utils.washing import wash_interval
from tests.test_items import _make_test_image_bytes
from tests.test_wear_service import source

PREFERENCES = "/api/v1/users/me/preferences"
ITEMS = "/api/v1/items"


async def garment(db_session, user, interval=None, wears=0, kind="socks"):
    item = ClothingItem(
        user_id=user.id,
        type=kind,
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


@pytest.mark.parametrize("bulk", [False, True])
async def test_auto_tag_create_response_uses_type_setting(client, test_user, monkeypatch, bulk):
    app.dependency_overrides[get_current_user] = lambda: test_user
    await setting(client, {"socks": 2})
    original_create = ItemService.create

    async def create_socks(service, *, item_data, **kwargs):
        return await original_create(
            service, item_data=item_data.model_copy(update={"type": "socks"}), **kwargs
        )

    if bulk:
        monkeypatch.setattr(ItemService, "create", create_socks)
    queue = AsyncMock()
    queue.enqueue_job.return_value.job_id = "test-washing-job"
    with (
        patch.object(
            items_api,
            "settings",
            items_api.settings.model_copy(
                update={"ai_internal_enabled": True, "ai_vision_enabled": True}
            ),
        ),
        patch.object(items_api, "create_pool", AsyncMock(return_value=queue)),
    ):
        response = await client.post(
            f"{ITEMS}/bulk" if bulk else ITEMS,
            data={"type": "socks"},
            files={
                "images" if bulk else "image": ("socks.jpg", _make_test_image_bytes(), "image/jpeg")
            },
        )
    assert response.status_code == 201, response.text
    payload = response.json()["results"][0]["item"] if bulk else response.json()
    assert payload["type"] == "socks"
    assert payload["effective_wash_interval"] == 2
    queue.enqueue_job.assert_awaited_once()


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


async def test_stored_invalid_type_settings_are_ignored(client, db_session, test_user):
    app.dependency_overrides[get_current_user] = lambda: test_user
    items = {}
    for kind in ("shirt", "coat", "underwear", "jacket", "pants"):
        item = await garment(db_session, test_user, kind=kind)
        items[kind] = item.id
    db_session.add(
        UserPreference(
            user_id=test_user.id,
            wash_intervals={
                "socks": 2,
                "retired-type": 3,
                "shirt": True,
                "coat": 31,
                "jacket": "2",
                "pants": 2.5,
                "underwear": 0,
                "dress": 999,
            },
        )
    )
    await db_session.commit()
    response = await client.get(PREFERENCES)
    assert response.status_code == 200, response.text
    assert response.json()["wash_intervals"] == {"socks": 2, "dress": 999}
    db_session.expunge_all()
    listing = await client.get(ITEMS)
    assert listing.status_code == 200, listing.text
    assert {item["type"]: item["effective_wash_interval"] for item in listing.json()["items"]} == {
        kind: DEFAULT_WASH_INTERVALS[kind] for kind in items
    }
    for kind, item_id in items.items():
        detail = await client.get(f"{ITEMS}/{item_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["effective_wash_interval"] == DEFAULT_WASH_INTERVALS[kind]


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
