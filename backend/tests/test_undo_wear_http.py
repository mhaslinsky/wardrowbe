from unittest.mock import AsyncMock, patch

import pytest

from app.main import app
from app.services.wear_service import WearService
from app.utils.auth import get_current_user
from app.utils.timezone import get_user_today
from tests.test_wear_service import source

pytest_plugins = ["tests.test_studio_service"]


@pytest.mark.parametrize("scenario", ["undo", "not_worn", "rating"])
async def test_undo_http(client, db_session, studio_user, wardrobe_items, scenario):
    app.dependency_overrides[get_current_user] = lambda: studio_user
    today = get_user_today(studio_user)
    ids = [item.id for item in wardrobe_items[:2]]
    outfit = await source(db_session, studio_user, ids, day=today)
    if scenario == "undo":
        outfit = (
            await WearService(db_session).log_worn(studio_user, outfit, today, caller="test")
        ).outfit
    await db_session.commit()
    with (
        patch("app.api.outfits.clear_suggestions", new_callable=AsyncMock),
        patch("app.api.outfits._run_learning_safely", new_callable=AsyncMock),
        patch("app.api.outfits.LearningService.recompute_learning_profile", new_callable=AsyncMock),
    ):
        suffix = "feedback" if scenario == "rating" else "undo-wear"
        body = {"rating": 4} if scenario == "rating" else None
        response = await client.post(f"/api/v1/outfits/{outfit.id}/{suffix}", json=body)
    assert response.status_code == (200 if scenario == "undo" else 409), response.text
    if scenario == "undo":
        assert response.json()["status"] == "skipped"
        assert response.json()["learning_stale"] is False
        replacement = await source(db_session, studio_user, ids, day=today)
        await db_session.commit()
        with (
            patch("app.api.outfits.clear_suggestions", new_callable=AsyncMock),
            patch("app.api.outfits._run_learning_safely", new_callable=AsyncMock),
            patch("app.api.outfits.rate_limit_by_user", new_callable=AsyncMock),
        ):
            response = await client.post(
                f"/api/v1/outfits/{replacement.id}/wore", json={"date": str(today)}
            )
        assert response.status_code == 200, response.text
    else:
        assert response.json()["detail"]["error_code"] == "NOT_WORN"
