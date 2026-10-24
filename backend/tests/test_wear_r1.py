import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.main import app
from app.models.item import ClothingItem, ItemHistory, ItemStatus
from app.models.outfit import OutfitSource, OutfitStatus, UserFeedback
from app.services.learning_service import LearningService
from app.services.wear_service import WearService
from app.utils.timezone import get_user_today
from tests.test_outfit_wear_history import _items
from tests.test_wear_service import source


@pytest_asyncio.fixture
async def wear_case(db_session, test_user):
    ids = [UUID(value) for value in await _items(db_session, test_user.id)]
    today = get_user_today(test_user)
    chosen = await source(db_session, test_user, ids[:2], day=today)
    await db_session.commit()
    return ids, today, chosen


async def test_multihop_existing_retry(client, db_session, test_user, auth_headers, wear_case):
    ids, today, root = wear_case
    middle = await source(db_session, test_user, ids[:2], day=today)
    child = await source(db_session, test_user, ids[:2], day=today)
    middle.replaces_outfit_id = root.id
    child.replaces_outfit_id = middle.id
    child.feedback = UserFeedback(outfit_id=child.id, worn_at=today, actually_worn=True)
    await db_session.commit()
    response = await client.post(
        f"/api/v1/outfits/{child.id}/wore", headers=auth_headers, json={"date": str(today)}
    )
    assert response.status_code == 200
    assert response.json()["already_logged"] is True
    assert response.json()["id"] == str(child.id)


async def test_archived_worn_rating(client, db_session, auth_headers, wear_case):
    ids, today, chosen = wear_case
    endpoint = f"/api/v1/outfits/{chosen.id}"
    assert (
        await client.post(endpoint + "/wore", headers=auth_headers, json={"date": str(today)})
    ).status_code == 200
    item = await db_session.get(ClothingItem, ids[0])
    item.is_archived = True
    await db_session.commit()
    response = await client.post(
        endpoint + "/feedback",
        headers=auth_headers,
        json={"worn": True, "actually_worn": True, "rating": 4},
    )
    assert response.status_code == 200
    assert response.json()["rating"] == 4
    assert (
        await db_session.scalar(
            select(func.count()).select_from(ItemHistory).where(ItemHistory.outfit_id == chosen.id)
        )
        == 2
    )


@pytest.mark.parametrize(
    "state,logged", [("rejected", True), ("expired", True), ("rejected", False), ("expired", False)]
)
async def test_ha_eligibility_and_envelope(client, db_session, test_user, wear_case, state, logged):
    ids, today, newest = wear_case
    newest.source = OutfitSource.scheduled
    newest.status = OutfitStatus(state)
    if logged:
        older = await source(db_session, test_user, ids[:2], day=today)
        older.created_at = datetime(2020, 1, 1, tzinfo=UTC)
        older.feedback = UserFeedback(outfit_id=older.id, worn_at=today)
    await db_session.commit()
    with (
        patch("app.api.ha.settings.ha_user_email", test_user.email),
        patch("app.api.ha.settings.wardrowbe_ha_token", "r1-test"),
    ):
        response = await client.post("/api/v1/ha/wore-today", headers={"x-ha-token": "r1-test"})
    if logged:
        assert response.status_code == 200
        assert response.json()["already_logged"] is True
        assert response.json()["outfit_id"] == str(older.id)
    else:
        assert response.status_code == 422
        assert response.json() == {"ok": False, "error": "Outfit cannot be worn"}


@pytest.mark.parametrize("kind", ["external", "on_demand"])
async def test_undated_wear_today_real_route(client, db_session, auth_headers, wear_case, kind):
    ids, today, chosen = wear_case
    chosen.source = OutfitSource(kind)
    chosen.scheduled_for = None
    chosen.ai_raw_response = {"inspiration": {}} if kind == "external" else None
    await db_session.commit()
    response = await client.post(
        f"/api/v1/outfits/{chosen.id}/wear-today",
        headers=auth_headers,
        json={"scheduled_for": str(today - timedelta(days=1))},
    )
    assert response.status_code == 200
    assert response.json()["replaces_outfit_id"] == str(chosen.id)
    assert response.json()["cloned_from_outfit_id"] is None


async def test_template_wore_instead_keeps_both_links(client, db_session, auth_headers, wear_case):
    ids, today, chosen = wear_case
    chosen.source, chosen.scheduled_for = OutfitSource.manual, None
    await db_session.commit()
    response = await client.post(
        f"/api/v1/outfits/{chosen.id}/wore-instead",
        headers=auth_headers,
        json={"items": [str(ids[2])]},
    )
    assert response.status_code == 200
    assert (
        response.json()["cloned_from_outfit_id"]
        == response.json()["replaces_outfit_id"]
        == str(chosen.id)
    )
    listed = await client.get(
        "/api/v1/outfits", headers=auth_headers, params={"is_replacement": True}
    )
    assert response.json()["id"] in {outfit["id"] for outfit in listed.json()["outfits"]}


async def test_original_negative_signal(client, db_session, test_user, auth_headers, wear_case):
    ids, today, chosen = wear_case
    with patch("app.api.outfits._run_learning_safely", new_callable=AsyncMock) as learning:
        response = await client.post(
            f"/api/v1/outfits/{chosen.id}/feedback",
            headers=auth_headers,
            json={"actually_worn": False, "wore_instead_items": [str(ids[2])]},
        )
    assert response.status_code == 200
    original = await WearService(db_session)._load(test_user.id, chosen.id)
    assert original.feedback.actually_worn is False
    assert original.feedback.wore_instead_items == [str(ids[2])]
    assert LearningService(db_session)._get_outfit_signal(original) < 0
    assert {call.args[1] for call in learning.await_args_list} == {
        chosen.id,
        UUID(response.json()["outfit_id"]),
    }


@pytest.mark.parametrize(
    "endpoint,missing", [("wear-today", True), ("wore-instead", True), ("wore-instead", False)]
)
async def test_legacy_error_contracts(client, auth_headers, wear_case, endpoint, missing):
    ids, today, chosen = wear_case
    response = await client.post(
        f"/api/v1/outfits/{uuid4() if missing else chosen.id}/{endpoint}",
        headers=auth_headers,
        json={} if endpoint == "wear-today" else {"items": [str(uuid4())]},
    )
    assert response.status_code == (404 if missing else 403)
    assert response.json()["detail"]["error_code"] == (
        "OUTFIT_NOT_FOUND" if missing else "OUTFIT_ITEM_OWNERSHIP"
    )


@pytest.mark.parametrize("state", [ItemStatus.processing, ItemStatus.error])
async def test_only_ready_items_can_be_worn(client, db_session, auth_headers, wear_case, state):
    ids, today, chosen = wear_case
    item = await db_session.get(ClothingItem, ids[0])
    item.status = state
    await db_session.commit()
    response = await client.post(
        f"/api/v1/outfits/{chosen.id}/wore", headers=auth_headers, json={"date": str(today)}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("check", ["limit", "skip"])
async def test_skip_query_is_bounded_and_wore_is_limited(
    client, db_session, auth_headers, wear_case, check
):
    ids, today, chosen = wear_case
    statements = []
    execute = db_session.execute

    async def capture(statement, *args, **kwargs):
        statements.append(str(statement))
        return await execute(statement, *args, **kwargs)

    with (
        patch.object(db_session, "execute", side_effect=capture),
        patch("app.api.outfits.rate_limit_by_user", new_callable=AsyncMock) as limiter,
    ):
        assert (
            await client.post(
                f"/api/v1/outfits/{chosen.id}/wore", headers=auth_headers, json={"date": str(today)}
            )
        ).status_code == 200
    if check == "limit":
        limiter.assert_awaited_once()
        return
    skip = next(statement for statement in statements if "outfits.status IN" in statement)
    assert "outfits.scheduled_for =" in skip and "outfits.id =" in skip


async def test_studio_and_wore_overlap_without_deadlock(
    session_maker, db_session, test_user, auth_headers, wear_case
):
    ids, today, chosen = wear_case
    held, release = asyncio.Event(), asyncio.Event()
    pids = {}
    flush = AsyncSession.flush

    async def database(request: Request):
        async with session_maker() as session:
            name = "studio" if request.url.path.endswith("/studio") else "wore"
            session.info["writer"] = name
            pids[name] = await session.scalar(text("SELECT pg_backend_pid()"))
            yield session
            await session.commit()

    async def pause(self, *args, **kwargs):
        if self.info.get("writer") == "studio" and not held.is_set():
            assert await self.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = pg_backend_pid() AND locktype = 'advisory' AND granted)"
                )
            )
            held.set()
            await release.wait()
        return await flush(self, *args, **kwargs)

    app.dependency_overrides[get_db] = database
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers=auth_headers
        ) as client:
            with patch.object(AsyncSession, "flush", pause):
                studio = asyncio.create_task(
                    client.post(
                        "/api/v1/outfits/studio",
                        json={
                            "items": [str(item_id) for item_id in ids[:2]],
                            "occasion": "casual",
                            "mark_worn": True,
                        },
                    )
                )
                async with asyncio.timeout(15):
                    while not held.is_set():
                        if studio.done():
                            await studio
                        await asyncio.sleep(0)
                wore = asyncio.create_task(
                    client.post(f"/api/v1/outfits/{chosen.id}/wore", json={"date": str(today)})
                )
                async with session_maker() as observer:
                    async with asyncio.timeout(15):
                        while "wore" not in pids or not await observer.scalar(
                            text("SELECT CAST(:owner AS integer) = ANY(pg_blocking_pids(:rival))"),
                            {"owner": pids["studio"], "rival": pids.get("wore", 0)},
                        ):
                            await asyncio.sleep(0)
                release.set()
                first, second = await asyncio.gather(studio, wore)
                assert (first.status_code, second.status_code) == (201, 409)
    finally:
        release.set()
        app.dependency_overrides.clear()
