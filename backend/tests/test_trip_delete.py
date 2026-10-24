from datetime import date
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.outfit import Outfit, UserFeedback
from app.models.trip import Trip
from app.models.user import User
from app.services.trip_service import TripService


async def seed_trip(db, user_id, count=4):
    outfits = [Outfit(user_id=user_id, occasion="casual") for _ in range(count)]
    db.add_all(outfits)
    await db.flush()
    trip = Trip(
        user_id=user_id,
        destination="Paris",
        start_date=date(2030, 10, 24),
        end_date=date(2030, 10, 27),
        occasions=["casual"],
        packing=[],
        days=[{"outfit_id": str(outfit.id)} for outfit in outfits],
    )
    db.add(trip)
    await db.commit()
    return trip.id, [outfit.id for outfit in outfits]


@pytest.mark.parametrize("count", [0, 4])
async def test_owner_delete(client, db_session, test_user, auth_headers, count):
    trip_id, outfit_ids = await seed_trip(db_session, test_user.id, count)
    response = await client.delete(f"/api/v1/trips/{trip_id}", headers=auth_headers)
    assert response.status_code == 204, response.text
    assert response.content == b""
    assert await db_session.scalar(select(Trip.id).where(Trip.id == trip_id)) is None
    assert list(await db_session.scalars(select(Outfit.id).where(Outfit.id.in_(outfit_ids)))) == []
    assert (
        await client.delete(f"/api/v1/trips/{trip_id}", headers=auth_headers)
    ).status_code == 404


async def test_other_owner_hidden(client, db_session, test_user, auth_headers):
    other = User(email=f"{uuid4()}@example.com", display_name="Other")
    db_session.add(other)
    await db_session.flush()
    trip_id, outfit_ids = await seed_trip(db_session, other.id)
    assert (await client.get("/api/v1/trips", headers=auth_headers)).json() == []
    assert (
        await client.delete(f"/api/v1/trips/{trip_id}", headers=auth_headers)
    ).status_code == 404
    assert await db_session.scalar(select(Trip.id).where(Trip.id == trip_id)) == trip_id
    assert set(await db_session.scalars(select(Outfit.id).where(Outfit.id.in_(outfit_ids)))) == set(
        outfit_ids
    )


@pytest.mark.parametrize("feedback", [{"worn_at": date(2030, 10, 24)}, {"actually_worn": True}])
async def test_worn_history_kept(client, db_session, test_user, auth_headers, feedback):
    trip_id, outfit_ids = await seed_trip(db_session, test_user.id, 1)
    db_session.add(UserFeedback(outfit_id=outfit_ids[0], **feedback))
    await db_session.commit()
    assert (
        await client.delete(f"/api/v1/trips/{trip_id}", headers=auth_headers)
    ).status_code == 204
    assert await db_session.scalar(select(Trip.id).where(Trip.id == trip_id)) is None
    assert (
        await db_session.scalar(select(Outfit.id).where(Outfit.id == outfit_ids[0]))
        == outfit_ids[0]
    )
    assert (
        await db_session.scalar(
            select(UserFeedback.outfit_id).where(UserFeedback.outfit_id == outfit_ids[0])
        )
        == outfit_ids[0]
    )


async def test_partial_failure_rolls_back(db_session, test_user):
    trip_id, outfit_ids = await seed_trip(db_session, test_user.id)
    user_id = test_user.id
    original_delete = db_session.delete
    calls = 0

    async def fail_later(instance):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("forced delete failure")
        await original_delete(instance)
        await db_session.flush()

    with patch.object(db_session, "delete", side_effect=fail_later):
        with pytest.raises(RuntimeError, match="forced delete failure"):
            await TripService(db_session).delete(user_id, trip_id)
    assert await db_session.scalar(select(Trip.id).where(Trip.id == trip_id)) == trip_id
    assert set(await db_session.scalars(select(Outfit.id).where(Outfit.id.in_(outfit_ids)))) == set(
        outfit_ids
    )


async def test_malformed_id(client, auth_headers):
    assert (await client.delete("/api/v1/trips/broken", headers=auth_headers)).status_code == 422


async def test_missing_and_foreign_links(client, db_session, test_user, auth_headers):
    other = User(email=f"{uuid4()}@example.com", display_name="Other")
    db_session.add(other)
    await db_session.flush()
    foreign_trip, foreign_ids = await seed_trip(db_session, other.id, 1)
    trip_id, _ = await seed_trip(db_session, test_user.id, 0)
    trip = await db_session.get(Trip, trip_id)
    trip.days = [{"outfit_id": str(uuid4())}, {"outfit_id": str(foreign_ids[0])}]
    await db_session.commit()
    assert (
        await client.delete(f"/api/v1/trips/{trip_id}", headers=auth_headers)
    ).status_code == 204
    assert await db_session.scalar(select(Trip.id).where(Trip.id == foreign_trip)) == foreign_trip
    assert (
        await db_session.scalar(select(Outfit.id).where(Outfit.id == foreign_ids[0]))
        == foreign_ids[0]
    )


async def test_commit_failure_keeps_images_and_rows(db_session, test_user):
    trip_id, outfit_ids = await seed_trip(db_session, test_user.id, 1)
    user_id = test_user.id
    with (
        patch.object(db_session, "commit", side_effect=RuntimeError("forced commit failure")),
        patch("app.services.trip_service.InspirationService.cleanup_image") as cleanup,
    ):
        with pytest.raises(RuntimeError, match="forced commit failure"):
            await TripService(db_session).delete(user_id, trip_id)
    cleanup.assert_not_called()
    assert await db_session.scalar(select(Trip.id).where(Trip.id == trip_id)) == trip_id
    assert (
        await db_session.scalar(select(Outfit.id).where(Outfit.id == outfit_ids[0]))
        == outfit_ids[0]
    )


async def test_image_cleanup_after_commit(db_session, test_user):
    trip_id, outfit_ids = await seed_trip(db_session, test_user.id, 1)
    with patch("app.services.trip_service.InspirationService.cleanup_image") as cleanup:
        assert await TripService(db_session).delete(test_user.id, trip_id)
    assert cleanup.call_count == 1
    assert cleanup.call_args.args[0].id == outfit_ids[0]
    assert not db_session.in_transaction()
