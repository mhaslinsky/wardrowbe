import asyncio
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import Select, func, select, text

from app.models.item import ItemHistory
from app.models.outfit import Outfit
from app.services.studio_service import StudioService

pytest_plugins = ["tests.test_studio_service"]


async def make_template(db_session, studio_user, wardrobe_items):
    return await StudioService(db_session).create_from_scratch(
        user=studio_user,
        item_ids=[wardrobe_items[0].id],
        occasion="casual",
        name="Backfill",
        scheduled_for=None,
        mark_worn=False,
        source_item_id=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("days_ago", [0, 7])
async def test_wear_date_and_duplicate(db_session, studio_user, wardrobe_items, days_ago):
    template = await make_template(db_session, studio_user, wardrobe_items)
    target = date(2026, 10, 23) - timedelta(days=days_ago)
    with patch("app.services.studio_service.get_user_today", return_value=date(2026, 10, 23)):
        first = await StudioService(db_session).wear_today(studio_user, template.id, target)
        second = await StudioService(db_session).wear_today(studio_user, template.id, target)
    assert first.id == second.id
    assert first.scheduled_for == target
    assert first.feedback.worn_at == target
    assert first.status.value == "accepted"
    histories = list(
        (
            await db_session.execute(select(ItemHistory).where(ItemHistory.outfit_id == first.id))
        ).scalars()
    )
    assert len(histories) == 1
    assert histories[0].worn_at == target
    await db_session.refresh(wardrobe_items[0])
    assert wardrobe_items[0].wear_count == 1


@pytest.mark.asyncio
async def test_future_wear_rejected(db_session, studio_user, wardrobe_items):
    template = await make_template(db_session, studio_user, wardrobe_items)
    with pytest.raises(ValueError, match="future"):
        await StudioService(db_session).wear_today(studio_user, template.id, date(2999, 1, 1))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "offset,expected_count,expected_dirty", [(-1, 0, False), (0, 1, True), (1, 1, True)]
)
async def test_wash_boundary(
    db_session, studio_user, wardrobe_items, offset, expected_count, expected_dirty
):
    item = wardrobe_items[0]
    item.last_washed_at = date(2026, 10, 20)
    item.wash_interval = 1
    template = await make_template(db_session, studio_user, wardrobe_items)
    with patch("app.services.studio_service.get_user_today", return_value=date(2026, 10, 23)):
        wear = await StudioService(db_session).wear_today(
            studio_user, template.id, date(2026, 10, 20) + timedelta(days=offset)
        )
    await db_session.refresh(item)
    assert item.wear_count == 1
    assert item.wears_since_wash == expected_count
    assert item.needs_wash is expected_dirty
    assert item.last_worn_at == wear.scheduled_for


@pytest.mark.asyncio
async def test_api_duplicate_and_dated_lists(client, db_session, test_user, auth_headers):
    from tests.test_outfit_wear_history import _items

    item_ids = await _items(db_session, test_user.id)
    created = await client.post(
        "/api/v1/outfits/studio",
        json={"items": item_ids, "occasion": "casual"},
        headers=auth_headers,
    )
    assert created.status_code == 201
    template_id = created.json()["id"]
    endpoint = f"/api/v1/outfits/{template_id}/wear-today"
    with patch("app.services.studio_service.get_user_today", return_value=date(2026, 10, 23)):
        first = await client.post(
            endpoint, json={"scheduled_for": "2026-10-01"}, headers=auth_headers
        )
        second = await client.post(
            endpoint, json={"scheduled_for": "2026-10-01"}, headers=auth_headers
        )
    assert first.status_code == second.status_code == 200
    assert first.json()["already_logged"] is False
    assert second.json()["already_logged"] is True
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["feedback"]["worn_at"] == "2026-10-01"
    for filters in [
        {"date_from": "2026-10-01", "date_to": "2026-10-01"},
        {"status_filter": "accepted"},
    ]:
        listed = await client.get("/api/v1/outfits", params=filters, headers=auth_headers)
        assert listed.status_code == 200
        assert first.json()["id"] in {outfit["id"] for outfit in listed.json()["outfits"]}
    for payload, expected in [
        ({"scheduled_for": "2999-01-01"}, 400),
        ({"scheduled_for": "not-a-date"}, 422),
    ]:
        rejected = await client.post(endpoint, json=payload, headers=auth_headers)
        assert rejected.status_code == expected
    with patch("app.services.studio_service.get_user_today", return_value=date(2026, 10, 23)):
        defaulted = await client.post(endpoint, json={}, headers=auth_headers)
    assert defaulted.status_code == 200
    assert defaulted.json()["scheduled_for"] == "2026-10-23"


@pytest.mark.asyncio
async def test_concurrent_wear_on_creates_one_row(
    db_session, session_maker, studio_user, wardrobe_items
):
    template = await make_template(db_session, studio_user, wardrobe_items)
    template_id = template.id
    await db_session.commit()
    release = asyncio.Event()
    first_checked = asyncio.Event()
    second_checked = asyncio.Event()

    def pause_after_duplicate_check(session, checked):
        execute = session.execute

        async def execute_and_pause(statement, *args, **kwargs):
            result = await execute(statement, *args, **kwargs)
            if isinstance(statement, Select):
                columns = statement.column_descriptions
                if (
                    len(columns) == 1
                    and columns[0]["entity"] is Outfit
                    and columns[0]["name"] == "id"
                ):
                    checked.set()
                    await release.wait()
            return result

        session.execute = execute_and_pause

    async def log_and_commit(session, checked):
        try:
            wear, already_logged = await StudioService(session).wear_on(
                studio_user, template_id, date(2026, 10, 1)
            )
            await session.commit()
            return wear.id, already_logged
        finally:
            checked.set()

    async with session_maker() as first, session_maker() as second, session_maker() as observer:
        second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
        pause_after_duplicate_check(first, first_checked)
        pause_after_duplicate_check(second, second_checked)
        first_call = asyncio.create_task(log_and_commit(first, first_checked))
        second_call = None
        try:
            await first_checked.wait()
            if first_call.done():
                first_call.result()
                pytest.fail("First call finished without pausing at the duplicate check")
            second_call = asyncio.create_task(log_and_commit(second, second_checked))
            # Release after Postgres reports a lock wait or the second duplicate check finishes.
            while not second_checked.is_set():
                if second_call.done():
                    second_call.result()
                waiting = await observer.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = :pid AND NOT granted)"),
                    {"pid": second_pid},
                )
                if waiting:
                    break
            release.set()
            results = await asyncio.gather(first_call, second_call)
        finally:
            release.set()
            for call in [first_call, second_call]:
                if call is not None and not call.done():
                    call.cancel()
            await asyncio.gather(
                *[call for call in [first_call, second_call] if call is not None],
                return_exceptions=True,
            )

    count = await db_session.scalar(
        select(func.count())
        .select_from(Outfit)
        .where(
            Outfit.cloned_from_outfit_id == template_id,
            Outfit.scheduled_for == date(2026, 10, 1),
        )
    )
    assert count == 1
    assert results[0][0] == results[1][0]
    assert sorted(already_logged for _wear_id, already_logged in results) == [False, True]
