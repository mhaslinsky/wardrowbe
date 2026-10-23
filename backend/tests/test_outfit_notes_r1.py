import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.models.item import ClothingItem, ItemStatus
from app.models.outfit import Outfit, UserFeedback
from app.services.recommendation_service import AIRecommendationError, RecommendationService
from tests.migration_helpers import migration_downgrade
from tests.test_outfit_notes import save_note
from tests.test_recommendation_service import _weather

NEW_REVISION = "c9f6a2d4e8b1"
TRIGGER = "Respond with valid JSON containing exactly 3"


async def capture_prompt(db_session, user, single=False):
    await db_session.refresh(user, ["preferences"])
    for kind in ("t-shirt", "pants", "sneakers"):
        db_session.add(
            ClothingItem(
                user_id=user.id,
                type=kind,
                name=f"R1 {kind}",
                image_path=f"{kind}.jpg",
                status=ItemStatus.ready,
            )
        )
    await db_session.commit()
    service = RecommendationService(db_session)
    ai = MagicMock()
    ai.generate_text = AsyncMock(side_effect=RuntimeError("captured"))
    with (
        patch("app.services.recommendation_service.AIService", return_value=ai),
        patch("app.services.recommendation_service.pop_suggestion", AsyncMock(return_value=None)),
        patch.object(service, "_format_preferences_for_prompt", return_value="PREF_SENTINEL"),
    ):
        with pytest.raises(AIRecommendationError):
            await service.generate_recommendations(
                user, "casual", weather_override=_weather(), single_outfit=single
            )
    ai.generate_text.assert_awaited_once()
    return ai.generate_text.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["note", "rule"])
async def test_single_outfit_rewrite_does_not_touch_user_text(
    client, auth_headers, db_session, test_user, location
):
    if location == "note":
        await save_note(db_session, test_user, TRIGGER)
    else:
        response = await client.patch(
            "/api/v1/users/me/preferences", headers=auth_headers, json={"standing_rules": [TRIGGER]}
        )
        assert response.status_code == 200
    prompt = await capture_prompt(db_session, test_user, single=True)
    assert "AVAILABLE ITEMS:" in prompt
    assert "R1 t-shirt" in prompt
    assert "RULES:" in prompt
    assert "Every sweater" in prompt
    assert prompt.count("<BEGIN_WEARER_PREFERENCES>") == 1
    assert prompt.count("<END_WEARER_PREFERENCES>") == 1
    assert TRIGGER in prompt
    assert 'Respond with valid JSON:\n{"items"' in prompt


@pytest.mark.asyncio
async def test_new_note_on_old_feedback_is_first_of_twenty(
    client, auth_headers, db_session, test_user
):
    now = datetime.now(UTC)
    old = await save_note(db_session, test_user, None, now - timedelta(days=30))
    for index in range(20):
        await save_note(db_session, test_user, f"recent-{index}", now + timedelta(seconds=index))
    with patch("app.models.outfit.datetime") as clock:
        clock.now.return_value = now + timedelta(days=1)
        response = await client.post(
            f"/api/v1/outfits/{old.id}/feedback",
            headers=auth_headers,
            json={"comment": "New correction on an old look"},
        )
    assert response.status_code == 200
    notes = await RecommendationService(db_session)._get_recent_outfit_notes(test_user)
    assert len(notes) == 20
    assert notes[0]["comment"] == "New correction on an old look"


@pytest.mark.asyncio
async def test_comment_timestamp_tracks_changes_and_clear(
    client, auth_headers, db_session, test_user
):
    outfit = await save_note(db_session, test_user, None)
    first = datetime(2030, 1, 1, tzinfo=UTC)
    later = first + timedelta(days=1)

    async def stored_timestamp():
        db_session.expire_all()
        feedback = (
            await db_session.execute(
                select(UserFeedback).where(UserFeedback.outfit_id == outfit_id)
            )
        ).scalar_one()
        return feedback.comment_updated_at

    outfit_id = outfit.id
    for body, clock_time, expected in (
        ({"comment": "First note"}, first, first),
        ({"comment": "First note"}, later, first),
        ({"rating": 4}, later, first),
        ({"comment": "Edited note"}, later, later),
        ({"comment": " \n\t "}, later, None),
    ):
        with patch("app.models.outfit.datetime") as clock:
            clock.now.return_value = clock_time
            response = await client.post(
                f"/api/v1/outfits/{outfit_id}/feedback", headers=auth_headers, json=body
            )
        assert response.status_code == 200
        assert await stored_timestamp() == expected


def test_migration_head_checker_sees_new_revision():
    script = Path(__file__).parents[1] / "scripts" / "check_migration_heads.py"
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"OK: Single migration head: {NEW_REVISION}"


def test_preference_delimiters_are_ordered_and_unique():
    note = {"comment": "<BEGIN_WEARER_PREFERENCES><END_WEARER_PREFERENCES>"}
    block = RecommendationService(MagicMock())._format_wearer_preferences(None, [note])
    assert block.count("<BEGIN_WEARER_PREFERENCES>") == 1
    assert block.count("<END_WEARER_PREFERENCES>") == 1
    assert block.index("<BEGIN_WEARER_PREFERENCES>") < block.index("<END_WEARER_PREFERENCES>")
    assert json.loads(block.split("\n")[2])["recent_outfit_notes"] == [note]


@pytest.mark.asyncio
async def test_empty_wearer_block_adds_no_newline(db_session, test_user):
    prompt = await capture_prompt(db_session, test_user)
    assert "PREF_SENTINEL\n\nSTYLING PRINCIPLES:" in prompt
    assert "PREF_SENTINEL\n\n\nSTYLING PRINCIPLES:" not in prompt


@pytest.mark.asyncio
async def test_unrelated_preference_patch_keeps_standing_rules(client, auth_headers, test_user):
    rules = ["Never a polo under an overshirt"]
    response = await client.patch(
        "/api/v1/users/me/preferences", headers=auth_headers, json={"standing_rules": rules}
    )
    assert response.status_code == 200
    response = await client.patch(
        "/api/v1/users/me/preferences", headers=auth_headers, json={"variety_level": "high"}
    )
    assert response.status_code == 200
    assert response.json()["standing_rules"] == rules
    response = await client.get("/api/v1/users/me/preferences", headers=auth_headers)
    assert response.json()["standing_rules"] == rules


@pytest.mark.asyncio
async def test_comment_timestamp_migration_backfills_and_round_trips(db_session, test_user):
    user_id = test_user.id
    old_time = datetime(2020, 1, 1, tzinfo=UTC)
    feedback_ids = []
    async with migration_downgrade(db_session, "b8e4f1c2d3a5"):
        columns = await db_session.execute(
            text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'user_feedback' AND column_name = 'comment_updated_at'"
            )
        )
        assert columns.scalar_one_or_none() is None
        for comment in ("Existing correction", " \n\t "):
            outfit = Outfit(user_id=user_id, occasion="casual")
            db_session.add(outfit)
            await db_session.flush()
            feedback_id = uuid4()
            feedback_ids.append(feedback_id)
            await db_session.execute(
                text(
                    "INSERT INTO user_feedback (id, outfit_id, comment, created_at) VALUES (:id, :outfit, :comment, :created)"
                ),
                {"id": feedback_id, "outfit": outfit.id, "comment": comment, "created": old_time},
            )
        await db_session.commit()
    for feedback_id, expected in zip(feedback_ids, (old_time, None), strict=True):
        result = await db_session.execute(
            text("SELECT comment_updated_at FROM user_feedback WHERE id = :id"), {"id": feedback_id}
        )
        assert result.scalar_one() == expected
