import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db
from app.main import app
from app.models.item import ClothingItem, ItemStatus
from app.models.outfit import Outfit, OutfitItem, OutfitSource, OutfitStatus, UserFeedback
from app.models.user import User
from app.services import recommendation_service
from app.services.recommendation_service import RecommendationService
from app.services.wear_service import WearService
from app.services.weather_service import (
    DailyForecast,
    GeocodingServiceError,
    WeatherData,
    WeatherService,
    WeatherServiceError,
)
from app.utils.timezone import get_user_today

TEST_HANG_CEILING_SECONDS = 20


@pytest_asyncio.fixture
async def day_generation(monkeypatch, db_session, test_user):
    calls = []
    items = [
        ClothingItem(
            user_id=test_user.id, type=kind, image_path="test.jpg", status=ItemStatus.ready
        )
        for kind in ["shirt", "pants"]
    ]
    db_session.add_all(items)
    await db_session.commit()
    number_map = {number: item.id for number, item in enumerate(items, start=1)}

    async def generate(service, **kwargs):
        calls.append(kwargs)
        outfits = []
        for number in range(kwargs["count"]):
            outfits.append(
                await service._materialize_outfit(
                    {"items": [1, 2], "headline": f"Option {number}"},
                    kwargs["user"],
                    kwargs["weather_override"],
                    kwargs["occasion"],
                    OutfitSource.on_demand,
                    number_map,
                    scheduled_date=kwargs["scheduled_date"],
                )
            )
        return outfits

    monkeypatch.setattr(RecommendationService, "generate_recommendations", generate)
    return calls


@pytest.fixture
def forecast(monkeypatch):
    original_get = httpx.AsyncClient.get

    def configure(service, day, hourly=None):
        service.weather_service.get_daily_forecast = AsyncMock(
            return_value=[DailyForecast(day.isoformat(), 10, 22, 20, "sunny", 0)]
        )
        payload = {
            "hourly": hourly
            or {
                "time": [f"{day.isoformat()}T{hour:02d}:00" for hour in range(24)],
                "temperature_2m": [10, 22] * 12,
                "apparent_temperature": [9, 21] * 12,
            }
        }

        async def get(client, url, **kwargs):
            if str(url).endswith("/forecast"):
                return httpx.Response(200, json=payload, request=httpx.Request("GET", url))
            return await original_get(client, url, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "get", get)

    return configure


async def test_pick_a_day_excludes_cold_morning(db_session, test_user, day_generation, forecast):
    from app.models.preference import UserPreference

    await prepare(db_session, test_user)
    test_user.preferences = UserPreference(
        user_id=test_user.id, active_start_hour=10, active_end_hour=17
    )
    await db_session.commit()
    day = get_user_today(test_user) + timedelta(days=5)
    service = RecommendationService(db_session)
    forecast(service, day)
    payload = {
        "hourly": {
            "time": [f"{day.isoformat()}T{hour:02d}:00" for hour in range(24)],
            "temperature_2m": [6.8] * 10 + [14, 15, 16, 17, 18, 19, 20, 21] + [7] * 6,
            "apparent_temperature": [5] * 10 + [13, 14, 15, 16, 17, 18, 19, 20] + [6] * 6,
        }
    }
    with patch(
        "httpx.AsyncClient.get",
        return_value=httpx.Response(
            200, json=payload, request=httpx.Request("GET", "http://forecast")
        ),
    ) as request:
        await service.top_up_day(test_user, day, "casual")
    weather = day_generation[0]["weather_override"]
    assert (weather.window_min, weather.window_max) == (14, 21)
    assert weather.temperature == 17.5
    assert weather.feels_like == 16.5
    params = request.call_args.kwargs["params"]
    assert params["forecast_days"] == 10
    assert "start_date" not in params


async def test_pick_a_day_prompt_uses_saved_hours_and_unit(
    db_session, test_user, client, auth_headers, http_ai, forecast
):
    saved = await client.patch(
        "/api/v1/users/me/preferences",
        headers=auth_headers,
        json={"active_start_hour": 10, "active_end_hour": 17, "temperature_unit": "fahrenheit"},
    )
    assert saved.status_code == 200
    await db_session.refresh(test_user, ["preferences"])
    service = RecommendationService(db_session)
    day = get_user_today(test_user) + timedelta(days=1)
    forecast(service, day)
    forecast(
        service,
        day,
        hourly={
            "time": [f"{day.isoformat()}T{hour:02d}:00" for hour in range(24)],
            "temperature_2m": [14, 20] * 12,
            "apparent_temperature": [13, 19] * 12,
        },
    )
    outfits = await service.top_up_day(test_user, day, "casual")
    assert len(outfits) == 3
    assert "10 AM to 5 PM: 57 to 68°F" in http_ai["prompts"][0]
    weather = outfits[0].weather_data
    assert (weather["window_min"], weather["window_max"]) == (14, 20)


async def test_same_day_scoring_and_prompt_use_window_averages(db_session, test_user, http_ai):
    from app.models.preference import UserPreference

    test_user.preferences = UserPreference(
        user_id=test_user.id,
        active_start_hour=10,
        active_end_hour=17,
        temperature_unit="fahrenheit",
    )
    await db_session.commit()
    service = RecommendationService(db_session)
    payload = {
        "current": {"time": "2026-10-29T07:00", "temperature_2m": 6.8, "apparent_temperature": 5},
        "hourly": {
            "time": [f"2026-10-29T{hour:02d}:00" for hour in range(24)],
            "temperature_2m": [1] * 10 + [14, 26] * 4 + [2] * 6,
            "apparent_temperature": [1] * 10 + [13, 25] * 4 + [2] * 6,
        },
    }
    with (
        patch.object(service.weather_service, "_cache_get", return_value=None),
        patch.object(service.weather_service, "_cache_set"),
        patch(
            "httpx.AsyncClient.get",
            return_value=httpx.Response(
                200, json=payload, request=httpx.Request("GET", "http://forecast")
            ),
        ),
        patch.object(
            recommendation_service, "score_items", wraps=recommendation_service.score_items
        ) as score,
    ):
        outfits = await service.generate_recommendations(test_user, "casual", count=3)
    weather = score.call_args.kwargs["weather"]
    assert (weather.temperature, weather.feels_like) == (20, 19)
    assert (weather.window_start_hour, weather.window_end_hour) == (10, 17)
    assert "10 AM to 5 PM: 57 to 79°F" in http_ai["prompts"][0]
    assert "20.0°C (feels like 19.0°C)" in http_ai["prompts"][0]
    assert "14.0°C to 26.0°C" not in http_ai["prompts"][0]
    assert "large swing" in http_ai["prompts"][0]
    assert outfits[0].weather_data["temperature"] == 6.8
    assert outfits[0].weather_data["window_temperature"] == 20


async def prepare(db, user):
    user.location_lat = 40
    user.location_lon = -74
    await db.commit()


async def test_topup_repeat_and_full_day(db_session, test_user, day_generation, forecast):
    await prepare(db_session, test_user)
    day = get_user_today(test_user) + timedelta(days=1)
    service = RecommendationService(db_session)
    forecast(service, day)
    db_session.add(
        Outfit(
            user_id=test_user.id,
            occasion="casual",
            scheduled_for=day,
            source=OutfitSource.scheduled,
        )
    )
    await db_session.commit()
    first = await service.top_up_day(test_user, day, "casual")
    assert len(first) == 3
    assert first[0].source == OutfitSource.scheduled
    assert all(outfit.scheduled_for == day for outfit in first)
    assert len(await service.top_up_day(test_user, day, "casual")) == 3
    assert len(day_generation) == 1
    assert day_generation[0]["count"] == 2
    assert day_generation[0]["time_of_day"] == "full day"
    weather = day_generation[0]["weather_override"]
    assert (weather.window_min, weather.window_max) == (10, 22)


@pytest.mark.parametrize("offset,logged", [(-1, False), (0, True)])
async def test_readonly_days(db_session, test_user, day_generation, offset, logged):
    await prepare(db_session, test_user)
    day = get_user_today(test_user) + timedelta(days=offset)
    outfit = Outfit(
        user_id=test_user.id, occasion="casual", scheduled_for=day, source=OutfitSource.on_demand
    )
    db_session.add(outfit)
    await db_session.flush()
    if logged:
        db_session.add(
            UserFeedback(
                outfit_id=outfit.id,
                worn_at=day,
                actually_worn=True,
                accepted=True,
            )
        )
    await db_session.commit()
    result = await RecommendationService(db_session).top_up_day(test_user, day, "casual")
    assert len(result) == (0 if logged else 1)
    assert day_generation == []


async def test_future_limit(db_session, test_user, day_generation):
    with pytest.raises(ValueError, match="7 days"):
        await RecommendationService(db_session).top_up_day(
            test_user, get_user_today(test_user) + timedelta(days=8), "casual"
        )
    assert day_generation == []


async def test_two_callers_recount(
    session_maker, db_session, test_user, day_generation, monkeypatch, forecast
):
    monkeypatch.setattr(
        recommendation_service, "DAY_LOCK_TIMEOUT_MS", TEST_HANG_CEILING_SECONDS * 1000
    )
    await prepare(db_session, test_user)
    day = get_user_today(test_user) + timedelta(days=1)
    user_id = test_user.id
    started = asyncio.Event()
    release = asyncio.Event()
    second_lock = asyncio.Event()
    original_lock = WearService.lock_day
    lock_calls = 0

    async def observed_lock(service, user_id, worn_on):
        nonlocal lock_calls
        lock_calls += 1
        if lock_calls == 2:
            second_lock.set()
        await original_lock(service, user_id, worn_on)

    original = RecommendationService.generate_recommendations

    async def held_generate(service, **kwargs):
        started.set()
        await release.wait()
        return await original(service, **kwargs)

    async def call():
        async with session_maker() as db:
            user = await db.scalar(
                select(User).where(User.id == user_id).options(selectinload(User.preferences))
            )
            service = RecommendationService(db)
            forecast(service, day)
            return await service.top_up_day(user, day, "casual")

    async with asyncio.timeout(TEST_HANG_CEILING_SECONDS):
        with (
            patch.object(RecommendationService, "generate_recommendations", held_generate),
            patch.object(WearService, "lock_day", observed_lock),
        ):
            first = asyncio.create_task(call())
            await started.wait()
            second = asyncio.create_task(call())
            await second_lock.wait()
            release.set()
            results = await asyncio.gather(first, second)
    assert [len(result) for result in results] == [3, 3]
    assert len(day_generation) == 1
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Outfit)
            .where(Outfit.user_id == user_id, Outfit.scheduled_for == day)
        )
        == 3
    )


async def test_lock_timeout_returns_existing(
    session_maker, db_session, test_user, day_generation, monkeypatch
):
    monkeypatch.setattr(recommendation_service, "DAY_LOCK_TIMEOUT_MS", 3)
    await prepare(db_session, test_user)
    day = get_user_today(test_user)
    async with session_maker() as holder:
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{test_user.id}:{day.isoformat()}"},
        )
        async with asyncio.timeout(TEST_HANG_CEILING_SECONDS):
            result = await RecommendationService(db_session).top_up_day(test_user, day, "casual")
        assert result == []
        assert day_generation == []
        await holder.rollback()


async def test_route_no_date_unchanged(client, auth_headers, monkeypatch):
    generate = AsyncMock(return_value=[])
    monkeypatch.setattr(RecommendationService, "generate_recommendations", generate)
    response = await client.post("/api/v1/outfits/suggest-options", json={}, headers=auth_headers)
    assert response.status_code == 200
    assert generate.await_count == 1
    assert generate.call_args.kwargs["count"] == 3


@pytest.mark.parametrize("value", ["bad-date", "2026-99-01"])
async def test_route_malformed_date(client, auth_headers, value):
    response = await client.post(
        "/api/v1/outfits/suggest-options", json={"date": value}, headers=auth_headers
    )
    assert response.status_code == 422


async def test_missing_forecast_writes_nothing(db_session, test_user, day_generation):
    await prepare(db_session, test_user)
    service = RecommendationService(db_session)
    service.weather_service.get_daily_forecast = AsyncMock(return_value=[])
    with pytest.raises(ValueError, match="Forecast unavailable"):
        await service.top_up_day(test_user, get_user_today(test_user), "casual")
    assert day_generation == []


async def test_generation_failure_rolls_back_and_resets(
    db_session, session_maker, test_user, http_ai, monkeypatch, forecast
):
    await prepare(db_session, test_user)
    user_id = test_user.id
    day = get_user_today(test_user)
    service = RecommendationService(db_session)
    forecast(service, day)
    original = service.generate_recommendations

    async def fail_after_materializing(**kwargs):
        await original(**kwargs)
        raise ValueError("Generation failed")

    monkeypatch.setattr(service, "generate_recommendations", fail_after_materializing)
    with pytest.raises(ValueError, match="Generation failed"):
        await service.top_up_day(test_user, day, "casual")
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Outfit)
            .where(Outfit.user_id == user_id, Outfit.scheduled_for == day)
        )
        == 0
    )
    await db_session.refresh(test_user)
    await db_session.refresh(test_user, ["preferences"])
    monkeypatch.setattr(service, "generate_recommendations", original)
    await service.generate_recommendations(
        user=test_user,
        occasion="casual",
        count=3,
        weather_override=WeatherData(16, 16, 50, 0, 0, 0, "sunny", 0, True, 0, datetime.now(UTC)),
    )
    async with session_maker() as observer:
        assert (
            await observer.scalar(
                select(func.count())
                .select_from(Outfit)
                .where(Outfit.user_id == user_id, Outfit.scheduled_for == day)
            )
            == 3
        )


@pytest_asyncio.fixture
async def http_ai(db_session, test_user, monkeypatch):
    await prepare(db_session, test_user)
    await db_session.refresh(test_user, ["preferences"])
    db_session.add_all(
        [
            ClothingItem(
                user_id=test_user.id, type=kind, image_path="test.jpg", status=ItemStatus.ready
            )
            for kind in ["shirt", "pants"]
        ]
    )
    await db_session.commit()
    monkeypatch.setattr(get_settings(), "ai_base_url", "https://ai.test/v1")
    state = {"count": 3, "prompts": []}
    original_post = httpx.AsyncClient.post

    async def post(client, url, **kwargs):
        if not str(url).endswith("/chat/completions"):
            return await original_post(client, url, **kwargs)
        body = json.loads(kwargs["content"]) if "content" in kwargs else kwargs["json"]
        state["prompts"].append(body["messages"][-1]["content"])
        content = json.dumps(
            {
                "outfits": [
                    {"items": [1, 2], "headline": "Test option"} for _ in range(state["count"])
                ]
            }
        )
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    return state


@pytest.mark.parametrize("missing", [1, 2])
async def test_real_generator_requests_missing_count(
    db_session, test_user, http_ai, missing, forecast
):
    day = get_user_today(test_user)
    for _ in range(3 - missing):
        db_session.add(
            Outfit(
                user_id=test_user.id,
                occasion="casual",
                scheduled_for=day,
                source=OutfitSource.scheduled,
            )
        )
    await db_session.commit()
    http_ai["count"] = missing
    service = RecommendationService(db_session)
    forecast(service, day)
    assert len(await service.top_up_day(test_user, day, "casual")) == 3
    assert len(http_ai["prompts"]) == 1
    assert f"exactly {missing} distinct outfit options" in http_ai["prompts"][0]


@pytest.mark.parametrize("geocode", [False, True])
async def test_weather_failures_are_400(
    client, auth_headers, db_session, test_user, monkeypatch, geocode
):
    await prepare(db_session, test_user)
    if geocode:
        test_user.location_lat = test_user.location_lon = None
        test_user.location_name = "New York"
        await db_session.commit()
    method = "geocode_location_name" if geocode else "get_daily_forecast"
    error = GeocodingServiceError if geocode else WeatherServiceError
    monkeypatch.setattr(
        WeatherService, method, AsyncMock(side_effect=error("Provider unavailable"))
    )
    response = await client.post(
        "/api/v1/outfits/suggest-options",
        headers=auth_headers,
        json={"date": get_user_today(test_user).isoformat()},
    )
    assert response.status_code == 400
    assert (
        "location" in response.json()["detail"]
        if geocode
        else "weather" in response.json()["detail"]
    )


async def test_seventh_day_with_different_location_date(
    db_session, test_user, day_generation, monkeypatch
):
    location_today = date(2026, 10, 24)
    target = date(2026, 11, 1)
    await prepare(db_session, test_user)
    test_user.timezone = "UTC"
    await db_session.commit()

    async def get(client, url, **kwargs):
        params = kwargs["params"]
        assert params["timezone"] == "auto"
        if "hourly" in params:
            return httpx.Response(200, request=httpx.Request("GET", url), json={"hourly": {}})
        days = params["forecast_days"]
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "daily": {
                    "time": [
                        (location_today + timedelta(days=offset)).isoformat()
                        for offset in range(days)
                    ],
                    "temperature_2m_min": [10] * days,
                    "temperature_2m_max": [22] * days,
                }
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    with patch("app.utils.timezone.datetime") as clock:
        clock.now.return_value = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
        assert get_user_today(test_user) == date(2026, 10, 25)
        result = await RecommendationService(db_session).top_up_day(test_user, target, "casual")
    assert len(result) == 3
    assert all(outfit.scheduled_for == target for outfit in result)


async def test_day_lock_survives_savepoint_until_commit(
    db_session,
    session_maker,
    client,
    auth_headers,
    test_user,
    day_generation,
    monkeypatch,
    forecast,
):
    await prepare(db_session, test_user)
    user_id, day = test_user.id, get_user_today(test_user)
    template = Outfit(user_id=user_id, occasion="casual", source=OutfitSource.manual)
    db_session.add(template)
    await db_session.flush()
    for position, item_id in enumerate(
        await db_session.scalars(select(ClothingItem.id).where(ClothingItem.user_id == user_id))
    ):
        db_session.add(OutfitItem(outfit_id=template.id, item_id=item_id, position=position))
    await db_session.commit()
    template_id = template.id
    generating, release = asyncio.Event(), asyncio.Event()
    pids = {}
    generate = RecommendationService.generate_recommendations
    lock = WearService.lock_day
    monkeypatch.setattr(
        recommendation_service, "DAY_LOCK_TIMEOUT_MS", TEST_HANG_CEILING_SECONDS * 1000
    )

    async def observed_lock(service, user_id, worn_on):
        pids[asyncio.current_task().get_name()] = await service.db.scalar(
            text("SELECT pg_backend_pid()")
        )
        await lock(service, user_id, worn_on)

    async def held_generate(service, **kwargs):
        generating.set()
        await release.wait()
        return await generate(service, **kwargs)

    async def database():
        async with session_maker() as db:
            yield db

    async def top_up():
        async with session_maker() as db:
            user = await db.scalar(
                select(User).where(User.id == user_id).options(selectinload(User.preferences))
            )
            service = RecommendationService(db)
            forecast(service, day)
            return await service.top_up_day(user, day, "casual")

    monkeypatch.setitem(app.dependency_overrides, get_db, database)
    monkeypatch.setattr(WearService, "lock_day", observed_lock)
    monkeypatch.setattr(RecommendationService, "generate_recommendations", held_generate)

    async def wait_until_blocked(name):
        async with session_maker() as observer:
            while True:
                if name in pids and await observer.scalar(
                    text("SELECT :holder = ANY(pg_blocking_pids(:waiter))"),
                    {"holder": pids["first-topup"], "waiter": pids[name]},
                ):
                    return
                await observer.execute(text("SELECT 1"))

    async with asyncio.timeout(TEST_HANG_CEILING_SECONDS), asyncio.TaskGroup() as tasks:
        first = tasks.create_task(top_up(), name="first-topup")
        await generating.wait()
        second = tasks.create_task(top_up(), name="second-topup")
        await wait_until_blocked("second-topup")
        wear = tasks.create_task(
            client.post(
                f"/api/v1/outfits/{template_id}/wore",
                headers=auth_headers,
                json={"date": day.isoformat()},
            ),
            name="wear",
        )
        await wait_until_blocked("wear")
        assert not first.done() and not second.done() and not wear.done()
        assert day_generation == []
        release.set()
    assert len(first.result()) == len(second.result()) == 3
    assert wear.result().status_code == 200, wear.result().text
    assert len(day_generation) == 1
    async with session_maker() as observer:
        assert (
            await observer.scalar(
                select(func.count())
                .select_from(Outfit)
                .where(
                    Outfit.user_id == user_id,
                    Outfit.scheduled_for == day,
                    Outfit.source == OutfitSource.on_demand,
                    Outfit.status == OutfitStatus.skipped,
                )
            )
            == 3
        )
