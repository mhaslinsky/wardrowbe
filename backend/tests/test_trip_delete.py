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
            await TripService(db_session).delete(test_user, trip_id)
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


async def inspiration_files(db_session, test_user):
    from app.services.inspiration_service import InspirationService

    trip_id, outfit_ids = await seed_trip(db_session, test_user.id, 2)
    paths = []
    for outfit_id in outfit_ids:
        outfit = await db_session.get(Outfit, outfit_id)
        outfit.ai_raw_response = {"inspiration": {"name": "Test"}}
        path = InspirationService(db_session).image_path(test_user.id, outfit_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test-image")
        paths.append(path)
    db_session.add(UserFeedback(outfit_id=outfit_ids[1], worn_at=date(2030, 10, 24)))
    await db_session.commit()
    return trip_id, outfit_ids, paths


async def test_commit_failure_keeps_images_and_rows(db_session, test_user):
    trip_id, outfit_ids, paths = await inspiration_files(db_session, test_user)
    try:
        with patch.object(db_session, "commit", side_effect=RuntimeError("forced commit failure")):
            with pytest.raises(RuntimeError, match="forced commit failure"):
                await TripService(db_session).delete(test_user, trip_id)
        assert all(path.exists() for path in paths)
        assert await db_session.scalar(select(Trip.id).where(Trip.id == trip_id)) == trip_id
        assert set(
            await db_session.scalars(select(Outfit.id).where(Outfit.id.in_(outfit_ids)))
        ) == set(outfit_ids)
    finally:
        for path in paths:
            path.unlink(missing_ok=True)


async def test_image_cleanup_after_commit(db_session, test_user):
    trip_id, outfit_ids, paths = await inspiration_files(db_session, test_user)
    try:
        assert await TripService(db_session).delete(test_user, trip_id)
        assert not paths[0].exists()
        assert paths[1].exists()
        assert not db_session.in_transaction()
        assert await db_session.scalar(select(Outfit.id).where(Outfit.id == outfit_ids[0])) is None
        assert (
            await db_session.scalar(select(Outfit.id).where(Outfit.id == outfit_ids[1]))
            == outfit_ids[1]
        )
    finally:
        for path in paths:
            path.unlink(missing_ok=True)


@pytest.mark.parametrize("first", ["wear", "delete"])
@pytest.mark.parametrize("already_worn", [False, True])
async def test_delete_wear_race_both_orders(
    session_maker, db_session, test_user, first, already_worn
):
    import asyncio
    from datetime import timedelta

    from fastapi import HTTPException
    from sqlalchemy import func, text

    from app.api.outfits import FeedbackRequest, submit_feedback
    from app.api.trips import delete_trip
    from app.models.item import ClothingItem, ItemHistory, ItemStatus
    from app.models.outfit import OutfitItem
    from app.services.item_service import ItemService
    from app.services.wear_service import WearService
    from app.utils.timezone import get_user_today

    today = get_user_today(test_user)
    user_id = test_user.id
    trip_id, outfit_ids = await seed_trip(db_session, user_id, 1)
    outfit_id = outfit_ids[0]
    trip = await db_session.get(Trip, trip_id)
    trip.start_date, trip.end_date = today - timedelta(days=3), today
    outfit = await db_session.get(Outfit, outfit_id)
    outfit.scheduled_for = today
    item = ClothingItem(user_id=user_id, type="shirt", image_path="test", status=ItemStatus.ready)
    db_session.add(item)
    await db_session.flush()
    item_id = item.id
    db_session.add(OutfitItem(outfit_id=outfit_id, item_id=item_id, position=0))
    await db_session.commit()
    if already_worn:
        await submit_feedback(outfit_id, FeedbackRequest(worn=True), db_session, test_user)
    await db_session.refresh(item)
    baseline = (item.wear_count, item.wears_since_wash)
    history_before = await db_session.scalar(
        select(func.count()).select_from(ItemHistory).where(ItemHistory.item_id == item_id)
    )
    await db_session.commit()
    acquired, release = asyncio.Event(), asyncio.Event()
    original_begin, original_day = WearService.begin, WearService.lock_day
    original_record = ItemService.record_wears
    pids, locked_days, recorded = {}, [], []
    tasks = []

    async def gate(service, *args, **kwargs):
        await original_begin(service, *args, **kwargs)
        if service.db.info["operation"] == first:
            acquired.set()
            await release.wait()

    async def trace_day(service, owner_id, day):
        await original_day(service, owner_id, day)
        if service.db.info["operation"] == "delete":
            locked_days.append(day)

    async def trace_record(service, *args, **kwargs):
        recorded.append(True)
        return await original_record(service, *args, **kwargs)

    async def run(operation):
        async with session_maker() as session:
            session.info["operation"] = operation
            pids[operation] = await session.scalar(text("SELECT pg_backend_pid()"))
            try:
                if operation == "delete":
                    await delete_trip(trip_id, test_user, session)
                    return 204
                await submit_feedback(outfit_id, FeedbackRequest(worn=True), session, test_user)
                return 200
            except HTTPException as error:
                assert error.status_code == 404
                assert error.detail["error_code"] == "OUTFIT_NOT_FOUND"
                return error.status_code

    second = "delete" if first == "wear" else "wear"
    try:
        with (
            patch.object(WearService, "begin", gate),
            patch.object(WearService, "lock_day", trace_day),
            patch.object(ItemService, "record_wears", trace_record),
        ):
            async with asyncio.timeout(20):
                tasks.append(asyncio.create_task(run(first)))
                await acquired.wait()
                tasks.append(asyncio.create_task(run(second)))
                async with session_maker() as observer:
                    while second not in pids or not await observer.scalar(
                        text("SELECT CAST(:owner AS integer) = ANY(pg_blocking_pids(:rival))"),
                        {"owner": pids[first], "rival": pids.get(second, 0)},
                    ):
                        if tasks[1].done():
                            pytest.fail(f"Rival completed before blocking: {await tasks[1]}")
                        await asyncio.sleep(0)
                release.set()
                results = dict(zip([first, second], await asyncio.gather(*tasks), strict=True))
        assert results["delete"] == 204
        kept = already_worn or first == "wear"
        assert results["wear"] == (200 if kept else 404)
        assert locked_days == [today - timedelta(days=offset) for offset in (3, 2, 1, 0)]
        assert await db_session.scalar(select(Trip.id).where(Trip.id == trip_id)) is None
        feedback = await db_session.scalar(
            select(UserFeedback).where(UserFeedback.outfit_id == outfit_id)
        )
        assert (feedback is not None) == kept
        if kept:
            assert feedback.worn_at == today and feedback.actually_worn
        assert await db_session.scalar(select(Outfit.id).where(Outfit.id == outfit_id)) == (
            outfit_id if kept else None
        )
        await db_session.refresh(item)
        increment = int(not already_worn and first == "wear")
        assert (item.wear_count, item.wears_since_wash) == tuple(
            value + increment for value in baseline
        )
        assert (
            await db_session.scalar(
                select(func.count()).select_from(ItemHistory).where(ItemHistory.item_id == item_id)
            )
            == history_before + increment
        )
        assert len(recorded) == increment
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
