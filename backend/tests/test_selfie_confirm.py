import asyncio
from datetime import timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.main import app
from app.models.item import ClothingItem, ItemHistory, ItemStatus
from app.models.learning import ItemPairScore, OutfitPerformance, UserLearningProfile
from app.models.outfit import Outfit, UserFeedback
from app.services.suggestion_cache import _cache_key, push_suggestions
from app.utils.auth import get_current_user
from app.utils.redis_lock import get_redis
from app.utils.timezone import get_user_today
from tests.test_outfit_wear_history import _items
from tests.test_wear_service import source

URL = "/api/v1/selfie/confirm"


@pytest_asyncio.fixture
async def confirm_client(session_maker, test_user):
    async def database():
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def authenticated():
        return test_user

    previous = app.dependency_overrides.copy()
    app.dependency_overrides.update({get_db: database, get_current_user: authenticated})
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


async def counts(db, user_id):
    from app.models.selfie_confirm import SelfieConfirm

    return tuple(
        [
            await db.scalar(select(func.count()).select_from(model).where(model.user_id == user_id))
            for model in (Outfit, SelfieConfirm)
        ]
        + [
            await db.scalar(
                select(func.count())
                .select_from(ItemHistory)
                .join(ClothingItem)
                .where(ClothingItem.user_id == user_id)
            )
        ]
    )


def payload(ids, request_id=None):
    return {"request_id": str(request_id or uuid4()), "item_ids": ids}


async def test_corrected_wear_replay_midnight_and_learning(confirm_client, db_session, test_user):
    ids = await _items(db_session, test_user.id)
    body = payload(ids[1:])
    redis = await get_redis()
    await push_suggestions(test_user.id, "casual", [{"before": True}])
    first = await confirm_client.post(URL, json=body)
    assert first.status_code == 200, first.text
    outfit = first.json()
    assert not await redis.exists(_cache_key(test_user.id, "casual"))
    assert {item["id"] for item in outfit["items"]} == set(ids[1:])
    assert outfit["scheduled_for"] == str(get_user_today(test_user))
    assert outfit["status"] == "accepted"
    assert outfit["feedback"]["actually_worn"] is True
    assert await counts(db_session, test_user.id) == (1, 1, 2)
    for item_id in ids:
        item = await db_session.get(ClothingItem, UUID(item_id), populate_existing=True)
        assert item.wear_count == int(item_id in ids[1:])
    learning = []
    for model in (ItemPairScore, UserLearningProfile):
        rows = (
            await db_session.execute(select(model.__table__).where(model.user_id == test_user.id))
        ).all()
        assert rows
        learning.append(rows)
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(OutfitPerformance)
            .where(OutfitPerformance.outfit_id == UUID(outfit["id"]))
        )
        == 1
    )
    redis = await get_redis()
    await push_suggestions(test_user.id, "casual", [{"sentinel": True}])
    cache_before = await redis.lrange(_cache_key(test_user.id, "casual"), 0, -1)
    assert cache_before
    await db_session.execute(
        ClothingItem.__table__.update()
        .where(ClothingItem.id == UUID(ids[1]))
        .values(is_archived=True)
    )
    await db_session.commit()
    tomorrow = get_user_today(test_user) + timedelta(days=1)
    with patch("app.services.selfie_confirm_service.get_user_today", return_value=tomorrow):
        retry = await confirm_client.post(URL, json={**body, "item_ids": list(reversed(ids[1:]))})
    assert retry.status_code == 200, retry.text
    assert retry.json()["id"] == outfit["id"]
    assert retry.json()["scheduled_for"] == outfit["scheduled_for"]
    assert await counts(db_session, test_user.id) == (1, 1, 2)
    for model, before in zip((ItemPairScore, UserLearningProfile), learning, strict=True):
        assert (
            await db_session.execute(select(model.__table__).where(model.user_id == test_user.id))
        ).all() == before
    assert await redis.lrange(_cache_key(test_user.id, "casual"), 0, -1) == cache_before
    changed = await confirm_client.post(URL, json={**body, "item_ids": ids[:1]})
    assert changed.status_code == 409
    assert changed.json()["detail"]["error_code"] == "REQUEST_REUSED"
    await db_session.execute(delete(Outfit).where(Outfit.id == UUID(outfit["id"])))
    await db_session.commit()
    with patch("app.services.selfie_confirm_service.get_user_today", return_value=tomorrow):
        deleted = await confirm_client.post(URL, json=body)
    assert deleted.status_code == 410
    assert deleted.json()["detail"] == {
        "error_code": "CONFIRM_RESULT_DELETED",
        "worn_on": outfit["scheduled_for"],
    }
    assert await counts(db_session, test_user.id) == (0, 1, 2)


@pytest.mark.parametrize(
    "bad", ["empty", "duplicate", "missing", "malformed", "foreign", "archived", "processing"]
)
async def test_validation_writes_nothing(confirm_client, db_session, test_user, bad):
    ids = await _items(db_session, test_user.id)
    body = payload(ids)
    if bad == "empty":
        body["item_ids"] = []
    if bad == "duplicate":
        body["item_ids"] = [ids[0], ids[0]]
    if bad == "missing":
        del body["request_id"]
    if bad == "malformed":
        body["item_ids"] = ["bad-uuid"]
    if bad == "foreign":
        body["item_ids"] = [str(uuid4())]
    if bad in ("archived", "processing"):
        item = await db_session.get(ClothingItem, UUID(ids[0]))
        if bad == "archived":
            item.is_archived = True
        else:
            item.status = ItemStatus.processing
        await db_session.commit()
    result = await confirm_client.post(URL, json=body)
    assert result.status_code == (403 if bad in ("foreign", "archived", "processing") else 422), (
        result.text
    )
    assert await counts(db_session, test_user.id) == (0, 0, 0)


async def test_structure_before_replay_and_day_logged_rollback(
    confirm_client, db_session, test_user
):
    ids = await _items(db_session, test_user.id)
    body = payload(ids)
    assert (await confirm_client.post(URL, json=body)).status_code == 200
    for invalid in ([], [ids[0], ids[0]]):
        assert (
            await confirm_client.post(URL, json={**body, "item_ids": invalid})
        ).status_code == 422
    conflict = await confirm_client.post(URL, json=payload(ids))
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"]["error_code"] == "DAY_LOGGED"
    assert len(conflict.json()["detail"]["existing_ids"]) == 1
    assert await counts(db_session, test_user.id) == (1, 1, 3)
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(UserFeedback)
            .join(Outfit)
            .where(Outfit.user_id == test_user.id)
        )
        == 1
    )


async def test_failure_after_wear_rolls_back(confirm_client, db_session, test_user):
    ids = await _items(db_session, test_user.id)
    from app.services.selfie_confirm_service import SelfieConfirmService

    original = SelfieConfirmService.confirm

    async def fail_after_wear(self, *args):
        await original(self, *args)
        raise RuntimeError("injected before commit")

    with patch.object(SelfieConfirmService, "confirm", fail_after_wear):
        with pytest.raises(RuntimeError, match="injected"):
            await confirm_client.post(URL, json=payload(ids))
    assert await counts(db_session, test_user.id) == (0, 0, 0)
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(UserFeedback)
            .join(Outfit)
            .where(Outfit.user_id == test_user.id)
        )
        == 0
    )
    assert (await confirm_client.post(URL, json=payload(ids))).status_code == 200


async def test_unique_request_constraint(db_session, test_user):
    from app.models.selfie_confirm import SelfieConfirm

    request_id = uuid4()
    for index in range(2):
        db_session.add(
            SelfieConfirm(
                user_id=test_user.id,
                request_id=request_id,
                payload_hash="x",
                worn_on=get_user_today(test_user),
            )
        )
        if index == 0:
            await db_session.commit()
        else:
            with pytest.raises(IntegrityError, match="uq_selfie_confirm_request"):
                await db_session.commit()
            await db_session.rollback()


@pytest.mark.parametrize("rival", ["same_request", "other_request", "wore"])
async def test_concurrent_writers(confirm_client, db_session, session_maker, test_user, rival):
    ids = await _items(db_session, test_user.id)
    body = payload(ids[:2])
    rival_url, rival_body = URL, body if rival == "same_request" else payload(ids[1:])
    if rival == "wore":
        chosen = await source(db_session, test_user, [UUID(value) for value in ids[1:]])
        rival_url = f"/api/v1/outfits/{chosen.id}/wore"
        rival_body = {"date": str(get_user_today(test_user))}
        await db_session.commit()
    first_read, second_read, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    sessions = []
    previous_db = app.dependency_overrides[get_db]

    async def database():
        async with session_maker() as session:
            position = len(sessions)
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            sessions.append(pid)
            scalar = session.scalar

            async def gated(statement, *args, **kwargs):
                result = await scalar(statement, *args, **kwargs)
                if "FROM selfie_confirms" in str(statement):
                    if position == 0:
                        first_read.set()
                        await release.wait()
                    else:
                        second_read.set()
                return result

            with patch.object(session, "scalar", gated):
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

    app.dependency_overrides[get_db] = database
    try:
        async with asyncio.timeout(30):
            first = asyncio.create_task(confirm_client.post(URL, json=body))
            await first_read.wait()
            second = asyncio.create_task(confirm_client.post(rival_url, json=rival_body))
            async with session_maker() as observer:
                while True:
                    blocked = len(sessions) == 2 and await observer.scalar(
                        text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": sessions[1]}
                    )
                    if blocked or second_read.is_set() or second.done():
                        break
                    await observer.execute(text("SELECT 1"))
            release.set()
            responses = await asyncio.gather(first, second)
        assert responses[0].status_code == 200, responses[0].text
        if rival == "same_request":
            assert responses[1].status_code == 200, responses[1].text
            assert responses[0].json()["id"] == responses[1].json()["id"]
        else:
            assert responses[1].status_code == 409, responses[1].text
            assert responses[1].json()["detail"]["error_code"] == "DAY_LOGGED"
        assert await counts(db_session, test_user.id) == (2 if rival == "wore" else 1, 1, 2)
    finally:
        release.set()
        app.dependency_overrides[get_db] = previous_db


async def test_post_commit_failure_keeps_wear(confirm_client, db_session, test_user):
    ids = await _items(db_session, test_user.id)
    with (
        patch(
            "app.api.selfie.LearningService.process_feedback",
            side_effect=RuntimeError("learning unavailable"),
        ),
        patch("app.api.selfie.clear_suggestions", side_effect=RuntimeError("cache unavailable")),
    ):
        result = await confirm_client.post(URL, json=payload(ids))
    assert result.status_code == 200, result.text
    assert await counts(db_session, test_user.id) == (1, 1, 3)
