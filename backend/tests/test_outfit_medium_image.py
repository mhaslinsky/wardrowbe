from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from app.models.item import ClothingItem, ItemStatus


@pytest.mark.asyncio
@pytest.mark.parametrize("medium_path", ["test/medium.jpg", None])
async def test_outfit_response_contains_session_medium_image(
    client, db_session, test_user, auth_headers, medium_path
):
    item = ClothingItem(
        id=uuid4(),
        user_id=test_user.id,
        type="t-shirt",
        status=ItemStatus.ready,
        image_path="test/original.jpg",
        thumbnail_path="test/thumb.jpg",
        medium_path=medium_path,
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
    if medium_path is None:
        assert actual["medium_url"] is None
    else:
        url = urlsplit(actual["medium_url"])
        path = url.path.removeprefix("/photos/images/")
        assert path == medium_path
        assert url.query == ""
