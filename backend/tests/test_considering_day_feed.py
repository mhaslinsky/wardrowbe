import pytest

from app.models.item import ClothingItem, ItemStatus
from app.models.outfit import OutfitSource
from app.services.external_outfit_service import ExternalOutfitService
from app.services.outfit_service import OutfitListFilters, OutfitService
from app.utils.timezone import get_user_today
from tests.test_considering_r1 import purchase

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("state", ["considering", "passed"])
@pytest.mark.parametrize("group", [False, True])
@pytest.mark.parametrize(
    "view", ["day", "date_from", "date_to", "pairing_chip", "pairing_source", "saved_chooser"]
)
async def test_day_and_pairing_lists_exclude_shelf_without_ready_flag(
    client, auth_headers, db_session, test_user, state, group, view
):
    shelf, _ = await purchase(db_session, test_user, state)
    owned = ClothingItem(
        user_id=test_user.id, type="pants", image_path="owned.jpg", status=ItemStatus.ready
    )
    db_session.add(owned)
    await db_session.flush()
    service = ExternalOutfitService(db_session)
    day = get_user_today(test_user)
    saved = await service.create_suggestion(
        test_user, item_ids=[shelf.id, owned.id], occasion="casual", scheduled_for=day
    )
    saved.source_item_id = owned.id
    if view == "pairing_source":
        saved.source = OutfitSource.pairing
    if view == "saved_chooser":
        saved.source = OutfitSource.manual
        saved.scheduled_for = None
    await db_session.commit()
    filters = {"group": group}
    if view in {"day", "date_from"}:
        filters["date_from"] = day.isoformat()
    if view in {"day", "date_to"}:
        filters["date_to"] = day.isoformat()
    if view == "pairing_chip":
        filters["has_source_item"] = True
    if view == "pairing_source":
        filters["source"] = "pairing"
    if view == "saved_chooser":
        filters.update(source="manual", is_lookbook=True, owned_only=True)
    response = await client.get("/api/v1/outfits", params=filters, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["outfits"] == []
    selection = OutfitListFilters(
        user_id=test_user.id,
        group=group,
        date_from=day if view in {"day", "date_from"} else None,
        date_to=day if view in {"day", "date_to"} else None,
        has_source_item=True if view == "pairing_chip" else None,
        source="pairing" if view == "pairing_source" else None,
    )
    if view == "saved_chooser":
        selection.source = "manual"
        selection.is_lookbook = True
        selection.owned_only = True
    assert await OutfitService(db_session).get_ids_by_filter(selection) == []
    general = await client.get("/api/v1/outfits", headers=auth_headers)
    assert general.json()["total"] == 1
    assert general.json()["outfits"][0]["id"] == str(saved.id)
    assert (
        await client.get(f"/api/v1/outfits/{saved.id}", headers=auth_headers)
    ).status_code == 200
    if state == "passed":
        assert (
            await client.post(f"/api/v1/considering/{shelf.id}/restore", headers=auth_headers)
        ).status_code == 200
    assert (
        await client.post(f"/api/v1/considering/{shelf.id}/bought", headers=auth_headers)
    ).status_code == 200
    restored = await client.get("/api/v1/outfits", params=filters, headers=auth_headers)
    assert restored.json()["total"] == 1
    assert restored.json()["outfits"][0]["id"] == str(saved.id)


async def test_owned_only_retains_dirty_owned_templates_and_reset_keeps_shelf(
    client, auth_headers, db_session, test_user
):
    shelf, _ = await purchase(db_session, test_user)
    dirty = ClothingItem(
        user_id=test_user.id,
        type="pants",
        image_path="dirty.jpg",
        status=ItemStatus.ready,
        needs_wash=True,
    )
    db_session.add(dirty)
    await db_session.flush()
    service = ExternalOutfitService(db_session)
    for item in [shelf, dirty]:
        outfit = await service.create_suggestion(test_user, item_ids=[item.id], occasion="casual")
        outfit.source = OutfitSource.manual
        outfit.scheduled_for = None
    await db_session.commit()
    params = {"source": "manual", "is_lookbook": True, "owned_only": True}
    response = await client.get("/api/v1/outfits", params=params, headers=auth_headers)
    assert response.json()["total"] == 1
    assert response.json()["outfits"][0]["items"][0]["id"] == str(dirty.id)
    params["owned_only"] = False
    reset = await client.get("/api/v1/outfits", params=params, headers=auth_headers)
    assert reset.json()["total"] == 2
    del params["owned_only"]
    assert (await client.get("/api/v1/outfits", params=params, headers=auth_headers)).json()[
        "total"
    ] == 2
