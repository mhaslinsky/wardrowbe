from datetime import date
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.item import ClothingItem, ItemStatus
from app.schemas.item import ItemUpdate
from app.services.item_service import ItemService
from app.utils.washing import replay_copy_wears
from tests.test_ha_actions import LAUNDRY_DONE, TOKEN, _ha_settings


async def _make_item(
    db_session: AsyncSession, user_id, quantity: int = 1, wash_interval: int | None = 1
) -> ClothingItem:
    item = ClothingItem(
        user_id=user_id,
        type="socks",
        image_path="test/socks.jpg",
        status=ItemStatus.ready,
        wash_interval=wash_interval,
        quantity=quantity,
        clean_count=quantity,
    )
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    return item


async def _wear(client: AsyncClient, item_id, auth_headers) -> dict:
    response = await client.post(f"/api/v1/items/{item_id}/wear", json={}, headers=auth_headers)
    assert response.status_code == 200, response.json()
    return response.json()


@pytest.mark.asyncio
async def test_single_copy_wear_marks_needs_wash(
    client: AsyncClient, test_user, auth_headers, db_session: AsyncSession
):
    item = await _make_item(db_session, test_user.id)
    data = await _wear(client, item.id, auth_headers)
    assert (data["quantity"], data["clean_count"], data["needs_wash"]) == (1, 0, True)
    assert data["wears_since_wash"] == 1


@pytest.mark.asyncio
async def test_three_copies_stay_available_until_all_worn(
    client: AsyncClient, test_user, auth_headers, db_session: AsyncSession
):
    item = await _make_item(db_session, test_user.id, quantity=3)
    expected = [(2, False), (1, False), (0, True)]
    for clean, needs_wash in expected:
        data = await _wear(client, item.id, auth_headers)
        assert (data["clean_count"], data["needs_wash"]) == (clean, needs_wash)
    # Wearing with no clean copies left keeps the count at zero.
    data = await _wear(client, item.id, auth_headers)
    assert (data["clean_count"], data["needs_wash"]) == (0, True)


@pytest.mark.asyncio
async def test_wash_restores_every_copy(
    client: AsyncClient, test_user, auth_headers, db_session: AsyncSession
):
    item = await _make_item(db_session, test_user.id, quantity=3)
    await _wear(client, item.id, auth_headers)
    response = await client.post(f"/api/v1/items/{item.id}/wash", json={}, headers=auth_headers)
    assert response.status_code == 200, response.json()
    data = response.json()
    assert (data["clean_count"], data["needs_wash"], data["wears_since_wash"]) == (3, False, 0)


@pytest.mark.asyncio
async def test_lowering_quantity_clamps_clean_count(
    client: AsyncClient, test_user, auth_headers, db_session: AsyncSession
):
    item = await _make_item(db_session, test_user.id, quantity=3)
    response = await client.patch(
        f"/api/v1/items/{item.id}", json={"quantity": 2}, headers=auth_headers
    )
    assert response.status_code == 200, response.json()
    assert (response.json()["quantity"], response.json()["clean_count"]) == (2, 2)

    await _wear(client, item.id, auth_headers)
    await _wear(client, item.id, auth_headers)
    response = await client.patch(
        f"/api/v1/items/{item.id}", json={"quantity": 1}, headers=auth_headers
    )
    data = response.json()
    assert (data["quantity"], data["clean_count"], data["needs_wash"]) == (1, 0, True)


@pytest.mark.asyncio
async def test_raising_quantity_adds_clean_copies(
    client: AsyncClient, test_user, auth_headers, db_session: AsyncSession
):
    item = await _make_item(db_session, test_user.id)
    await _wear(client, item.id, auth_headers)
    response = await client.patch(
        f"/api/v1/items/{item.id}", json={"quantity": 3}, headers=auth_headers
    )
    data = response.json()
    assert (data["quantity"], data["clean_count"], data["needs_wash"]) == (3, 2, False)
    assert data["wears_since_wash"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("quantity", [0, -1])
async def test_non_positive_quantity_rejected(
    client: AsyncClient, test_user, auth_headers, db_session: AsyncSession, quantity
):
    item = await _make_item(db_session, test_user.id)
    response = await client.patch(
        f"/api/v1/items/{item.id}", json={"quantity": quantity}, headers=auth_headers
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_next_copy_starts_clean_when_interval_is_above_one(
    client: AsyncClient, test_user, auth_headers, db_session: AsyncSession
):
    item = await _make_item(db_session, test_user.id, quantity=2, wash_interval=3)
    trajectory = []
    for _ in range(6):
        data = await _wear(client, item.id, auth_headers)
        trajectory.append((data["wears_since_wash"], data["clean_count"], data["needs_wash"]))
    assert trajectory == [
        (1, 2, False),
        (2, 2, False),
        (0, 1, False),
        (1, 1, False),
        (2, 1, False),
        (3, 0, True),
    ]


@pytest.mark.asyncio
async def test_wash_overrides_changes_made_after_the_item_was_loaded(
    test_user, db_session: AsyncSession, session_maker
):
    item = await _make_item(db_session, test_user.id, quantity=3)
    item.clean_count = 1
    await db_session.commit()
    async with session_maker() as other:
        await other.execute(
            update(ClothingItem)
            .where(ClothingItem.id == item.id)
            .values(quantity=1, clean_count=0, wears_since_wash=1, needs_wash=True)
        )
        await other.commit()

    await ItemService(db_session).log_wash(item, date(2026, 11, 26))
    await db_session.commit()
    await db_session.refresh(item)
    assert (item.quantity, item.clean_count, item.wears_since_wash, item.needs_wash) == (
        1,
        1,
        0,
        False,
    )


@pytest.mark.asyncio
async def test_quantity_change_uses_counters_committed_after_the_item_was_loaded(
    test_user, db_session: AsyncSession, session_maker
):
    item = await _make_item(db_session, test_user.id, quantity=2)
    async with session_maker() as other:
        await other.execute(
            update(ClothingItem).where(ClothingItem.id == item.id).values(clean_count=1)
        )
        await other.commit()

    await ItemService(db_session).update(item, ItemUpdate(quantity=3))
    await db_session.commit()
    await db_session.refresh(item)
    assert (item.quantity, item.clean_count, item.needs_wash) == (3, 2, False)


@pytest.mark.parametrize(
    ("wears", "expected"),
    [
        (0, (2, 0, False)),
        (2, (2, 2, False)),
        (3, (1, 0, False)),
        (5, (1, 2, False)),
        (6, (0, 3, True)),
    ],
)
def test_undo_replays_wears_copy_by_copy(wears, expected):
    item = SimpleNamespace(
        type="t-shirt",
        wash_interval=3,
        quantity=2,
        manually_needs_wash=False,
        clean_count=None,
        wears_since_wash=None,
        needs_wash=None,
    )
    replay_copy_wears(item, wears)
    assert (item.clean_count, item.wears_since_wash, item.needs_wash) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(("start", "new", "clean"), [(1, 2, 1), (3, 2, 0)])
async def test_quantity_edit_keeps_an_item_put_in_the_wash(
    client: AsyncClient, test_user, auth_headers, db_session: AsyncSession, start, new, clean
):
    item = await _make_item(db_session, test_user.id, quantity=start)
    response = await client.post(f"/api/v1/items/{item.id}/in-the-wash", headers=auth_headers)
    assert (response.json()["needs_wash"], response.json()["clean_count"]) == (True, 0)
    response = await client.patch(
        f"/api/v1/items/{item.id}", json={"quantity": new}, headers=auth_headers
    )
    body = response.json()
    assert (body["needs_wash"], body["clean_count"]) == (True, clean)
    response = await client.post(f"/api/v1/items/{item.id}/wash", json={}, headers=auth_headers)
    assert response.status_code == 200, response.json()


@pytest.mark.asyncio
async def test_laundry_done_washes_an_item_with_some_copies_dirty(
    client: AsyncClient, test_user, auth_headers, db_session: AsyncSession
):
    item = await _make_item(db_session, test_user.id, quantity=3)
    await _wear(client, item.id, auth_headers)
    with _ha_settings(test_user):
        response = await client.post(LAUNDRY_DONE, json={}, headers={"X-HA-Token": TOKEN})
    assert response.json() == {"ok": True, "washed": 1}
    await db_session.refresh(item)
    assert (item.clean_count, item.needs_wash) == (3, False)


@pytest.mark.asyncio
async def test_retyping_as_glasses_uses_a_quantity_committed_after_the_item_was_loaded(
    test_user, db_session: AsyncSession, session_maker
):
    item = await _make_item(db_session, test_user.id, quantity=3)
    item.clean_count = 0
    await db_session.commit()
    await db_session.refresh(item)
    async with session_maker() as other:
        await other.execute(
            update(ClothingItem).where(ClothingItem.id == item.id).values(quantity=2)
        )
        await other.commit()

    await ItemService(db_session).update(item, ItemUpdate(type="glasses"))
    await db_session.commit()
    await db_session.refresh(item)
    assert (item.quantity, item.clean_count) == (2, 2)


@pytest.mark.asyncio
async def test_an_edit_keeps_a_wash_put_in_after_the_item_was_loaded_as_glasses(
    test_user, db_session: AsyncSession, session_maker
):
    item = await _make_item(db_session, test_user.id, quantity=2)
    item.type = "glasses"
    await db_session.commit()
    await db_session.refresh(item)
    async with session_maker() as other:
        await other.execute(
            update(ClothingItem)
            .where(ClothingItem.id == item.id)
            .values(type="socks", manually_needs_wash=True, needs_wash=True, clean_count=0)
        )
        await other.commit()

    await ItemService(db_session).update(item, ItemUpdate(name="Renamed"))
    await db_session.commit()
    await db_session.refresh(item)
    assert (item.type, item.manually_needs_wash, item.needs_wash, item.clean_count) == (
        "socks",
        True,
        True,
        0,
    )


@pytest.mark.asyncio
async def test_a_quantity_edit_reads_the_never_setting_of_the_new_type(
    client: AsyncClient, test_user, auth_headers, db_session: AsyncSession
):
    item = await _make_item(db_session, test_user.id, quantity=2, wash_interval=None)
    response = await client.patch(
        "/api/v1/users/me/preferences",
        json={"wash_intervals": {"socks": 1, "dress": 999}},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.json()
    await _wear(client, item.id, auth_headers)
    assert (await _wear(client, item.id, auth_headers))["needs_wash"] is True
    response = await client.patch(
        f"/api/v1/items/{item.id}", json={"type": "dress", "quantity": 1}, headers=auth_headers
    )
    assert response.status_code == 200, response.json()
    assert response.json()["needs_wash"] is False


@pytest.mark.asyncio
async def test_a_quantity_edit_reads_the_setting_of_a_type_changed_after_the_load(
    client: AsyncClient, test_user, auth_headers, db_session: AsyncSession, session_maker
):
    item = await _make_item(db_session, test_user.id, quantity=2, wash_interval=None)
    response = await client.patch(
        "/api/v1/users/me/preferences",
        json={"wash_intervals": {"socks": 1, "dress": 999}},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.json()
    await _wear(client, item.id, auth_headers)
    assert (await _wear(client, item.id, auth_headers))["needs_wash"] is True
    await db_session.commit()
    await db_session.refresh(item)
    async with session_maker() as other:
        await other.execute(
            update(ClothingItem)
            .where(ClothingItem.id == item.id)
            .values(type="dress", needs_wash=False)
        )
        await other.commit()

    await ItemService(db_session).update(item, ItemUpdate(quantity=1))
    await db_session.commit()
    await db_session.refresh(item)
    assert (item.type, item.clean_count, item.needs_wash) == ("dress", 0, False)


@pytest.mark.asyncio
async def test_retyping_as_glasses_brings_every_copy_back(
    client: AsyncClient, test_user, auth_headers, db_session: AsyncSession
):
    item = await _make_item(db_session, test_user.id, quantity=2)
    await _wear(client, item.id, auth_headers)
    await _wear(client, item.id, auth_headers)
    response = await client.patch(
        f"/api/v1/items/{item.id}", json={"type": "glasses"}, headers=auth_headers
    )
    body = response.json()
    assert (body["clean_count"], body["needs_wash"]) == (2, False)


@pytest.mark.asyncio
async def test_a_never_wash_interval_frees_an_item_with_no_clean_copies(
    client: AsyncClient, test_user, auth_headers, db_session: AsyncSession
):
    item = await _make_item(db_session, test_user.id, quantity=2)
    await _wear(client, item.id, auth_headers)
    assert (await _wear(client, item.id, auth_headers))["needs_wash"] is True
    response = await client.patch(
        f"/api/v1/items/{item.id}", json={"wash_interval": 999}, headers=auth_headers
    )
    assert response.status_code == 200, response.json()
    assert (await _wear(client, item.id, auth_headers))["needs_wash"] is False
    response = await client.patch(
        f"/api/v1/items/{item.id}", json={"quantity": 1}, headers=auth_headers
    )
    assert response.status_code == 200, response.json()
    assert response.json()["needs_wash"] is False


def test_undo_replay_keeps_an_item_put_in_the_wash():
    item = SimpleNamespace(
        type="t-shirt",
        wash_interval=3,
        quantity=2,
        manually_needs_wash=True,
        clean_count=None,
        wears_since_wash=None,
        needs_wash=None,
    )
    replay_copy_wears(item, 0)
    assert (item.clean_count, item.needs_wash) == (0, True)
