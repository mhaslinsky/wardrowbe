import pytest

from app.models.item import ClothingItem, ItemImage, ItemStatus


@pytest.mark.asyncio
async def test_set_primary_swaps_all_paths(client, test_user, auth_headers, db_session):
    paths = ("image_path", "thumbnail_path", "medium_path")
    original = {field: f"original/{field}.jpg" for field in paths}
    extra = {field: f"extra/{field}.jpg" for field in paths}
    item = ClothingItem(user_id=test_user.id, type="shirt", status=ItemStatus.ready, **original)
    db_session.add(item)
    await db_session.flush()
    image = ItemImage(item_id=item.id, position=0, **extra)
    db_session.add(image)
    await db_session.commit()

    response = await client.post(
        f"/api/v1/items/{item.id}/images/{image.id}/set-primary", headers=auth_headers
    )
    assert response.status_code == 200, response.text
    for data in (
        response.json(),
        (await client.get(f"/api/v1/items/{item.id}", headers=auth_headers)).json(),
    ):
        assert {field: data[field] for field in paths} == extra
        assert len(data["additional_images"]) == 1
        gallery = data["additional_images"][0]
        assert gallery["id"] == str(image.id)
        assert {field: gallery[field] for field in paths} == original
