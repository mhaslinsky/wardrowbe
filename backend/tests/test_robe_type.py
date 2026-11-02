import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.models import OutfitItem
from app.models.considering import ItemPurchase
from app.services.inspiration_service import match_slots
from app.services.item_scorer import score_items
from app.services.pairing_service import PairingService
from app.services.preview_service import PreviewService
from app.services.recommendation_service import RecommendationService
from app.services.wear_service import WearService
from app.utils import garment_vocabulary
from app.utils.garment_vocabulary import (
    DEFAULT_WASH_INTERVALS,
    ITEM_ROLE,
    NEAR_FACE_TYPES,
    TYPES,
    is_outfit_type,
    is_suggestion_type,
    render_tagging_prompt,
)
from app.utils.prompts import load_prompt
from app.utils.washing import never_washed, wash_interval
from tests.test_ha_actions import LAUNDRY_DONE, TOKEN, _ha_settings
from tests.test_image_undo_replace import _make_item
from tests.test_inspiration import analysis, garment
from tests.test_item_scorer import _weather
from tests.test_outfit_preview import layer_transport as layer_transport
from tests.test_outfit_preview import preview_setup as preview_setup
from tests.test_outfit_preview import request_prompt, upload
from tests.test_wear_service import source

ROOT = Path(__file__).resolve().parents[2]
REJECTION = "This item type isn't used in outfit suggestions"


async def robe_item(db_session, test_user, kind="robe"):
    item = await _make_item(db_session, test_user)
    item.type = kind
    await db_session.commit()
    return item


def test_robe_vocabulary_and_tagging():
    assert "robe" in TYPES
    assert ITEM_ROLE["robe"] == "outer_layer"
    assert "robe" in NEAR_FACE_TYPES
    assert "robe" in render_tagging_prompt(load_prompt("clothing_analysis"))
    assert "'robe'" in (ROOT / "frontend/lib/generated/garment-vocabulary.ts").read_text()


@pytest.mark.parametrize("locale", ["de", "en", "fr", "it", "ja", "ko", "zh-CN", "zh-TW"])
def test_every_locale_names_robe(locale):
    constants = json.loads((ROOT / f"frontend/messages/{locale}/constants.json").read_text())
    assert constants["types"]["robe"]


@pytest.mark.parametrize("kind", ["robe", " RoBe "])
async def test_robe_excluded_from_all_candidate_paths(db_session, test_user, kind):
    robe = await robe_item(db_session, test_user, kind)
    shirt = await _make_item(db_session, test_user)
    assert not is_suggestion_type(kind)
    assert is_outfit_type(kind)
    candidates = await RecommendationService(db_session).get_candidate_items(
        test_user, _weather(), "casual", None, []
    )
    assert [item.id for item in candidates] == [shirt.id]
    scored = score_items(
        [robe, shirt], _weather(), "casual", None, date.today(), "fall", None, {}, {}
    )
    assert [entry.item.id for entry in scored] == [shirt.id]
    pairing = PairingService(db_session)
    with pytest.raises(HTTPException) as rejected:
        await pairing.get_source_item(test_user.id, robe.id)
    assert rejected.value.status_code == 422
    assert rejected.value.detail == REJECTION
    assert [item.id for item in await pairing.get_available_items(test_user, shirt.id)] == []
    slots, chosen = match_slots(analysis([garment(robe.id, type="robe")], [robe]), [robe])
    assert chosen == [] and slots[0].no_match


@pytest.mark.parametrize("endpoint", ["suggestion", "pairing", "pairing_source"])
async def test_authored_endpoints_reject_robe(
    client, auth_headers, db_session, test_user, endpoint
):
    robe = await robe_item(db_session, test_user)
    shirt = await _make_item(db_session, test_user)
    route = "/api/v1/outfits/suggestions"
    item_ids = [str(robe.id)]
    if endpoint != "suggestion":
        route = f"/api/v1/pairings/item/{robe.id if endpoint == 'pairing_source' else shirt.id}"
        if endpoint == "pairing_source":
            item_ids = [str(shirt.id)]
    response = await client.post(
        route,
        headers=auth_headers,
        json={"items": item_ids, **({"occasion": "casual"} if endpoint == "suggestion" else {})},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == REJECTION


@pytest.mark.parametrize("considering", [False, True])
async def test_forced_robe_rejected_before_provider(db_session, test_user, considering):
    robe = await robe_item(db_session, test_user)
    if considering:
        db_session.add(ItemPurchase(item_id=robe.id))
        await db_session.commit()
    test_user.preferences = None
    service = RecommendationService(db_session)
    with (
        patch.object(service, "_get_today_rejected_item_ids", AsyncMock(return_value=[])),
        patch.object(service, "_get_recent_outfit_notes", AsyncMock(return_value=[])),
        patch.object(service, "get_candidate_items", AsyncMock(return_value=[])),
        patch("app.services.recommendation_service.AIService") as provider,
    ):
        with pytest.raises(ValueError, match=REJECTION):
            await service.generate_recommendations(
                test_user,
                "casual",
                weather_override=_weather(),
                include_items=[robe.id],
                considering_anchor_id=robe.id if considering else None,
            )
        assert provider.return_value.mock_calls == []


@pytest.mark.parametrize("kind", ["robe", " RoBe "])
async def test_robe_wear_and_laundry_reset(client, db_session, test_user, kind):
    assert DEFAULT_WASH_INTERVALS["robe"] == 3
    robe = await robe_item(db_session, test_user, kind)
    assert not never_washed(robe)
    assert wash_interval(robe) == 3
    robe_id = robe.id
    for offset in range(3):
        await db_session.refresh(test_user)
        day = date.today() - timedelta(days=2 - offset)
        outfit = await source(db_session, test_user, [robe_id], day=day)
        await WearService(db_session).log_worn(test_user, outfit, day, caller="test")
        await db_session.commit()
    await db_session.refresh(robe)
    assert (robe.wear_count, robe.wears_since_wash, robe.needs_wash) == (3, 3, True)
    with _ha_settings(test_user):
        response = await client.post(LAUNDRY_DONE, headers={"X-HA-Token": TOKEN})
    assert response.status_code == 200, response.text
    assert response.json()["washed"] == 1
    await db_session.refresh(robe)
    assert (robe.wears_since_wash, robe.needs_wash, robe.manually_needs_wash) == (0, False, False)
    assert robe.wear_count == 3 and robe.last_washed_at is not None


def test_shared_rejection_and_existing_type_controls():
    helper = garment_vocabulary.suggestion_rejection_message
    assert helper([None, "", "shirt", " RoBe "]) == REJECTION
    assert helper(["glasses"]) == REJECTION
    assert helper(["underwear"]) == "Underwear can't be part of an outfit"
    for kind in (None, "", "unknown", "invalid", "shirt"):
        assert is_suggestion_type(kind)
    assert not is_suggestion_type("underwear")


async def test_manual_robe_preview(
    client, auth_headers, db_session, test_user, preview_setup, layer_transport
):
    robe = await robe_item(db_session, test_user)
    db_session.add(
        OutfitItem(
            outfit_id=preview_setup["outfit"],
            item_id=robe.id,
            position=2,
            layer_type="outer",
        )
    )
    await db_session.commit()
    await upload(client, auth_headers)
    await PreviewService(db_session).generate(preview_setup["outfit"], test_user.id, False)
    assert len(layer_transport.requests) == 2
    base_prompt = request_prompt(layer_transport.requests[0])
    base_garments = json.loads(
        base_prompt.split("<garment_data>\n")[1].split("\n</garment_data>")[0]
    )
    assert {entry["type"] for entry in base_garments} == {"shirt", "pants"}
    assert '"type": "robe"' in request_prompt(layer_transport.requests[1])
