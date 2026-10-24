import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.orm import selectinload

from app.models.item import ClothingItem, ItemStatus
from app.models.outfit import Outfit, OutfitSource, UserFeedback
from app.models.user import User
from app.services.recommendation_service import RecommendationService
from app.services.wear_service import WearService
from app.services.weather_service import DailyForecast
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


def forecast(service, day):
    service.weather_service.get_daily_forecast = AsyncMock(
        return_value=[DailyForecast(day.isoformat(), 10, 22, 20, "sunny", 0)]
    )


async def prepare(db, user):
    user.location_lat = 40
    user.location_lon = -74
    await db.commit()


async def test_topup_repeat_and_full_day(db_session, test_user, day_generation):
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


async def test_two_callers_recount(session_maker, db_session, test_user, day_generation):
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

    from unittest.mock import patch

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


async def test_lock_timeout_returns_existing(session_maker, db_session, test_user, day_generation):
    await prepare(db_session, test_user)
    day = get_user_today(test_user)
    async with session_maker() as holder:
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{test_user.id}:{day.isoformat()}"},
        )
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
    assert service._defer_commit is False


async def test_generation_failure_rolls_back_and_resets(
    db_session, test_user, day_generation, monkeypatch
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
    assert service._defer_commit is False
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Outfit)
            .where(Outfit.user_id == user_id, Outfit.scheduled_for == day)
        )
        == 0
    )


async def test_route_dated_dispatch(client, auth_headers, test_user, monkeypatch):
    top_up = AsyncMock(return_value=[])
    monkeypatch.setattr(RecommendationService, "top_up_day", top_up)
    day = get_user_today(test_user) + timedelta(days=7)
    response = await client.post(
        "/api/v1/outfits/suggest-options", json={"date": day.isoformat()}, headers=auth_headers
    )
    assert response.status_code == 200
    assert top_up.call_args.kwargs["day"] == day
