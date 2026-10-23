from uuid import uuid4

from app.models.item import ClothingItem, ItemStatus
from app.models.outfit import Outfit, OutfitItem, OutfitSource, OutfitStatus


async def test_ready_to_wear_filters_before_pagination_and_resets(
    client, db_session, test_user, auth_headers
):
    clean = ClothingItem(
        id=uuid4(),
        user_id=test_user.id,
        type="t-shirt",
        status=ItemStatus.ready,
        needs_wash=False,
    )
    dirty = ClothingItem(
        id=uuid4(),
        user_id=test_user.id,
        type="shirt",
        status=ItemStatus.ready,
        needs_wash=True,
    )
    outfits = [
        Outfit(
            user_id=test_user.id,
            occasion="casual",
            source=OutfitSource.manual,
            status=OutfitStatus.pending,
        )
        for _ in range(3)
    ]
    db_session.add_all([clean, dirty, *outfits])
    await db_session.flush()
    db_session.add_all(
        [
            OutfitItem(outfit_id=outfits[0].id, item_id=clean.id, position=0),
            OutfitItem(outfit_id=outfits[1].id, item_id=clean.id, position=0),
            OutfitItem(outfit_id=outfits[1].id, item_id=dirty.id, position=1),
        ]
    )
    await db_session.commit()
    response = await client.get("/api/v1/outfits", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["total"] == 3
    by_id = {outfit["id"]: outfit for outfit in response.json()["outfits"]}
    assert [item["needs_wash"] for item in by_id[str(outfits[1].id)]["items"]] == [False, True]
    response = await client.get(
        "/api/v1/outfits", params={"ready_to_wear": True, "page_size": 1}, headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert response.json()["has_more"] is True
    assert response.json()["outfits"][0]["id"] != str(outfits[1].id)
    response = await client.get(
        "/api/v1/outfits", params={"ready_to_wear": False}, headers=auth_headers
    )
    assert response.json()["total"] == 3
    dirty.needs_wash = False
    await db_session.commit()
    response = await client.get(
        "/api/v1/outfits", params={"ready_to_wear": True}, headers=auth_headers
    )
    assert response.json()["total"] == 3
    response = await client.get(
        "/api/v1/outfits", params={"ready_to_wear": "invalid"}, headers=auth_headers
    )
    assert response.status_code == 422
