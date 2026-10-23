import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from PIL import Image
from pydantic import ValidationError

from app.models.item import ClothingItem, ItemStatus
from app.models.preference import UserPreference
from app.services.inspiration_service import InspirationService, match_slots, parse_analysis


def garment(item_id=None, **values):
    return {
        "type": "shirt",
        "color": "blue",
        "fit": "regular",
        "pattern": "solid",
        "item_id": str(item_id) if item_id else None,
        "match_strength": 0.9,
        **values,
    }


def analysis(slots, items):
    return parse_analysis(
        json.dumps({"slots": slots, "allowed_item_ids": [str(item.id) for item in items]})
    )


def item(kind="shirt", subtype=None):
    return SimpleNamespace(id=uuid4(), type=kind, subtype=subtype)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "{}",
        '{"slots":[],"allowed_item_ids":[]}',
        "null",
        '{"slots":[{}],"allowed_item_ids":[]}',
    ],
)
def test_reject_missing_empty_malformed_analysis(text):
    with pytest.raises(ValidationError):
        parse_analysis(text)


def test_parse_fenced_garments_and_strength():
    shirt = item()
    parsed = analysis([garment(shirt.id)], [shirt])
    assert parse_analysis("```json\n" + parsed.model_dump_json() + "\n```") == parsed
    slots, chosen = match_slots(parsed, [shirt])
    assert chosen == [shirt.id]
    assert slots[0].match_strength == 0.9 and not slots[0].no_match


@pytest.mark.parametrize(
    "change",
    [{"match_strength": 0.49}, {"no_match": True}, {"item_id": str(uuid4())}, {"type": "jeans"}],
)
def test_bad_matches_become_explicit_gaps(change):
    shirt = item()
    slots, chosen = match_slots(analysis([garment(shirt.id, **change)], [shirt]), [shirt])
    assert chosen == []
    assert slots[0].no_match and slots[0].item_id is None and slots[0].match_strength == 0


def test_no_wardrobe_and_rule_exclusions_are_gaps():
    shirt = item()
    for candidates in [[], [shirt]]:
        slots, chosen = match_slots(analysis([garment(shirt.id)], []), candidates)
        assert not chosen and slots[0].no_match


def test_layering_adds_base_and_prevents_reuse():
    sweater, base = item("sweater"), item("t-shirt")
    slots, chosen = match_slots(
        analysis(
            [garment(sweater.id, type="sweater"), garment(sweater.id, type="sweater")],
            [sweater, base],
        ),
        [sweater, base],
    )
    assert set(chosen) == {sweater.id, base.id}
    assert slots[1].no_match


async def test_create_list_image_delete_and_ownership(
    client, test_user, auth_headers, db_session, monkeypatch, tmp_path
):
    import app.services.inspiration_service as service

    monkeypatch.setattr(
        service, "get_settings", lambda: SimpleNamespace(storage_path=str(tmp_path))
    )
    monkeypatch.setattr(service, "require_internal_ai", lambda capability: None)
    shirt = ClothingItem(
        user_id=test_user.id,
        type="shirt",
        primary_color="blue",
        image_path="test/shirt.jpg",
        status=ItemStatus.ready,
        tags={},
    )
    db_session.add(shirt)
    db_session.add(UserPreference(user_id=test_user.id, standing_rules=["No hats"]))
    test_user.location_lat, test_user.location_lon = 40, -74
    await db_session.commit()

    async def vision(messages, task_name, **kwargs):
        assert "No hats" in messages[0]["content"][0]["text"]
        assert kwargs["use_vision_model"] is True
        return analysis([garment(shirt.id)], [shirt]).model_dump_json(), None, None

    monkeypatch.setattr(
        service.RecommendationService, "get_candidate_items", AsyncMock(return_value=[shirt])
    )
    monkeypatch.setattr(
        service.RecommendationService, "_get_recent_outfit_notes", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(service, "score_items", lambda *args: [SimpleNamespace(item=shirt)])
    monkeypatch.setattr(
        service,
        "AIService",
        lambda **kwargs: SimpleNamespace(
            _preprocess_image=lambda path: "photo", _call_with_fallback=vision
        ),
    )
    monkeypatch.setattr(
        service.RecommendationService,
        "__init__",
        lambda self, db: setattr(
            self,
            "weather_service",
            SimpleNamespace(get_current_weather=AsyncMock(return_value=None)),
        ),
    )

    buffer = BytesIO()
    Image.new("RGB", (30, 40), "blue").save(buffer, format="PNG")
    response = await client.post(
        "/api/v1/inspiration",
        files={"image": ("look.png", buffer.getvalue(), "image/png")},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    look = response.json()
    assert look["slots"][0]["item_id"] == str(shirt.id)
    assert look["items"][0]["image_url"].startswith("/api/v1/images/")
    assert (await client.get(look["image_url"])).status_code == 200
    assert (await client.get(look["image_url"].split("?")[0])).status_code == 401
    assert (await client.get("/api/v1/inspiration", headers=auth_headers)).json()[0]["id"] == look[
        "id"
    ]
    assert (await client.get("/api/v1/inspiration")).status_code == 401
    assert await InspirationService(db_session).find(uuid4(), look["id"]) is None
    assert (
        await client.delete(f"/api/v1/inspiration/{look['id']}", headers=auth_headers)
    ).status_code == 204
    assert not list(tmp_path.rglob("*.jpg"))
    assert (await client.get(look["image_url"])).status_code == 404
    assert (await client.get("/api/v1/inspiration", headers=auth_headers)).json() == []
