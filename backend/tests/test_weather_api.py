from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import AsyncClient

from app.api.weather import GEOCODING_FAILURE_DETAIL
from app.services.weather_service import GeocodingServiceError


async def test_current_weather_preserves_live_reading_and_loads_user_hours(
    client, test_user, auth_headers, db_session
):
    from app.models.preference import UserPreference

    test_user.location_lat, test_user.location_lon = 40, -74
    db_session.add(UserPreference(user_id=test_user.id, active_start_hour=10, active_end_hour=17))
    await db_session.commit()
    payload = {
        "current": {"time": "2026-10-29T07:00", "temperature_2m": 6.8, "apparent_temperature": 5.2},
        "hourly": {
            "time": [f"2026-10-29T{hour:02d}:00" for hour in range(24)],
            "temperature_2m": [float(hour) for hour in range(24)],
        },
    }
    provider = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    with (
        patch(
            "app.services.weather_service.WeatherService._cache_get", return_value=None
        ) as cache_read,
        patch("app.services.weather_service.WeatherService._cache_set") as cache_write,
        patch("app.services.weather_service.httpx.AsyncClient", return_value=provider),
    ):
        response = await client.get("/api/v1/weather/current", headers=auth_headers)
    assert response.status_code == 200
    assert (response.json()["temperature"], response.json()["feels_like"]) == (6.8, 5.2)
    cache_read.assert_awaited_once_with(40.0, -74.0, (10, 17))
    weather = cache_write.call_args.args[2]
    assert (weather.window_min, weather.window_max) == (10, 17)


class TestWeatherApi:
    @pytest.mark.asyncio
    async def test_current_weather_uses_location_name_without_persisting_coordinates(
        self, client: AsyncClient, test_user, auth_headers, db_session
    ):
        test_user.location_name = "New York City"
        test_user.location_lat = None
        test_user.location_lon = None
        await db_session.commit()

        geocode_mock = AsyncMock(return_value=(40.7128, -74.0060, "New York City"))
        weather_mock = AsyncMock(
            return_value=type(
                "Weather",
                (),
                {
                    "temperature": 12.5,
                    "feels_like": 7.3,
                    "humidity": 50,
                    "precipitation_chance": 10,
                    "precipitation_mm": 0.0,
                    "wind_speed": 23.4,
                    "condition": "partly cloudy",
                    "condition_code": 2,
                    "is_day": True,
                    "uv_index": 1.8,
                    "timestamp": datetime(2026, 5, 12, 15, 21, 35),
                    "temp_min": 6.0,
                    "temp_max": 14.0,
                },
            )()
        )

        with (
            patch("app.api.weather.WeatherService.geocode_location_name", geocode_mock),
            patch("app.api.weather.WeatherService.get_current_weather", weather_mock),
        ):
            response = await client.get("/api/v1/weather/current", headers=auth_headers)

        assert response.status_code == 200
        geocode_mock.assert_awaited_once_with("New York City")
        weather_mock.assert_awaited_once_with(40.7128, -74.0060, active_hours=(0, 21))

        await db_session.refresh(test_user)
        assert test_user.location_lat is None
        assert test_user.location_lon is None

    @pytest.mark.asyncio
    async def test_forecast_returns_400_when_location_missing(
        self, client: AsyncClient, test_user, auth_headers, db_session
    ):
        test_user.location_name = None
        test_user.location_lat = None
        test_user.location_lon = None
        await db_session.commit()

        response = await client.get("/api/v1/weather/forecast", headers=auth_headers)

        assert response.status_code == 400
        assert response.json()["detail"] == (
            "Location not set. Please provide coordinates or set your location in settings."
        )

    @pytest.mark.asyncio
    async def test_current_weather_returns_503_when_saved_location_geocoding_fails(
        self, client: AsyncClient, test_user, auth_headers, db_session
    ):
        test_user.location_name = "New York City"
        test_user.location_lat = None
        test_user.location_lon = None
        await db_session.commit()

        geocode_mock = AsyncMock(side_effect=GeocodingServiceError("geocoder unavailable"))

        with patch("app.api.weather.WeatherService.geocode_location_name", geocode_mock):
            response = await client.get("/api/v1/weather/current", headers=auth_headers)

        assert response.status_code == 503
        assert response.json()["detail"] == GEOCODING_FAILURE_DETAIL
        geocode_mock.assert_awaited_once_with("New York City")

    @pytest.mark.asyncio
    async def test_forecast_returns_503_when_saved_location_geocoding_fails(
        self, client: AsyncClient, test_user, auth_headers, db_session
    ):
        test_user.location_name = "New York City"
        test_user.location_lat = None
        test_user.location_lon = None
        await db_session.commit()

        geocode_mock = AsyncMock(side_effect=GeocodingServiceError("geocoder unavailable"))

        with patch("app.api.weather.WeatherService.geocode_location_name", geocode_mock):
            response = await client.get("/api/v1/weather/forecast", headers=auth_headers)

        assert response.status_code == 503
        assert response.json()["detail"] == GEOCODING_FAILURE_DETAIL
        geocode_mock.assert_awaited_once_with("New York City")
