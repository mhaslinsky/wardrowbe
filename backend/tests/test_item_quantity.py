import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.item import ClothingItem, ItemStatus


async def _make_item(db_session: AsyncSession, user_id, quantity: int = 1) -> ClothingItem:
    item = ClothingItem(
        user_id=user_id,
        type="socks",
        image_path="test/socks.jpg",
        status=ItemStatus.ready,
        wash_interval=1,
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
