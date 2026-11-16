"""Shared fakes for tests that run RecommendationService.generate_recommendations."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from app.services.recommendation_service import RecommendationService
from app.services.weather_service import WeatherData


def weather(
    temp=20,
    temp_min=None,
    temp_max=None,
    is_day=True,
    window_min=None,
    window_max=None,
    condition="clear",
    feels_like=None,
    precipitation=0,
) -> WeatherData:
    return WeatherData(
        temperature=temp,
        feels_like=temp if feels_like is None else feels_like,
        humidity=50,
        precipitation_chance=precipitation,
        precipitation_mm=0,
        wind_speed=10,
        condition=condition,
        condition_code=0,
        is_day=is_day,
        uv_index=5,
        timestamp=datetime(2026, 3, 8, 12, 0),
        temp_min=temp_min,
        temp_max=temp_max,
        window_min=window_min,
        window_max=window_max,
    )


def fake_ai(payload: dict) -> MagicMock:
    ai = MagicMock()
    ai.generate_text = AsyncMock(
        return_value=MagicMock(content=json.dumps(payload), model="m", endpoint="e")
    )
    return ai


@contextmanager
def generation_fakes(
    service: RecommendationService,
    payload: dict,
    number_map: dict[int, UUID],
    cached: dict | None = None,
) -> Iterator[tuple[MagicMock, AsyncMock, AsyncMock]]:
    """Fake the AI reply and the suggestion cache, and number the candidates by number_map."""
    ai = fake_ai(payload)
    with (
        patch("app.services.recommendation_service.AIService", return_value=ai),
        patch(
            "app.services.recommendation_service.pop_suggestion",
            AsyncMock(side_effect=[cached, None]),
        ) as pop,
        patch("app.services.recommendation_service.push_suggestions", AsyncMock()) as push,
        patch.object(service, "_format_items_for_prompt", return_value=("", number_map)),
    ):
        yield ai, pop, push
