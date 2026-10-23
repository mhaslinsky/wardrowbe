from uuid import uuid4

import pytest

from app.models.item import ClothingItem, ItemStatus
from app.utils.signed_urls import sign_optional


@pytest.mark.asyncio
async def test_outfit_response_contains_signed_medium_image(client, db_session, test_user, auth_headers):
    item = ClothingItem(
        id=uuid4(), user_id=test_user.id, type="t-shirt", status=ItemStatus.ready,
        image_path="test/original.jpg", thumbnail_path="test/thumb.jpg",
        medium_path="test/medium.jpg",
    )
    db_session.add(item)
    await db_session.commit()
    response = await client.post(
        "/api/v1/outfits/studio",
        json={"items": [str(item.id)], "occasion": "casual"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    outfit_id = response.json()["id"]
    response = await client.get(f"/api/v1/outfits/{outfit_id}", headers=auth_headers)
    assert response.status_code == 200
    [actual] = response.json()["items"]
    assert actual["medium_path"] == item.medium_path
    assert actual["medium_url"] == sign_optional(item.medium_path)
