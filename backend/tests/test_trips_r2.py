import asyncio
import json
import subprocess
import sys
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outfit import Outfit
from app.models.trip import Trip
from tests.test_trips import capture, real_plan, request

HANG_CEILING = 30


async def test_close_nested_session_then_fail_rolls_back_all_rows(db_session, test_user):
    user_id = test_user.id
    events = []
    try:
        async with AsyncSession(
            bind=await db_session.connection(),
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        ) as planning:
            planning.add(
                Outfit(user_id=user_id, occasion="casual", scheduled_for=date(2030, 10, 24))
            )
            await planning.commit()
            await planning.close()
            events.append("closed-after-day-one-commit")
            raise ValueError("Day two failed")
    except ValueError:
        events.append("outer-rollback")
        await db_session.rollback()
    assert events == ["closed-after-day-one-commit", "outer-rollback"]
    for model in (Outfit, Trip):
        assert (
            await db_session.scalar(
                select(func.count()).select_from(model).where(model.user_id == user_id)
            )
            == 0
        )


def test_trip_metadata_registered_without_api_imports():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.models; from app.database import Base; assert 'trips' in Base.metadata.tables; assert 'Trip' in app.models.__all__",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


async def test_disabled_text_ai_returns_503_before_weather(client, auth_headers, monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("AI_TEXT_ENABLED", "false")
    settings = Settings()
    assert settings.effective_ai_text_enabled is False
    with (
        patch("app.services.ai_service.get_settings", return_value=settings),
        patch("app.services.trip_service.TripWeather.for_dates", AsyncMock()) as weather,
    ):
        response = await client.post(
            "/api/v1/trips", headers=auth_headers, json=request().model_dump(mode="json")
        )
    assert (response.status_code, response.json()["detail"]) == (503, "AI planning is disabled")
    weather.assert_not_awaited()


async def test_trip_weather_follows_provider_redirects():
    from app.services.trip_service import TripWeather
    from app.services.weather_service import WeatherService

    original_client = httpx.AsyncClient
    urls = []

    def provider(request):
        urls.append(str(request.url))
        if request.url.path.endswith("/forecast"):
            return httpx.Response(307, headers={"Location": "https://forecast.example/redirected"})
        return httpx.Response(200, json=capture()["forecast"])

    with (
        patch(
            "app.services.trip_service.httpx.AsyncClient",
            side_effect=lambda **kwargs: original_client(
                transport=httpx.MockTransport(provider), **kwargs
            ),
        ),
        patch.object(
            WeatherService,
            "geocode_location_name",
            AsyncMock(return_value=(48.8566, 2.3522, "Paris")),
        ),
    ):
        result = await TripWeather().for_dates("Paris", [date(2026, 10, 24)])
    assert result[0][1] == "forecast" and len(urls) == 2


async def test_real_plan_closes_nested_session_before_failure_rollback(
    client, db_session, test_user, auth_headers, monkeypatch
):
    user_id = test_user.id
    events = []
    original_close = AsyncSession.close
    original_rollback = db_session.rollback

    async def close(session):
        await original_close(session)
        if session is not db_session:
            events.append("nested-close")

    async def rollback():
        events.append("outer-rollback")
        await original_rollback()

    monkeypatch.setattr(AsyncSession, "close", close)
    monkeypatch.setattr(db_session, "rollback", rollback)
    response, prompts = await real_plan(
        client, db_session, test_user, auth_headers, ["t-shirt", "hoodie", "jeans"], 2
    )
    assert response.status_code == 422 and len(prompts) == 2
    assert events.index("nested-close") < events.index("outer-rollback")
    for model in (Outfit, Trip):
        assert (
            await db_session.scalar(
                select(func.count()).select_from(model).where(model.user_id == user_id)
            )
            == 0
        )


@pytest.fixture
def independent_sessions(monkeypatch, session_maker):
    import app.database as database
    from app.main import app

    monkeypatch.setattr(database, "async_session_maker", session_maker)
    monkeypatch.setattr(app, "dependency_overrides", {})
    return session_maker


async def seed_trip_wardrobe(session, user):
    from app.models.item import ClothingItem, ItemStatus

    session.add_all(
        [
            ClothingItem(
                user_id=user.id,
                type=kind,
                name=f"trip-{kind}",
                image_path="trip-test",
                status=ItemStatus.ready,
            )
            for kind in ("t-shirt", "jeans")
        ]
    )
    await session.commit()


def ai_reply(prompt, **kwargs):
    numbers = [
        int(line[1 : line.index("]")])
        for line in prompt.splitlines()
        if line.startswith("[") and '"trip-' in line
    ]
    return SimpleNamespace(
        content=json.dumps({"items": numbers}), model="fixture", endpoint="fixture"
    )


async def test_concurrent_overlapping_plans_commit_one_trip(
    independent_sessions, db_session, test_user, auth_headers
):
    from app.main import app
    from app.services.trip_service import TripWeather

    await seed_trip_wardrobe(db_session, test_user)
    user_id = test_user.id
    arrived = []
    both_ready, release = asyncio.Event(), asyncio.Event()
    weather = TripWeather.climate_normal(date(2030, 10, 24), capture()["climate"]["daily"])

    async def concurrent_ai(prompt, **kwargs):
        arrived.append(prompt)
        if len(arrived) == 2:
            both_ready.set()
        await release.wait()
        return ai_reply(prompt, **kwargs)

    with (
        patch(
            "app.services.trip_service.TripWeather.for_dates",
            AsyncMock(return_value=[(weather, "forecast")]),
        ),
        patch(
            "app.services.recommendation_service.AIService.generate_text",
            AsyncMock(side_effect=concurrent_ai),
        ),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            tasks = [
                asyncio.create_task(
                    client.post(
                        "/api/v1/trips",
                        headers=auth_headers,
                        json=request().model_dump(mode="json"),
                    )
                )
                for _ in range(2)
            ]
            try:
                async with asyncio.timeout(HANG_CEILING):
                    await both_ready.wait()
                    release.set()
                    responses = await asyncio.gather(*tasks)
            finally:
                release.set()
                await asyncio.gather(*tasks, return_exceptions=True)
    assert sorted(response.status_code for response in responses) == [201, 409]
    for model in (Trip, Outfit):
        assert (
            await db_session.scalar(
                select(func.count()).select_from(model).where(model.user_id == user_id)
            )
            == 1
        )


async def test_user_patch_completes_while_plan_waits_for_ai(
    independent_sessions, db_session, test_user, auth_headers
):
    from app.main import app
    from app.services.trip_service import TripWeather

    await seed_trip_wardrobe(db_session, test_user)
    started, release = asyncio.Event(), asyncio.Event()
    weather = TripWeather.climate_normal(date(2030, 10, 24), capture()["climate"]["daily"])

    async def pending_ai(prompt, **kwargs):
        started.set()
        await release.wait()
        return ai_reply(prompt, **kwargs)

    with (
        patch(
            "app.services.trip_service.TripWeather.for_dates",
            AsyncMock(return_value=[(weather, "forecast")]),
        ),
        patch(
            "app.services.recommendation_service.AIService.generate_text",
            AsyncMock(side_effect=pending_ai),
        ),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            planning = asyncio.create_task(
                client.post(
                    "/api/v1/trips", headers=auth_headers, json=request().model_dump(mode="json")
                )
            )
            try:
                async with asyncio.timeout(HANG_CEILING):
                    await started.wait()
                    response = await client.patch(
                        "/api/v1/users/me",
                        headers=auth_headers,
                        json={"display_name": "Updated during trip planning"},
                    )
                    assert response.status_code == 200, response.text
                    assert response.json()["display_name"] == "Updated during trip planning"
                    assert not planning.done()
            finally:
                release.set()
                planned = await planning
    assert planned.status_code == 201, planned.text
