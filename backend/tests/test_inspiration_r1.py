import json
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from PIL import Image

from app.api.auth import create_access_token
from app.config import get_settings
from app.models.item import ClothingItem, ItemStatus
from app.models.outfit import Outfit
from app.models.preference import UserPreference
from app.models.user import User
from app.services.ai_service import AIService
from app.services.inspiration_service import InspirationService
from app.services.weather_service import WeatherData, WeatherService, WeatherServiceError
from tests.identity_helpers import add_users_with_identities
from tests.photo_helpers import backend_url


@pytest.fixture
async def inspiration(client, test_user, auth_headers, db_session, monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    monkeypatch.setattr(settings, "ai_internal_enabled", True)
    monkeypatch.setattr(settings, "ai_vision_enabled", True)
    test_user.location_lat, test_user.location_lon = 40, -74
    db_session.add(UserPreference(user_id=test_user.id, standing_rules=["No hats"]))
    await db_session.commit()
    weather = AsyncMock(
        return_value=WeatherData(20, 20, 50, 0, 0, 0, "clear", 0, True, 0, datetime.now(UTC))
    )
    original_weather = WeatherService.get_current_weather
    monkeypatch.setattr(WeatherService, "get_current_weather", weather)
    state = SimpleNamespace(
        original_weather=original_weather,
        text=None,
        prompts=[],
        weather=weather,
        path=tmp_path,
        user=test_user,
    )
    state.text = json.dumps({"slots": [slot()], "allowed_item_ids": []})

    async def vision(self, messages, task_name, **kwargs):
        state.prompts.append(messages)
        return state.text, None, None

    async def upload(data=None, content_type="image/png"):
        if data is None:
            buffer = BytesIO()
            Image.new("RGB", (30, 40), "blue").save(buffer, format="PNG")
            data = buffer.getvalue()
        return await client.post(
            "/api/v1/inspiration",
            files={"image": ("look", data, content_type)},
            headers=auth_headers,
        )

    monkeypatch.setattr(AIService, "_call_with_fallback", vision)
    state.upload = upload
    return state


def slot(item_id=None, **changes):
    return {
        "type": "shirt",
        "color": "blue",
        "fit": "regular",
        "pattern": "solid",
        "item_id": str(item_id) if item_id else None,
        "match_strength": 0.9,
        **changes,
    }


def clothing(user_id, color="blue"):
    return ClothingItem(
        user_id=user_id,
        type="shirt",
        primary_color=color,
        image_path=f"test/{uuid4()}.jpg",
        status=ItemStatus.ready,
        tags={},
    )


async def test_all_gap_look_is_saved_and_can_be_compared(
    inspiration, client, auth_headers, db_session
):
    response = await inspiration.upload()
    assert response.status_code == 201, response.text
    look = response.json()
    assert look["slots"][0]["no_match"] and look["slots"][0]["item_id"] is None
    assert look["slots"][0]["match_strength"] == 0
    assert look["items"] == []
    saved = await db_session.get(Outfit, UUID(look["id"]))
    assert saved.scheduled_for is None
    assert (
        await client.get(backend_url(look["image_url"]), headers=auth_headers)
    ).status_code == 200
    assert (await client.get("/api/v1/inspiration", headers=auth_headers)).json()[0]["id"] == look[
        "id"
    ]


async def test_inspiration_uses_user_active_hours(inspiration, client, auth_headers, monkeypatch):
    from app.services import inspiration_service

    saved = await client.patch(
        "/api/v1/users/me/preferences",
        headers=auth_headers,
        json={"active_start_hour": 10, "active_end_hour": 17},
    )
    assert saved.status_code == 200
    monkeypatch.setattr(WeatherService, "get_current_weather", inspiration.original_weather)
    monkeypatch.setattr(WeatherService, "_cache_get", AsyncMock(return_value=None))
    monkeypatch.setattr(WeatherService, "_cache_set", AsyncMock())
    payload = {
        "current": {"time": "2026-10-29T07:00", "temperature_2m": 6.8, "apparent_temperature": 5},
        "hourly": {
            "time": [f"2026-10-29T{hour:02d}:00" for hour in range(24)],
            "temperature_2m": [1] * 10 + [20] * 8 + [2] * 6,
            "apparent_temperature": [19] * 24,
        },
    }
    original_get = httpx.AsyncClient.get

    async def get(instance, url, **kwargs):
        if str(url).endswith("/forecast"):
            return httpx.Response(200, json=payload, request=httpx.Request("GET", url))
        return await original_get(instance, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    with patch.object(
        inspiration_service, "score_items", wraps=inspiration_service.score_items
    ) as score:
        response = await inspiration.upload()
    assert response.status_code == 201
    weather = score.call_args.args[1]
    assert (weather.window_min, weather.window_max) == (20, 20)
    assert (weather.window_start_hour, weather.window_end_hour) == (10, 17)
    assert (weather.temperature, weather.feels_like) == (20, 19)


async def test_full_eligible_wardrobe_reaches_vision(inspiration, db_session):
    items = [clothing(inspiration.user.id, "red") for _ in range(70)]
    blue = clothing(inspiration.user.id)
    db_session.add_all([*items, blue])
    await db_session.commit()
    inspiration.text = json.dumps({"slots": [slot(blue.id)], "allowed_item_ids": [str(blue.id)]})
    response = await inspiration.upload()
    assert str(blue.id) in str(inspiration.prompts)
    assert response.status_code == 201, response.text
    assert response.json()["slots"][0]["item_id"] == str(blue.id)


@pytest.mark.parametrize("bulk", [False, True])
async def test_generic_delete_removes_inspiration_image(
    inspiration, client, auth_headers, db_session, bulk
):
    outfit = Outfit(
        user_id=inspiration.user.id,
        occasion="casual",
        ai_raw_response={"inspiration": {"slots": [], "items": []}},
    )
    db_session.add(outfit)
    await db_session.commit()
    path = InspirationService(db_session).image_path(inspiration.user.id, outfit.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"image")
    if bulk:
        response = await client.post(
            "/api/v1/outfits/bulk/delete",
            json={"outfit_ids": [str(outfit.id)]},
            headers=auth_headers,
        )
        assert response.status_code == 200 and response.json()["deleted"] == 1
    else:
        response = await client.delete(f"/api/v1/outfits/{outfit.id}", headers=auth_headers)
        assert response.status_code == 204
    assert not path.exists()


@pytest.mark.parametrize("text", ["```", '```{"slots":[]}```'])
async def test_malformed_fence_returns_502_and_cleans_upload(inspiration, text):
    inspiration.text = text
    response = await inspiration.upload()
    assert response.status_code == 502
    assert not list(inspiration.path.rglob("*.jpg"))


async def test_weather_failure_returns_502(inspiration):
    inspiration.weather.side_effect = WeatherServiceError("unavailable")
    response = await inspiration.upload()
    assert response.status_code == 502


@pytest.mark.parametrize("text", [None, "{}"])
async def test_failed_analysis_cleans_temporary_image(inspiration, text):
    inspiration.text = text
    response = await inspiration.upload()
    assert response.status_code == 502
    assert not list(inspiration.path.rglob("*.jpg"))


async def test_foreign_item_and_nonowner_access_are_rejected(
    inspiration, client, auth_headers, db_session
):
    other = User(
        id=uuid4(), external_id=str(uuid4()), email=f"{uuid4()}@example.com", display_name="Other"
    )
    (identity,) = await add_users_with_identities(db_session, other)
    foreign = clothing(other.id)
    db_session.add(foreign)
    await db_session.commit()
    inspiration.text = json.dumps(
        {"slots": [slot(foreign.id)], "allowed_item_ids": [str(foreign.id)]}
    )
    response = await inspiration.upload()
    assert str(foreign.id) not in str(inspiration.prompts)
    assert response.status_code == 201, response.text
    look = response.json()
    assert look["slots"][0]["no_match"] and look["items"] == []
    headers = {"Authorization": f"Bearer {create_access_token(other.id, identity.id)}"}
    assert (
        await client.get(backend_url(look["image_url"].split("?")[0]), headers=headers)
    ).status_code == 401
    assert (
        await client.delete(f"/api/v1/inspiration/{look['id']}", headers=headers)
    ).status_code == 404
    assert (await client.get(backend_url(look["image_url"]))).status_code == 401
    assert (
        await client.delete(f"/api/v1/inspiration/{look['id']}", headers=auth_headers)
    ).status_code == 204
    assert not list(inspiration.path.rglob("*.jpg"))


async def test_vision_instructions_are_separate_from_image(inspiration, db_session):
    shirt = clothing(inspiration.user.id)
    db_session.add(shirt)
    await db_session.commit()
    inspiration.text = json.dumps(
        {"slots": [slot(shirt.id, type=" Shirt ")], "allowed_item_ids": [str(shirt.id)]}
    )
    response = await inspiration.upload()
    assert response.status_code == 201, response.text
    assert response.json()["slots"][0]["item_id"] == str(shirt.id)
    assert inspiration.prompts[0][0]["role"] == "system"
    assert "boots" in inspiration.prompts[0][0]["content"]
    assert "No hats" in inspiration.prompts[0][0]["content"]
    assert inspiration.prompts[0][1]["role"] == "user"


@pytest.mark.parametrize("content_type,status", [("image/bmp", 415), ("image/jpeg", 422)])
async def test_unsupported_format_is_rejected(inspiration, db_session, content_type, status):
    shirt = clothing(inspiration.user.id)
    db_session.add(shirt)
    await db_session.commit()
    inspiration.text = json.dumps({"slots": [slot(shirt.id)], "allowed_item_ids": [str(shirt.id)]})
    buffer = BytesIO()
    Image.new("RGB", (30, 40)).save(buffer, format="BMP")
    response = await inspiration.upload(buffer.getvalue(), content_type)
    assert response.status_code == status
    assert inspiration.prompts == []
    assert not list(inspiration.path.rglob("*.jpg"))
