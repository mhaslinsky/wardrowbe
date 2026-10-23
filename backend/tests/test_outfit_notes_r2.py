from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.outfit import UserFeedback
from tests.test_outfit_notes import save_note


@pytest.mark.asyncio
async def test_api_edit_and_clear_stamp_comment_after_session_expiry(
    client, auth_headers, db_session, test_user
):
    outfit = await save_note(db_session, test_user, "Original note")
    outfit_id = outfit.id
    feedback = (
        await db_session.execute(select(UserFeedback).where(UserFeedback.outfit_id == outfit_id))
    ).scalar_one()
    original_timestamp = feedback.comment_updated_at
    edited_at = datetime.now(UTC) + timedelta(days=1)
    db_session.expire_all()

    with patch("app.models.outfit.datetime") as clock:
        clock.now.return_value = edited_at
        response = await client.post(
            f"/api/v1/outfits/{outfit_id}/feedback",
            headers=auth_headers,
            json={"comment": "Edited from a fresh request"},
        )
    assert response.status_code == 200
    db_session.expire_all()
    feedback = (
        await db_session.execute(select(UserFeedback).where(UserFeedback.outfit_id == outfit_id))
    ).scalar_one()
    assert feedback.comment == "Edited from a fresh request"
    assert feedback.comment_updated_at == edited_at
    assert feedback.comment_updated_at > original_timestamp
    db_session.expire_all()

    response = await client.post(
        f"/api/v1/outfits/{outfit_id}/feedback",
        headers=auth_headers,
        json={"comment": ""},
    )
    assert response.status_code == 200
    db_session.expire_all()
    feedback = (
        await db_session.execute(select(UserFeedback).where(UserFeedback.outfit_id == outfit_id))
    ).scalar_one()
    assert feedback.comment == ""
    assert feedback.comment_updated_at is None
