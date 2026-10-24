from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.trips import TripRequest
from app.models.outfit import Outfit
from app.models.trip import Trip
from app.services.trip_service import TripService, TripWeather, packing_list, reuse_limit


def item(kind, wears=0, interval=None):
    return SimpleNamespace(type=kind, wears_since_wash=wears, wash_interval=interval)


@pytest.mark.parametrize(
    "kind,wears,interval,expected",
    [
        ("t-shirt", 0, None, 1),
        ("shirt", 0, None, 1),
        ("jeans", 0, None, 6),
        ("jacket", 2, None, 6),
        ("jeans", 2, 3, 1),
        ("jeans", 3, 3, 0),
        ("unknown", 0, None, 1),
        ("shirt", 2, None, 0),
    ],
)
def test_reuse_limits(kind, wears, interval, expected):
    assert reuse_limit(item(kind, wears, interval)) == expected


def test_packing_has_one_entry_per_item_and_days_worn():
    shared = SimpleNamespace(item_id=uuid4(), item=SimpleNamespace(name="Jeans"))
    tops = [SimpleNamespace(item_id=uuid4(), item=SimpleNamespace(name="Top")) for _ in range(4)]
    outfits = [SimpleNamespace(items=[shared, top]) for top in tops]
    packing = packing_list(outfits)
    assert len(packing) == 5
    assert packing[0] == {
        "item_id": str(shared.item_id),
        "name": "Jeans",
        "count": 1,
        "days_worn": 4,
    }
    assert all(entry["count"] == 1 for entry in packing)
    assert packing_list([]) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"destination": "   "},
        {"end_date": "2026-10-24"},
        {"end_date": "2026-11-08"},
        {"occasions": []},
        {"occasions": ["bogus"]},
        {"start_date": "broken"},
    ],
)
def test_invalid_trip(changes):
    with pytest.raises(ValidationError):
        TripRequest.model_validate(
            {
                "destination": "Paris",
                "start_date": "2026-10-26",
                "end_date": "2026-10-29",
                "occasions": ["casual"],
            }
            | changes
        )


def test_climate_normal_uses_matching_calendar_day_and_rejects_missing():
    daily = {
        "time": ["1991-12-01", "1992-12-01", "1992-12-02"],
        "temperature_2m_min": [2, 4, -20],
        "temperature_2m_max": [10, 12, -10],
    }
    weather = TripWeather.climate_normal(date(2027, 12, 1), daily)
    assert (weather.window_min, weather.window_max, weather.temperature) == (3, 11, 7)
    with pytest.raises(ValueError, match="unavailable"):
        TripWeather.climate_normal(date(2027, 1, 1), daily)


async def test_daytime_forecast_and_beyond_range_fallback():
    slots = [f"2026-10-26T{hour:02}:00" for hour in range(24)]
    hourly = {
        "time": slots,
        "temperature_2m": list(range(24)),
        "apparent_temperature": list(range(24)),
        "relative_humidity_2m": [50] * 24,
        "precipitation_probability": [20] * 24,
        "wind_speed_10m": [10] * 24,
        "weather_code": [0] * 24,
    }
    daily = {
        "time": ["1991-12-01", "1992-12-01"],
        "temperature_2m_min": [2, 4],
        "temperature_2m_max": [10, 12],
    }
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            200, json={"daily": daily} if "archive" in str(request.url) else {"hourly": hourly}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    with (
        patch("app.services.trip_service.httpx.AsyncClient", return_value=client),
        patch.object(
            TripWeather,
            "__init__",
            lambda self: setattr(
                self,
                "weather",
                SimpleNamespace(
                    base_url="https://api.open-meteo.com/v1",
                    geocode_location_name=AsyncMock(return_value=(48.8, 2.3, "Paris")),
                    _interpret_weather_code=lambda code: "sunny",
                ),
            ),
        ),
    ):
        results = await TripWeather().for_dates("Paris", [date(2026, 10, 26), date(2027, 12, 1)])
    assert [source for _, source in results] == ["forecast", "climate"]
    assert (results[0][0].window_min, results[0][0].window_max) == (10, 18)
    assert results[1][0].temperature == 7
    assert calls[0].url.params["latitude"] == "48.8"
    assert len(calls) == 2


async def test_trip_failure_rolls_back_committed_recommendations(db_session, test_user):
    request = TripRequest(
        destination="Paris",
        start_date=date(2027, 5, 1),
        end_date=date(2027, 5, 4),
        occasions=["casual"],
    )
    baseline = (
        await db_session.execute(
            select(func.count()).select_from(Outfit).where(Outfit.user_id == test_user.id)
        )
    ).scalar_one()
    user_id = test_user.id

    async def fail_after_commit(**kwargs):
        service_db = generated_service[0]
        outfit = Outfit(user_id=user_id, occasion="casual", scheduled_for=kwargs["scheduled_date"])
        service_db.add(outfit)
        await service_db.commit()
        if kwargs["scheduled_date"] != request.start_date:
            raise ValueError("Later day failed")
        return [
            SimpleNamespace(
                id=outfit.id,
                items=[
                    SimpleNamespace(item_id=uuid4(), item=SimpleNamespace(type=kind, name=kind))
                    for kind in ("shirt", "jeans")
                ],
            )
        ]

    generated_service = []

    def generator(session):
        generated_service.append(session)
        return SimpleNamespace(generate_recommendations=fail_after_commit)

    with (
        patch(
            "app.services.trip_service.TripWeather.for_dates",
            AsyncMock(
                return_value=[(SimpleNamespace(window_min=10, window_max=18), "forecast")] * 4
            ),
        ),
        patch("app.services.trip_service.RecommendationService", side_effect=generator),
        patch("app.services.trip_service.get_user_today", return_value=date(2026, 10, 23)),
    ):
        with pytest.raises(ValueError, match="Later day"):
            await TripService(db_session).plan(test_user, request)
    assert (
        await db_session.execute(
            select(func.count()).select_from(Outfit).where(Outfit.user_id == user_id)
        )
    ).scalar_one() == baseline
    assert (
        await db_session.execute(
            select(func.count()).select_from(Trip).where(Trip.user_id == user_id)
        )
    ).scalar_one() == 0
