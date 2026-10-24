import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.trips import TripRequest, TripResponse
from app.models.outfit import Outfit
from app.models.trip import Trip
from app.services.trip_service import TripService, TripWeather, packing_list, reuse_limit
from app.services.weather_service import WeatherService, WeatherServiceError


def item(kind, wears=0, interval=None):
    return SimpleNamespace(
        type=kind, subtype=None, name=kind, wears_since_wash=wears, wash_interval=interval
    )


@pytest.mark.parametrize(
    "kind,wears,interval,expected",
    [
        ("t-shirt", 0, None, 1),
        ("shirt", 0, None, 1),
        ("jeans", 0, None, 6),
        ("jacket", 1000, 999, float("inf")),
        ("jacket", 0, 1, 1),
        ("hoodie", 1, 2, 1),
        ("JEANS", 0, None, 6),
        ("jeans", 2, 3, 1),
        ("jeans", 3, 3, 0),
        ("unknown", 0, None, 3),
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
        {"end_date": "2026-11-09"},
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


def capture():
    return json.loads((Path(__file__).parent / "fixtures/trip_weather_ct107.json").read_text())


def test_climate_normal_uses_matching_calendar_day_and_rejects_missing():
    daily = capture()["climate"]["daily"]
    weather = TripWeather.climate_normal(date(2030, 10, 24), daily)
    assert (weather.window_min, weather.window_max, weather.temperature) == pytest.approx(
        (6.9, 11.3, 9.1)
    )
    with pytest.raises(WeatherServiceError, match="unavailable"):
        TripWeather.climate_normal(date(2030, 1, 1), daily)


async def weather_request(forecast=None):
    replies = capture()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=replies["climate"]
                if "archive" in str(request.url)
                else (forecast if forecast is not None else replies["forecast"]),
            )
        )
    )
    with (
        patch("app.services.trip_service.httpx.AsyncClient", return_value=client),
        patch.object(
            WeatherService,
            "geocode_location_name",
            AsyncMock(return_value=(48.8566, 2.3522, "Paris")),
        ),
    ):
        return await TripWeather().for_dates("Paris", [date(2026, 10, 24), date(2030, 10, 24)])


async def test_daytime_forecast_and_beyond_range_fallback():
    results = await weather_request()
    assert [source for _, source in results] == ["forecast", "climate"]
    assert (results[0][0].window_min, results[0][0].window_max) == (9.8, 12.4)
    assert results[1][0].temperature == pytest.approx(9.1)


@pytest.mark.parametrize(
    "field",
    [
        "temperature_2m",
        "apparent_temperature",
        "relative_humidity_2m",
        "precipitation_probability",
        "wind_speed_10m",
        "weather_code",
    ],
)
@pytest.mark.parametrize("value", [None, [], [None] * 24])
async def test_null_or_short_forecast_series_falls_back(field, value):
    forecast = capture()["forecast"]
    forecast["hourly"][field] = value
    assert [source for _, source in await weather_request(forecast)] == ["climate", "climate"]


@pytest.mark.parametrize("payload", [{}, {"hourly": {}}, {"hourly": {"time": ["malformed"]}}])
async def test_malformed_weather_is_provider_error(payload):
    with pytest.raises(WeatherServiceError):
        await weather_request(payload)


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
                    SimpleNamespace(item_id=uuid4(), item=item(kind)) for kind in ("shirt", "jeans")
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


async def test_four_day_api_plan_and_calendar(client, db_session, test_user, auth_headers):
    from app.models.item import ClothingItem, ItemStatus
    from app.models.outfit import OutfitSource
    from app.services.recommendation_service import RecommendationService

    wardrobe = [
        ClothingItem(
            user_id=test_user.id,
            type=kind,
            name=f"Trip {index}",
            image_path="trip-test",
            status=ItemStatus.ready,
            needs_wash=index == 0,
        )
        for index, kind in enumerate(["shirt"] * 5 + ["jeans", "jacket"])
    ]
    db_session.add_all(wardrobe)
    await db_session.commit()
    dirty_id = wardrobe[0].id
    top_ids = [entry.id for entry in wardrobe[1:5]]
    jeans_id = wardrobe[5].id
    jacket_id = wardrobe[6].id
    weather = TripWeather.climate_normal(date(2030, 10, 24), capture()["climate"]["daily"])
    seen = []

    async def generate(service, **kwargs):
        assert kwargs["time_of_day"].value == "full_day"
        excluded = kwargs["exclude_items"]
        assert dirty_id in excluded
        assert all(previous in excluded for previous in seen)
        top_id = next(entry for entry in top_ids if entry not in excluded)
        seen.append(top_id)
        return [
            await service._materialize_outfit(
                {"items": [1, 2, 3]},
                kwargs["user"],
                weather,
                kwargs["occasion"],
                OutfitSource.on_demand,
                {1: top_id, 2: jeans_id, 3: jacket_id},
                scheduled_date=kwargs["scheduled_date"],
            )
        ]

    with (
        patch(
            "app.services.trip_service.TripWeather.for_dates",
            AsyncMock(return_value=[(weather, "forecast")] * 4),
        ),
        patch.object(RecommendationService, "generate_recommendations", generate),
        patch("app.services.trip_service.get_user_today", return_value=date(2026, 10, 23)),
    ):
        response = await client.post(
            "/api/v1/trips",
            headers=auth_headers,
            json={
                "destination": "Paris",
                "start_date": "2027-05-01",
                "end_date": "2027-05-04",
                "occasions": ["casual", "work"],
            },
        )
    assert response.status_code == 201, response.text
    trip = response.json()
    assert [day["date"] for day in trip["days"]] == [f"2027-05-0{index}" for index in range(1, 5)]
    assert len(trip["packing"]) == len({entry["item_id"] for entry in trip["packing"]}) == 6
    assert {entry["item_id"]: entry["days_worn"] for entry in trip["packing"]}[str(jeans_id)] == 4
    assert (await client.get("/api/v1/trips", headers=auth_headers)).json()[0]["id"] == trip["id"]
    calendar = await client.get(
        "/api/v1/outfits?date_from=2027-05-01&date_to=2027-05-04", headers=auth_headers
    )
    assert calendar.status_code == 200, calendar.text
    assert {entry["id"] for entry in calendar.json()["outfits"]} == {
        entry["outfit_id"] for entry in trip["days"]
    }


def request(days=1):
    return TripRequest(
        destination="Paris",
        start_date="2030-10-24",
        end_date=f"2030-10-{23 + days}",
        occasions=["casual"],
    )


async def test_destination_not_found():
    with patch.object(WeatherService, "geocode_location_name", AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="Destination not found"):
            await TripWeather().for_dates("Missing", [date(2030, 10, 24)])


async def test_past_departure(db_session, test_user):
    with patch("app.services.trip_service.get_user_today", return_value=date(2031, 1, 1)):
        with pytest.raises(ValueError, match="Departure"):
            await TripService(db_session).plan(test_user, request())


async def test_list_hides_second_users_trips(client, db_session, test_user, auth_headers):
    from app.models.user import User

    other = User(email=f"{uuid4()}@example.com", display_name="Other")
    db_session.add(other)
    await db_session.flush()
    db_session.add(Trip(user_id=other.id, **request().model_dump(), days=[], packing=[]))
    await db_session.commit()
    assert (await client.get("/api/v1/trips", headers=auth_headers)).json() == []


def test_stored_trip_response_does_not_apply_request_rules():
    stored = request().model_dump() | {
        "id": uuid4(),
        "occasions": ["retired-occasion"],
        "days": [],
        "packing": [],
    }
    assert TripResponse.model_validate(stored).occasions == ["retired-occasion"]


async def test_overlapping_trip_returns_409(client, db_session, test_user, auth_headers):
    db_session.add(Trip(user_id=test_user.id, **request().model_dump(), days=[], packing=[]))
    await db_session.commit()
    with patch("app.services.trip_service.TripWeather.for_dates", AsyncMock()) as weather:
        response = await client.post(
            "/api/v1/trips", headers=auth_headers, json=request().model_dump(mode="json")
        )
    assert response.status_code == 409
    weather.assert_not_called()


async def api_failure(client, auth_headers, error):
    with patch("app.api.trips.TripService.plan", AsyncMock(side_effect=error)):
        return await client.post(
            "/api/v1/trips", headers=auth_headers, json=request().model_dump(mode="json")
        )


async def test_invalid_plan_maps_to_422(client, auth_headers):
    assert (
        await api_failure(client, auth_headers, ValueError("Not enough clothes"))
    ).status_code == 422


async def test_weather_failure_maps_to_503(client, auth_headers):
    response = await api_failure(client, auth_headers, WeatherServiceError("bad provider"))
    assert (response.status_code, response.json()["detail"]) == (503, "Trip weather unavailable")


async def test_ai_failure_has_own_503_message(client, auth_headers):
    from app.services.recommendation_service import AIRecommendationError

    response = await api_failure(client, auth_headers, AIRecommendationError("bad endpoint"))
    assert (response.status_code, response.json()["detail"]) == (503, "Trip planning unavailable")


async def test_trip_rate_limit(client, auth_headers, test_user):
    from fastapi import HTTPException

    with (
        patch(
            "app.api.trips.rate_limit_by_user",
            AsyncMock(side_effect=HTTPException(429, "Too many requests")),
        ) as limiter,
        patch("app.api.trips.TripService.plan", AsyncMock()) as plan,
    ):
        response = await client.post(
            "/api/v1/trips", headers=auth_headers, json=request().model_dump(mode="json")
        )
    assert response.status_code == 429
    limiter.assert_awaited_once_with(str(test_user.id), "trip", max_requests=2, window_seconds=60)
    plan.assert_not_called()


async def real_plan(client, db_session, user, headers, kinds, days):
    from app.models.item import ClothingItem, ItemStatus

    wardrobe = [
        ClothingItem(
            user_id=user.id,
            type=kind,
            name=f"trip-{kind}",
            image_path="trip-test",
            status=ItemStatus.ready,
            wash_interval=2 if kind == "hoodie" else None,
        )
        for kind in kinds
    ]
    db_session.add_all(wardrobe)
    await db_session.commit()
    prompts = []

    async def ai_reply(prompt, **kwargs):
        prompts.append(prompt)
        numbers = [
            int(line[1 : line.index("]")])
            for line in prompt.splitlines()
            if line.startswith("[")
            and any(f'"trip-{kind}"' in line for kind in ("hoodie", "jeans"))
        ]
        return SimpleNamespace(
            content=json.dumps({"items": numbers}), model="fixture", endpoint="fixture"
        )

    weather = TripWeather.climate_normal(date(2030, 10, 24), capture()["climate"]["daily"])
    with (
        patch(
            "app.services.trip_service.TripWeather.for_dates",
            AsyncMock(return_value=[(weather, "forecast")] * days),
        ),
        patch(
            "app.services.recommendation_service.AIService.generate_text",
            AsyncMock(side_effect=ai_reply),
        ),
    ):
        response = await client.post(
            "/api/v1/trips", headers=headers, json=request(days).model_dump(mode="json")
        )
    return response, prompts


async def test_real_materialize_cannot_reuse_tee_under_hoodie(
    client, db_session, test_user, auth_headers
):
    response, prompts = await real_plan(
        client, db_session, test_user, auth_headers, ["t-shirt", "hoodie", "jeans"], 2
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Not enough clothes for a complete trip"
    assert (
        len(prompts) == 2 and '"trip-t-shirt"' in prompts[0] and '"trip-t-shirt"' not in prompts[1]
    )
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Outfit).where(Outfit.user_id == test_user.id)
        )
    ) == 0


async def test_not_enough_clothes(client, db_session, test_user, auth_headers):
    response, _ = await real_plan(
        client, db_session, test_user, auth_headers, ["hoodie", "jeans"], 1
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Not enough clothes for a complete trip"


async def test_saved_outfit_reuse_breach(db_session, test_user):
    wardrobe = [SimpleNamespace(item_id=uuid4(), item=item(kind)) for kind in ("shirt", "jeans")]
    outfit = SimpleNamespace(id=uuid4(), items=wardrobe)
    weather = SimpleNamespace(window_min=10, window_max=18)
    with (
        patch(
            "app.services.trip_service.TripWeather.for_dates",
            AsyncMock(return_value=[(weather, "forecast")] * 2),
        ),
        patch(
            "app.services.trip_service.RecommendationService.generate_recommendations",
            AsyncMock(return_value=[outfit]),
        ),
    ):
        with pytest.raises(ValueError, match="reuse limits"):
            await TripService(db_session).plan(test_user, request(2))


async def test_invalid_json_weather_maps_to_503(client, auth_headers):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"not json"))
    provider = httpx.AsyncClient(transport=transport)
    with (
        patch("app.services.trip_service.httpx.AsyncClient", return_value=provider),
        patch.object(
            WeatherService, "geocode_location_name", AsyncMock(return_value=(48.8, 2.3, "Paris"))
        ),
    ):
        response = await client.post(
            "/api/v1/trips", headers=auth_headers, json=request().model_dump(mode="json")
        )
    assert response.status_code == 503
