import asyncio
from contextvars import ContextVar
from datetime import timedelta
from itertools import permutations
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select, text

from app.database import get_db
from app.main import app
from app.models.item import ClothingItem, ItemHistory
from app.models.outfit import Outfit, UserFeedback
from app.services.item_service import ItemService
from app.services.wear_service import AlreadyThis, WearService
from app.utils.auth import get_current_user
from app.utils.timezone import get_user_today
from tests.test_outfit_wear_history import _items
from tests.test_wear_service import source

WRITERS = ("wore", "ha", "wear-today", "wore-instead", "studio", "feedback")
HANG_CEILING = 15


@pytest_asyncio.fixture
async def writer_cases(db_session, test_user):
    ids = [UUID(value) for value in await _items(db_session, test_user.id)]
    today = get_user_today(test_user)
    outfits = {}
    for writer in WRITERS:
        outfits[writer] = await source(
            db_session,
            test_user,
            ids[:2],
            day=None if writer == "wear-today" else today,
            kind="manual"
            if writer == "wear-today"
            else "scheduled"
            if writer == "ha"
            else "on_demand",
        )
    await db_session.commit()
    return ids, today, outfits


@pytest_asyncio.fixture
async def writer_client(session_maker, async_engine, test_user, writer_cases):
    active = ContextVar("wear_sql", default=None)
    records = []

    def trace(connection, cursor, statement, parameters, context, many):
        record = active.get()
        if record is not None:
            record["sql"].append(statement)

    async def database():
        async with session_maker() as session:
            connection = await session.connection()
            raw = await connection.get_raw_connection()
            pid = raw.driver_connection.get_server_pid()
            record = {"pid": pid, "sql": []}
            session.info["writer_index"] = len(records)
            records.append(record)
            token = active.set(record)
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise
            finally:
                active.reset(token)

    async def authenticated():
        return test_user

    previous = app.dependency_overrides.copy()
    app.dependency_overrides.update({get_db: database, get_current_user: authenticated})
    event.listen(async_engine.sync_engine, "before_cursor_execute", trace)
    ids, today, outfits = writer_cases
    try:
        with (
            patch("app.api.ha.settings.ha_user_email", test_user.email),
            patch("app.api.ha.settings.wardrowbe_ha_token", "r2-test"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
            ) as client:

                async def send(writer):
                    path = f"/api/v1/outfits/{outfits[writer].id}/{writer}"
                    body = {}
                    if writer == "wore":
                        body = {"date": str(today)}
                    elif writer == "wore-instead":
                        body = {"items": [str(item_id) for item_id in ids[:2]]}
                    elif writer == "studio":
                        path = "/api/v1/outfits/studio"
                        body = {
                            "items": [str(item_id) for item_id in ids[:2]],
                            "occasion": "casual",
                            "mark_worn": True,
                        }
                    elif writer == "feedback":
                        body = {"worn": True}
                    elif writer == "ha":
                        path = "/api/v1/ha/wore-today"
                    return await client.post(path, json=body, headers={"x-ha-token": "r2-test"})

                yield send, records
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", trace)
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.mark.parametrize("writer", WRITERS)
async def test_writer_first_sql_is_advisory_lock(writer_client, writer):
    send, records = writer_client
    response = await send(writer)
    assert response.status_code in (200, 201), response.text
    assert "pg_advisory_xact_lock" in records[0]["sql"][0], records[0]["sql"]


@pytest.mark.parametrize("writer", WRITERS)
async def test_all_writers_map_archived_items(writer_client, db_session, writer_cases, writer):
    ids, today, outfits = writer_cases
    item = await db_session.get(ClothingItem, ids[0])
    item.is_archived = True
    await db_session.commit()
    send, records = writer_client
    response = await send(writer)
    assert response.status_code == 403, response.text
    detail = response.json()["error"] if writer == "ha" else response.json()["detail"]["error_code"]
    assert detail == "OUTFIT_ITEM_OWNERSHIP"


async def test_feedback_swap_uses_suggestion_date(client, db_session, test_user, auth_headers):
    ids = [UUID(value) for value in await _items(db_session, test_user.id)]
    yesterday = get_user_today(test_user) - timedelta(days=1)
    original = await source(db_session, test_user, ids[:2], day=yesterday)
    await db_session.commit()
    response = await client.post(
        f"/api/v1/outfits/{original.id}/feedback",
        headers=auth_headers,
        json={"actually_worn": False, "wore_instead_items": [str(ids[2])]},
    )
    assert response.status_code == 200
    assert response.json()["worn_at"] == str(yesterday)
    assert (
        await db_session.scalar(
            select(ItemHistory.worn_at).where(
                ItemHistory.outfit_id == UUID(response.json()["outfit_id"])
            )
        )
        == yesterday
    )


@pytest.mark.parametrize("posted", ["wear", "root", "multihop"])
async def test_retry_never_records_wears(db_session, test_user, posted):
    ids = [UUID(value) for value in await _items(db_session, test_user.id)]
    today = get_user_today(test_user)
    root = await source(db_session, test_user, ids[:2], kind="manual")
    wear = await source(db_session, test_user, ids[:2], day=today)
    wear.cloned_from_outfit_id = root.id
    if posted == "multihop":
        middle = await source(db_session, test_user, ids[:2], day=today)
        middle.cloned_from_outfit_id = root.id
        wear.cloned_from_outfit_id = middle.id
    wear.feedback = UserFeedback(outfit_id=wear.id, worn_at=today, actually_worn=True)
    await db_session.commit()
    with patch.object(ItemService, "record_wears", new_callable=AsyncMock) as record:
        result = await WearService(db_session).log_worn(
            test_user, root if posted == "root" else wear, today, ids[:2], caller="test"
        )
    assert isinstance(result, AlreadyThis) and result.outfit.id == wear.id
    record.assert_not_awaited()


@pytest.mark.parametrize("first,second", list(permutations(WRITERS, 2)))
async def test_overlapping_writer_pairs(
    writer_client, writer_cases, session_maker, db_session, test_user, first, second
):
    send, records = writer_client
    held, release = asyncio.Event(), asyncio.Event()
    lock = WearService.lock_day
    tasks = []

    async def gate(self, *args, **kwargs):
        await lock(self, *args, **kwargs)
        if self.db.info.get("writer_index") == 0 and not held.is_set():
            held.set()
            await release.wait()

    try:
        with patch.object(WearService, "lock_day", gate):
            async with asyncio.timeout(HANG_CEILING):
                tasks.append(asyncio.create_task(send(first)))
                while not held.is_set():
                    if tasks[0].done():
                        pytest.fail(
                            f"First writer finished before its lock gate: {(await tasks[0]).status_code}"
                        )
                    await asyncio.sleep(0)
                tasks.append(asyncio.create_task(send(second)))
                async with session_maker() as observer:
                    while len(records) < 2 or not await observer.scalar(
                        text("SELECT CAST(:owner AS integer) = ANY(pg_blocking_pids(:rival))"),
                        {"owner": records[0]["pid"], "rival": records[-1]["pid"]},
                    ):
                        if tasks[1].done():
                            pytest.fail(
                                f"Rival finished without overlapping the owner: {(await tasks[1]).status_code}"
                            )
                        await asyncio.sleep(0)
                release.set()
                owner, rival = await asyncio.gather(*tasks)
        assert owner.status_code == (201 if first == "studio" else 200), owner.text
        assert rival.status_code == (200 if second == "ha" else 409), rival.text
        if second == "ha":
            assert rival.json()["already_logged"] is True
        else:
            assert rival.json()["detail"]["error_code"] == "DAY_LOGGED"
        assert all("pg_advisory_xact_lock" in record["sql"][0] for record in records)
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(Outfit)
                .where(
                    Outfit.user_id == test_user.id,
                    Outfit.feedback.has(UserFeedback.worn_at == writer_cases[1]),
                )
            )
            == 1
        )
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(ItemHistory)
                .join(ClothingItem)
                .where(ClothingItem.user_id == test_user.id)
            )
            == 2
        )
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_pending_filter_strips_spaces(client, db_session, test_user, auth_headers):
    ids = [UUID(value) for value in await _items(db_session, test_user.id)]
    await source(db_session, test_user, ids[:2], inspiration=True)
    await db_session.commit()
    response = await client.get(
        "/api/v1/outfits", headers=auth_headers, params={"status": " pending "}
    )
    assert response.status_code == 200 and response.json()["total"] == 0
