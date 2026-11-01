import json
import shutil
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text

from app.models import ClothingItem, OutfitItem, User
from app.models.considering import ItemPurchase
from app.models.item import ItemStatus, WashHistory
from app.models.preference import UserPreference
from app.models.preview import OutfitPreview
from app.schemas.item import ItemUpdate
from app.services.image_service import ImageService
from app.services.item_scorer import MIN_CANDIDATES_PER_ESSENTIAL_ROLE, TOP_N, score_items
from app.services.item_service import ItemService
from app.services.preview_service import PreviewService
from app.services.recommendation_service import RecommendationService
from app.services.undo_wear_service import UndoWearService
from app.services.wear_service import WearService
from app.utils.garment_vocabulary import ITEM_ROLE, NEAR_FACE_TYPES, render_outfit_prompt
from app.utils.prompts import load_prompt
from app.utils.washing import wash_interval
from app.workers.tagging import tag_item_image
from tests.migration_helpers import migration_downgrade
from tests.test_ha_actions import LAUNDRY_DONE, TOKEN, _ha_settings
from tests.test_image_undo_replace import _jpeg_bytes, _make_item
from tests.test_item_scorer import _item as scoring_item
from tests.test_item_scorer import _weather
from tests.test_outfit_preview import (
    configure_layers,
    request_images,
    request_prompt,
    upload,
)
from tests.test_outfit_preview import layer_transport as layer_transport
from tests.test_outfit_preview import preview_setup as preview_setup
from tests.test_preview_tryon import KOLORS
from tests.test_preview_tryon import fal_http as fal_http
from tests.test_tags_merge import _ai_tags, _StubAI
from tests.test_tags_merge import stub_ai as stub_ai
from tests.test_wear_service import source

BASE_REQUEST = Path(__file__).parent / "fixtures" / "glasses-preview-base.json"


@pytest_asyncio.fixture(autouse=True)
async def cleanup_account(db_session, test_user, monkeypatch):
    from app.services import preview_service

    monkeypatch.setattr(preview_service, "face_reference", lambda paths: (None, None))
    user_id = test_user.id
    yield
    await db_session.rollback()
    await db_session.execute(delete(User).where(User.id == user_id))
    await db_session.commit()
    shutil.rmtree(ImageService().storage_path / str(user_id), ignore_errors=True)


async def glasses_item(db_session, test_user, *, enabled=True):
    item = await _make_item(db_session, test_user)
    item.type, item.colors, item.primary_color = "glasses", ["gray"], "gray"
    if enabled is not None:
        item.always_in_preview = enabled
    await db_session.commit()
    return item


def block(prompt, name):
    return json.loads(prompt.split(f"<{name}>\n")[1].split(f"\n</{name}>")[0])


async def test_base_request_matches_recording(
    client, auth_headers, db_session, test_user, preview_setup, layer_transport
):
    await glasses_item(db_session, test_user, enabled=False)
    for item_id, kind in zip(
        sorted(preview_setup["items"], key=str), ("shirt", "pants"), strict=True
    ):
        item = await db_session.get(ClothingItem, item_id)
        item.type = kind
    await db_session.commit()
    await upload(client, auth_headers)
    await PreviewService(db_session).generate(preview_setup["outfit"], test_user.id, False)
    recorded = json.dumps(layer_transport.requests[0], sort_keys=True).encode()
    assert recorded == BASE_REQUEST.read_bytes()


@pytest.mark.parametrize("considering", [False, True])
async def test_preview_adds_glasses_and_invalidates_cache(
    client, auth_headers, db_session, test_user, preview_setup, layer_transport, considering
):
    item = await glasses_item(db_session, test_user, enabled=False)
    if considering:
        anchor_id = preview_setup["items"][0]
        db_session.add(ItemPurchase(item_id=anchor_id))
        await db_session.commit()
        response = await client.request(
            "POST", f"/api/v1/considering/{anchor_id}/outfits", headers=auth_headers, json={}
        )
        assert response.status_code == 201, response.text
        from uuid import UUID

        preview_setup["outfit"] = UUID(response.json()["id"])
    await upload(client, auth_headers)
    service = PreviewService(db_session)
    await service.generate(preview_setup["outfit"], test_user.id, False)
    off_request = layer_transport.requests[-1]
    old_key = (await db_session.get(OutfitPreview, preview_setup["outfit"])).cache_key
    item.always_in_preview = True
    await db_session.commit()
    assert (await service.saved(preview_setup["outfit"], test_user.id))["stale_reasons"] == [
        "clothes"
    ]
    await service.generate(preview_setup["outfit"], test_user.id, False)
    assert len(layer_transport.requests) == 2
    on_request = layer_transport.requests[-1]
    prompt = request_prompt(on_request)
    assert "glasses" in block(prompt, "present_slots")
    assert block(prompt, "garment_data")[-1] == {
        "type": "glasses",
        "colors": ["gray"],
        "primary_color": "gray",
        "layer": "base",
        "fit": None,
    }
    assert "frame sitting naturally on the nose" in prompt
    assert len(request_images(on_request)) == len(request_images(off_request)) + 1
    assert (await db_session.get(OutfitPreview, preview_setup["outfit"])).cache_key != old_key
    await service.generate(preview_setup["outfit"], test_user.id, False)
    assert len(layer_transport.requests) == 2
    ImageService().get_image_path(item.medium_path).write_bytes(_jpeg_bytes(color="red"))
    assert (await service.saved(preview_setup["outfit"], test_user.id))["stale"]
    layer_transport.outputs.append(_jpeg_bytes())
    await service.generate(preview_setup["outfit"], test_user.id, False)
    assert len(layer_transport.requests) == 3
    assert request_images(layer_transport.requests[-1])[-1] != request_images(on_request)[-1]
    item.always_in_preview = False
    await db_session.commit()
    await service.generate(preview_setup["outfit"], test_user.id, False)
    assert layer_transport.requests[-1] == off_request
    print(
        "SMOKE",
        json.dumps(
            {
                "considering": considering,
                "off": {
                    "present_slots": block(request_prompt(off_request), "present_slots"),
                    "garment_data": block(request_prompt(off_request), "garment_data"),
                },
                "on": {
                    "present_slots": block(prompt, "present_slots"),
                    "garment_data": block(prompt, "garment_data"),
                },
                "paid_calls": 0,
            }
        ),
    )


async def test_preview_excludes_archived_and_foreign_items_and_avoids_duplicates(
    client, auth_headers, db_session, test_user, preview_setup, layer_transport
):
    active = await glasses_item(db_session, test_user)
    archived = await glasses_item(db_session, test_user)
    archived.is_archived = True
    foreign = await glasses_item(db_session, test_user)
    foreign.user_id = uuid4()
    db_session.add(
        User(id=foreign.user_id, email=f"{foreign.user_id}@example.invalid", display_name="Other")
    )
    await db_session.commit()
    await upload(client, auth_headers)
    service = PreviewService(db_session)
    await service.generate(preview_setup["outfit"], test_user.id, False)
    assert len(request_images(layer_transport.requests[0])) == 4
    db_session.add(OutfitItem(outfit_id=preview_setup["outfit"], item_id=active.id, position=2))
    await db_session.commit()
    await service.generate(preview_setup["outfit"], test_user.id, True)
    garments = block(request_prompt(layer_transport.requests[-1]), "garment_data")
    assert [garment["type"] for garment in garments].count("glasses") == 1
    await db_session.execute(delete(User).where(User.id == foreign.user_id))
    await db_session.commit()


async def test_missing_enabled_photo_fails_without_a_provider_call(
    client, auth_headers, db_session, test_user, preview_setup, layer_transport
):
    item = await glasses_item(db_session, test_user)
    ImageService().get_image_path(item.medium_path).unlink()
    await upload(client, auth_headers)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        await PreviewService(db_session).generate(preview_setup["outfit"], test_user.id, False)
    assert caught.value.status_code == 422
    assert layer_transport.requests == []


async def test_switch_api_default_update_reset_and_null(
    client, auth_headers, db_session, test_user
):
    item = await glasses_item(db_session, test_user, enabled=None)
    route = f"/api/v1/items/{item.id}"
    assert (await client.get(route, headers=auth_headers)).json()["always_in_preview"] is False
    for enabled in (True, False):
        response = await client.patch(
            route, headers=auth_headers, json={"always_in_preview": enabled}
        )
        assert response.status_code == 200, response.text
        assert response.json()["always_in_preview"] is enabled
    response = await client.patch(route, headers=auth_headers, json={"always_in_preview": None})
    assert response.status_code == 422


async def test_glasses_are_not_suggestion_candidates_or_prompt_types(db_session, test_user):
    item = await glasses_item(db_session, test_user)
    service = RecommendationService(db_session)
    assert await service.get_candidate_items(test_user, _weather(), "casual", None, []) == []
    assert score_items([item], _weather(), "casual", None, date.today(), "fall", None, {}, {}) == []
    for name in ("recommendation", "item_pairing"):
        assert "glasses" not in render_outfit_prompt(load_prompt(name))
    assert ITEM_ROLE["glasses"] == "accessory"
    assert "glasses" in NEAR_FACE_TYPES


def test_large_mixed_wardrobe_reserves_accessory_minimum_for_belts():
    tops = [scoring_item(type="shirt", wear_count=0) for _index in range(TOP_N + 1)]
    belts = [
        scoring_item(type="belt", colors=["orange"], primary_color="orange", wear_count=0)
        for _index in range(MIN_CANDIDATES_PER_ESSENTIAL_ROLE)
    ]
    glasses = [
        scoring_item(type="glasses", wear_count=0)
        for _index in range(MIN_CANDIDATES_PER_ESSENTIAL_ROLE)
    ]
    wardrobe = tops + belts + glasses
    assert len({item.id for item in wardrobe}) == len(wardrobe) > TOP_N
    preferences = UserPreference(color_avoid=["orange"], prefer_underused_items=False)
    scored = score_items(
        wardrobe, _weather(), "casual", preferences, date.today(), "fall", None, {}, {}
    )
    selected = {entry.item.id for entry in scored}
    assert len(selected) == TOP_N
    assert selected.isdisjoint(item.id for item in glasses)
    assert {item.id for item in belts} <= selected
    assert sum(ITEM_ROLE[entry.item.type] == "accessory" for entry in scored) == len(belts)
    assert all(
        entry.preference_score < 1
        for entry in scored
        if entry.item.id in {item.id for item in belts}
    )


async def test_forced_glasses_are_rejected_before_provider_call(db_session, test_user):
    item = await glasses_item(db_session, test_user)
    test_user.preferences = None
    service = RecommendationService(db_session)
    with (
        patch.object(service, "_get_today_rejected_item_ids", AsyncMock(return_value=[])),
        patch.object(service, "_get_recent_outfit_notes", AsyncMock(return_value=[])),
        patch.object(service, "get_candidate_items", AsyncMock(return_value=[])),
        patch("app.services.recommendation_service.AIService") as provider,
    ):
        with pytest.raises(ValueError, match="Glasses aren't used in outfit suggestions"):
            await service.generate_recommendations(
                test_user, "casual", weather_override=_weather(), include_items=[item.id]
            )
        assert provider.return_value.mock_calls == []


@pytest.mark.parametrize("dirty", [False, True])
@pytest.mark.parametrize("item_type", ["glasses", " GlAsSeS "])
async def test_wear_undo_and_laundry_preserve_glasses_wash_state(
    client, auth_headers, db_session, test_user, dirty, item_type
):
    item = await glasses_item(db_session, test_user)
    item.type = item_type
    item.wash_interval, item.wears_since_wash = 1, 7
    item.needs_wash, item.manually_needs_wash = dirty, dirty
    item.last_washed_at = date(2026, 1, 1)
    await db_session.commit()
    item_id = item.id
    outfit = await source(db_session, test_user, [item_id], day=date.today())
    outfit_id = outfit.id
    await WearService(db_session).log_worn(test_user, outfit, date.today(), caller="test")
    await db_session.commit()
    await db_session.refresh(item)
    expected = (7, dirty, dirty, date(2026, 1, 1))
    assert (
        item.wears_since_wash,
        item.needs_wash,
        item.manually_needs_wash,
        item.last_washed_at,
    ) == expected
    assert item.wear_count == 1
    assert wash_interval(item) == 999
    with _ha_settings(test_user):
        response = await client.request("POST", LAUNDRY_DONE, headers={"X-HA-Token": TOKEN})
    assert response.status_code == 200, response.text
    assert response.json()["washed"] == 0
    await UndoWearService(db_session).undo(test_user, outfit_id, None)
    await db_session.commit()
    await db_session.refresh(item)
    assert (
        item.wears_since_wash,
        item.needs_wash,
        item.manually_needs_wash,
        item.last_washed_at,
    ) == expected
    assert item.wear_count == 0
    assert (
        list(
            (
                await db_session.scalars(select(WashHistory).where(WashHistory.item_id == item_id))
            ).all()
        )
        == []
    )


@pytest.mark.parametrize("tryon_result", ["success", "fallback"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_layer_and_cleanup_requests_keep_glasses(
    client,
    auth_headers,
    db_session,
    test_user,
    preview_setup,
    fal_http,
    tryon_result,
    enabled,
):
    item = await glasses_item(db_session, test_user, enabled=enabled)
    await configure_layers(db_session, preview_setup, [("t-shirt", "base"), ("jacket", "outer")])
    if not enabled:
        db_session.add(OutfitItem(outfit_id=preview_setup["outfit"], item_id=item.id, position=2))
        await db_session.commit()
    await upload(client, auth_headers)
    fal_http.failure = "http_error" if tryon_result == "fallback" else None
    await PreviewService(db_session).generate(preview_setup["outfit"], test_user.id, False)
    assert len(fal_http.gemini) == 2
    assert ("frame sitting naturally" in request_prompt(fal_http.gemini[0])) is enabled
    assert ("glasses, watch" in request_prompt(fal_http.gemini[1])) is enabled
    assert fal_http.sequence == [
        ("gemini", 3),
        ("fal", KOLORS),
        ("gemini", 3 if tryon_result == "success" else 2),
    ]
    assert len(fal_http.fal) == 1
    assert fal_http.fal[0]["human_image_url"].startswith("data:image/jpeg;base64,")
    assert len(fal_http.downloads) == (1 if tryon_result == "success" else 0)


async def test_favorite_patch_preserves_enabled_glasses(
    client, auth_headers, db_session, test_user
):
    item = await glasses_item(db_session, test_user)
    response = await client.patch(
        f"/api/v1/items/{item.id}", headers=auth_headers, json={"favorite": True}
    )
    assert response.status_code == 200, response.text
    assert response.json()["favorite"] is True
    assert response.json()["always_in_preview"] is True
    await db_session.refresh(item)
    assert item.always_in_preview is True


@pytest.mark.parametrize("change_type", [False, True])
async def test_preview_switch_rejects_non_glasses_type(
    client, auth_headers, db_session, test_user, change_type
):
    item = (
        await glasses_item(db_session, test_user)
        if change_type
        else await _make_item(db_session, test_user)
    )
    before = (item.type, item.always_in_preview)
    response = await client.patch(
        f"/api/v1/items/{item.id}",
        headers=auth_headers,
        json={"always_in_preview": True, **({"type": "shirt"} if change_type else {})},
    )
    assert response.status_code == 422, response.text
    await db_session.refresh(item)
    assert (item.type, item.always_in_preview) == before


@pytest.mark.parametrize("writer", ["api", "service"])
async def test_retyping_glasses_clears_preview_switch(
    client, auth_headers, db_session, test_user, writer
):
    item = await glasses_item(db_session, test_user)
    for kind in ("shirt", "glasses"):
        if writer == "api":
            response = await client.patch(
                f"/api/v1/items/{item.id}", headers=auth_headers, json={"type": kind}
            )
            assert response.status_code == 200, response.text
        else:
            await ItemService(db_session).update(item, ItemUpdate(type=kind))
            await db_session.commit()
        await db_session.refresh(item)
        assert item.type == kind
        assert item.always_in_preview is False


async def test_retyping_dirty_accessory_clears_wash_state(
    client, auth_headers, db_session, test_user
):
    item = await _make_item(db_session, test_user)
    item.type, item.needs_wash, item.manually_needs_wash, item.wears_since_wash = (
        "accessories",
        True,
        True,
        7,
    )
    item.last_washed_at = date(2026, 1, 1)
    await db_session.commit()
    response = await client.patch(
        f"/api/v1/items/{item.id}", headers=auth_headers, json={"type": "glasses"}
    )
    assert response.status_code == 200, response.text
    await db_session.refresh(item)
    assert (item.needs_wash, item.manually_needs_wash, item.wears_since_wash) == (False, False, 0)
    assert item.last_washed_at == date(2026, 1, 1)


@pytest.mark.parametrize(
    "initial_type,detected_type,expected_type,expected_wash",
    [
        ("unknown", "glasses", "glasses", (False, False, 0)),
        ("unknown", "shirt", "shirt", (True, True, 7)),
        ("shirt", "glasses", "shirt", (True, True, 7)),
    ],
)
async def test_tagging_clears_wash_state_for_glasses(
    db_session, test_user, stub_ai, initial_type, detected_type, expected_type, expected_wash
):
    item = await _make_item(db_session, test_user)
    item.type, item.status = initial_type, ItemStatus.processing
    item.needs_wash, item.manually_needs_wash, item.wears_since_wash = True, True, 7
    item.last_washed_at = date(2026, 1, 1)
    await db_session.commit()
    stub_ai(_ai_tags(type=detected_type))
    photo = ImageService().get_image_path(item.image_path)
    with (
        patch("app.workers.tagging.get_db_session", return_value=db_session),
        patch.object(db_session, "close", AsyncMock()),
        patch.object(
            _StubAI, "analyze_image", autospec=True, side_effect=_StubAI.analyze_image
        ) as analyze,
    ):
        result = await tag_item_image({}, str(item.id), str(photo))
        analyze.assert_awaited_once()
        assert analyze.call_args.args[1] == photo
    assert result["status"] == "success"
    await db_session.refresh(item)
    assert item.type == expected_type
    assert (item.needs_wash, item.manually_needs_wash, item.wears_since_wash) == expected_wash
    assert item.last_washed_at == date(2026, 1, 1)


@pytest.mark.parametrize("writer", ["type_only", "type_and_switch", "legacy"])
async def test_variant_glasses_type_keeps_switch_and_reaches_preview(
    client, auth_headers, db_session, test_user, preview_setup, layer_transport, writer
):
    item = await glasses_item(db_session, test_user)
    if writer == "legacy":
        item.type = " Glasses "
        await db_session.commit()
    else:
        response = await client.patch(
            f"/api/v1/items/{item.id}",
            headers=auth_headers,
            json={
                "type": " Glasses ",
                **({"always_in_preview": True} if writer == "type_and_switch" else {}),
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["always_in_preview"] is True
        enabled = await client.patch(
            f"/api/v1/items/{item.id}", headers=auth_headers, json={"always_in_preview": True}
        )
        assert enabled.status_code == 200, enabled.text
    await upload(client, auth_headers)
    await PreviewService(db_session).generate(preview_setup["outfit"], test_user.id, False)
    prompt = request_prompt(layer_transport.requests[0])
    assert "glasses" in block(prompt, "present_slots")
    assert block(prompt, "garment_data")[-1]["type"] == "glasses"
    assert len(request_images(layer_transport.requests[0])) == 4


@pytest.mark.parametrize("endpoint", ["suggestion", "pairing"])
async def test_authored_outfits_reject_glasses(
    client, auth_headers, db_session, test_user, endpoint
):
    shirt = await _make_item(db_session, test_user)
    glasses = await glasses_item(db_session, test_user)
    route = (
        "/api/v1/outfits/suggestions"
        if endpoint == "suggestion"
        else f"/api/v1/pairings/item/{shirt.id}"
    )
    response = await client.post(
        route,
        headers=auth_headers,
        json={
            "items": [str(glasses.id)],
            **({"occasion": "casual"} if endpoint == "suggestion" else {}),
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Glasses aren't used in outfit suggestions"


@pytest.mark.parametrize("state", ["considering", "passed", "bought"])
async def test_preview_adds_only_owned_glasses(
    client, auth_headers, db_session, test_user, preview_setup, layer_transport, state
):
    item = await glasses_item(db_session, test_user)
    db_session.add(ItemPurchase(item_id=item.id, state=state))
    await db_session.commit()
    await upload(client, auth_headers)
    await PreviewService(db_session).generate(preview_setup["outfit"], test_user.id, False)
    assert ("glasses" in block(request_prompt(layer_transport.requests[0]), "present_slots")) is (
        state == "bought"
    )


@pytest.mark.parametrize("action", ["wash", "in-the-wash"])
@pytest.mark.parametrize("dirty", [False, True])
async def test_wash_actions_reject_never_washed_items(
    client, auth_headers, db_session, test_user, action, dirty
):
    item = await glasses_item(db_session, test_user)
    item.type, item.needs_wash, item.wears_since_wash = " GlAsSeS ", dirty, 7 if dirty else 0
    await db_session.commit()
    response = await client.post(f"/api/v1/items/{item.id}/{action}", headers=auth_headers, json={})
    assert response.status_code == 422, response.text


async def test_dirty_glasses_do_not_block_ready_to_wear(
    client, auth_headers, db_session, test_user
):
    glasses = await glasses_item(db_session, test_user)
    shirt = await _make_item(db_session, test_user)
    glasses.needs_wash = shirt.needs_wash = True
    wearable = await source(db_session, test_user, [glasses.id])
    await source(db_session, test_user, [shirt.id])
    await db_session.commit()
    response = await client.get(
        "/api/v1/outfits", headers=auth_headers, params={"ready_to_wear": True}
    )
    assert response.status_code == 200, response.text
    assert {outfit["id"] for outfit in response.json()["outfits"]} == {str(wearable.id)}


async def test_wash_reminder_ignores_dirty_glasses(db_session, test_user):
    from app.workers.notifications import _check_wash_reminders_inner

    glasses = await glasses_item(db_session, test_user)
    shirt = await _make_item(db_session, test_user)
    glasses.needs_wash = shirt.needs_wash = True
    glasses.name, shirt.name = "Glasses", "Blue shirt"
    await db_session.commit()
    with (
        patch("app.workers.notifications.get_db_session", return_value=db_session),
        patch.object(db_session, "close", AsyncMock()),
        patch("app.workers.notifications.NotificationDispatcher") as dispatcher,
    ):
        dispatcher.return_value.deliver = AsyncMock(return_value=[])
        await _check_wash_reminders_inner({})
        messages = [
            call.args[1]
            for call in dispatcher.return_value.deliver.call_args_list
            if call.args[0] == str(test_user.id)
        ]
        assert len(messages) == 1
        assert messages[0].body == "1 item needs washing: Blue shirt"


async def test_migration_column_default_and_rollback(db_session, test_user):
    columns = (
        await db_session.execute(
            text(
                "SELECT is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name='clothing_items' AND column_name='always_in_preview'"
            )
        )
    ).all()
    assert columns == [("NO", "false")]
    item = await glasses_item(db_session, test_user)
    item_id = item.id
    async with migration_downgrade(db_session, "ac2870000001"):
        assert await db_session.scalar(
            select(ClothingItem.always_in_preview).where(ClothingItem.id == item_id)
        )
        inserted = await glasses_item(db_session, test_user, enabled=None)
        assert inserted.always_in_preview is False
    await db_session.refresh(item)
    assert item.always_in_preview is True
