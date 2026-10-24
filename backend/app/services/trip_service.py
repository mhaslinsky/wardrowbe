from collections import Counter
from datetime import date, datetime, timedelta
from hashlib import sha256
from math import isfinite
from statistics import mean

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.item import ClothingItem
from app.models.outfit import TimeOfDay
from app.models.trip import Trip
from app.services.ai_service import require_internal_ai
from app.services.recommendation_service import RecommendationService
from app.services.weather_service import WeatherData, WeatherService, WeatherServiceError
from app.utils.clothing import BODY_SLOTS, OVER_LAYER_SUBTYPES, covered_body_slots
from app.utils.garment_vocabulary import (
    BASE_UNDER_LAYER_TYPES,
    DEFAULT_WASH_INTERVALS,
    ITEM_ROLE,
    NEEDS_BASE_TOP_TYPES,
)
from app.utils.timezone import get_user_today


def reuse_limit(item) -> float:
    kind = (item.type or "").lower()
    role = ITEM_ROLE.get(kind)
    single_wear = role == "full_body" or (role == "base_top" and kind not in NEEDS_BASE_TOP_TYPES)
    if item.wash_interval == 999:
        return 1 if single_wear else float("inf")
    remaining = max(
        0,
        (
            item.wash_interval
            if item.wash_interval is not None
            else DEFAULT_WASH_INTERVALS.get(kind, 3)
        )
        - (item.wears_since_wash or 0),
    )
    return min(1, remaining) if single_wear else remaining


def packing_list(outfits) -> list[dict]:
    packing = {}
    for outfit in outfits:
        for entry in outfit.items:
            key = str(entry.item_id)
            if key not in packing:
                packing[key] = {"item_id": key, "name": entry.item.name, "count": 1, "days_worn": 0}
            packing[key]["days_worn"] += 1
    return list(packing.values())


class TripWeather:
    def __init__(self):
        self.weather = WeatherService()

    async def for_dates(self, destination: str, dates: list[date]) -> list[tuple[WeatherData, str]]:
        location = await self.weather.geocode_location_name(destination)
        if location is None:
            raise ValueError("Destination not found")
        try:
            return await self._for_location(location, dates)
        except (ValueError, KeyError, TypeError, IndexError, OverflowError) as error:
            raise WeatherServiceError("Invalid trip weather response") from error

    async def _for_location(self, location, dates):
        latitude, longitude, _ = location
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            response = await client.get(
                f"{self.weather.base_url}/forecast",
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "timezone": "auto",
                    "forecast_days": 16,
                    "hourly": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation_probability,wind_speed_10m,weather_code",
                },
            )
            response.raise_for_status()
            hourly = response.json()["hourly"]
            fields = (
                "temperature_2m",
                "apparent_temperature",
                "relative_humidity_2m",
                "precipitation_probability",
                "wind_speed_10m",
                "weather_code",
            )
            series = [hourly[field] for field in fields]
            if not isinstance(hourly["time"], list):
                raise ValueError("Invalid forecast times")
            for slot in hourly["time"]:
                datetime.fromisoformat(slot)
            normals = None
            results = []
            for day in dates:
                indices = [
                    index
                    for index, slot in enumerate(hourly["time"])
                    if slot[:10] == day.isoformat()
                    and 10 <= datetime.fromisoformat(slot).hour <= 18
                ]
                if (
                    len(indices) == 9
                    and all(
                        isinstance(values, list)
                        and index < len(values)
                        and isinstance(values[index], (int, float))
                        and not isinstance(values[index], bool)
                        and isfinite(values[index])
                        for values in series
                        for index in indices
                    )
                    and all(
                        0 <= hourly[field][index] <= 100
                        for field in ("relative_humidity_2m", "precipitation_probability")
                        for index in indices
                    )
                    and all(
                        hourly["wind_speed_10m"][index] >= 0
                        and hourly["weather_code"][index] in range(100)
                        for index in indices
                    )
                ):
                    temps = [hourly["temperature_2m"][index] for index in indices]
                    code = max(hourly["weather_code"][index] for index in indices)
                    data = WeatherData(
                        temperature=mean(temps),
                        feels_like=mean(hourly["apparent_temperature"][index] for index in indices),
                        humidity=round(
                            mean(hourly["relative_humidity_2m"][index] for index in indices)
                        ),
                        precipitation_chance=max(
                            hourly["precipitation_probability"][index] for index in indices
                        ),
                        precipitation_mm=0,
                        wind_speed=max(hourly["wind_speed_10m"][index] for index in indices),
                        condition=self.weather._interpret_weather_code(code),
                        condition_code=code,
                        is_day=True,
                        uv_index=0,
                        timestamp=datetime.combine(day, datetime.min.time()),
                        window_min=min(temps),
                        window_max=max(temps),
                    )
                    results.append((data, "forecast"))
                else:
                    if normals is None:
                        response = await client.get(
                            "https://archive-api.open-meteo.com/v1/archive",
                            params={
                                "latitude": latitude,
                                "longitude": longitude,
                                "timezone": "auto",
                                "start_date": "1991-01-01",
                                "end_date": "2020-12-31",
                                "daily": "temperature_2m_min,temperature_2m_max",
                                "models": "era5",
                            },
                        )
                        response.raise_for_status()
                        normals = response.json()["daily"]
                    results.append((self.climate_normal(day, normals), "climate"))
        return results

    @staticmethod
    def climate_normal(day: date, daily: dict) -> WeatherData:
        if not isinstance(daily["time"], list) or any(
            not isinstance(daily[field], list) or len(daily[field]) != len(daily["time"])
            for field in ("temperature_2m_min", "temperature_2m_max")
        ):
            raise WeatherServiceError("Invalid climate response")
        for slot in daily["time"]:
            date.fromisoformat(slot)
        indices = [
            index
            for index, slot in enumerate(daily["time"])
            if slot[5:] == day.strftime("%m-%d")
            and all(
                isinstance(daily[field][index], (int, float)) and isfinite(daily[field][index])
                for field in ("temperature_2m_min", "temperature_2m_max")
            )
        ]
        if not indices:
            raise WeatherServiceError("Climate normals unavailable")
        low = mean(daily["temperature_2m_min"][index] for index in indices)
        high = mean(daily["temperature_2m_max"][index] for index in indices)
        return WeatherData(
            temperature=(low + high) / 2,
            feels_like=(low + high) / 2,
            humidity=50,
            precipitation_chance=0,
            precipitation_mm=0,
            wind_speed=0,
            condition="climate normal",
            condition_code=0,
            is_day=True,
            uv_index=0,
            timestamp=datetime.combine(day, datetime.min.time()),
            window_min=low,
            window_max=high,
        )


class TripOverlapError(ValueError):
    pass


class TripService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _check_overlap(self, session, user_id, request):
        if await session.scalar(
            select(Trip.id)
            .where(
                Trip.user_id == user_id,
                Trip.start_date <= request.end_date,
                Trip.end_date >= request.start_date,
            )
            .limit(1)
        ):
            raise TripOverlapError("Trip dates overlap")

    async def plan(self, user, request) -> Trip:
        require_internal_ai("text")
        if request.start_date < get_user_today(user):
            raise ValueError("Departure must be today or later")
        await self._check_overlap(self.db, user.id, request)
        dates = [
            request.start_date + timedelta(days=offset)
            for offset in range((request.end_date - request.start_date).days + 1)
        ]
        weather = await TripWeather().for_dates(request.destination, dates)
        connection = await self.db.connection()
        try:
            async with AsyncSession(
                bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
            ) as planning:
                service = RecommendationService(planning)
                wardrobe = list(
                    (
                        await planning.execute(
                            select(ClothingItem).where(ClothingItem.user_id == user.id)
                        )
                    ).scalars()
                )
                uses = Counter()
                outfits, days = [], []
                for index, day in enumerate(dates):
                    excluded = [
                        item.id
                        for item in wardrobe
                        if item.needs_wash or uses[item.id] >= reuse_limit(item)
                    ]
                    generated = await service.generate_recommendations(
                        user=user,
                        occasion=request.occasions[index % len(request.occasions)],
                        weather_override=weather[index][0],
                        exclude_items=excluded,
                        time_of_day=TimeOfDay.full_day,
                        single_outfit=True,
                        count=1,
                        scheduled_date=day,
                    )
                    outfit = generated[0]
                    types = {
                        entry.item_id: (entry.item.type or "").lower() for entry in outfit.items
                    }
                    if not BODY_SLOTS <= covered_body_slots(list(types), types):
                        raise ValueError("Not enough clothes for a complete trip")
                    if any(
                        entry.item_id in excluded or uses[entry.item_id] >= reuse_limit(entry.item)
                        for entry in outfit.items
                    ):
                        raise ValueError("Outfit exceeds trip reuse limits")
                    if any(kind in NEEDS_BASE_TOP_TYPES for kind in types.values()) and not any(
                        (
                            (entry.item.type or "").lower() in BASE_UNDER_LAYER_TYPES
                            and (entry.item.subtype or "").lower() not in OVER_LAYER_SUBTYPES
                        )
                        or ITEM_ROLE.get((entry.item.type or "").lower()) == "full_body"
                        for entry in outfit.items
                    ):
                        raise ValueError("Not enough clothes for a complete trip")
                    uses.update(types.keys())
                    outfits.append(outfit)
                    days.append(
                        {
                            "date": day.isoformat(),
                            "outfit_id": str(outfit.id),
                            "weather_source": weather[index][1],
                            "low": weather[index][0].window_min,
                            "high": weather[index][0].window_max,
                        }
                    )
                trip = Trip(
                    user_id=user.id,
                    destination=request.destination,
                    start_date=request.start_date,
                    end_date=request.end_date,
                    occasions=request.occasions,
                    days=days,
                    packing=packing_list(outfits),
                )
                lock_key = int.from_bytes(
                    sha256(b"trip:" + user.id.bytes).digest()[:8], signed=True
                )
                await planning.execute(select(func.pg_advisory_xact_lock(lock_key)))
                await self._check_overlap(planning, user.id, request)
                planning.add(trip)
                await planning.commit()
            await self.db.commit()
            return trip
        except Exception:
            await self.db.rollback()
            raise
