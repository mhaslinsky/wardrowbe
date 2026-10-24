import asyncio
from datetime import timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text

from app.models.item import ClothingItem, ItemHistory
from app.models.outfit import Outfit, OutfitItem, OutfitSource, OutfitStatus, UserFeedback
from app.services.outfit_service import OutfitListFilters, OutfitService
from app.services.wear_service import AlreadyThis, Created, DayLogged, WearService
from app.utils.timezone import get_user_today
from tests.test_outfit_wear_history import _items

pytest_plugins = ["tests.test_studio_service"]


async def source(db, user, ids, *, day=None, kind="on_demand", status="pending", inspiration=False):
    outfit = Outfit(
        user_id=user.id,
        occasion="casual",
        scheduled_for=day,
        source=OutfitSource(kind),
        status=OutfitStatus(status),
        ai_raw_response={"inspiration": {}} if inspiration else None,
    )
    db.add(outfit)
    await db.flush()
    for position, item_id in enumerate(ids):
        db.add(OutfitItem(outfit_id=outfit.id, item_id=item_id, position=position))
    await db.flush()
    return await WearService(db)._load(user.id, outfit.id)


@pytest.mark.parametrize(
    "kind,offset,status,inspiration,swap,in_place",
    [
        ("on_demand", 0, "pending", False, False, True),
        ("scheduled", 0, "accepted", False, False, True),
        ("on_demand", 0, "skipped", False, False, True),
        ("external", -1, "pending", False, False, False),
        ("on_demand", None, "pending", False, False, False),
        ("manual", None, "pending", False, False, False),
        ("manual", 0, "pending", False, False, True),
        ("manual", -1, "pending", False, False, False),
        ("manual", 0, "pending", False, True, False),
        ("on_demand", 0, "pending", False, True, False),
        ("manual", None, "pending", False, True, False),
        ("external", None, "pending", True, False, False),
        ("external", None, "pending", True, True, False),
    ],
)
async def test_state_table_and_retry(
    db_session, studio_user, wardrobe_items, kind, offset, status, inspiration, swap, in_place
):
    today = get_user_today(studio_user)
    ids = [item.id for item in wardrobe_items]
    chosen = await source(
        db_session,
        studio_user,
        ids[:2],
        day=None if offset is None else today + timedelta(days=offset),
        kind=kind,
        status=status,
        inspiration=inspiration,
    )
    chosen_id = chosen.id
    target_ids = [ids[0], ids[2]] if swap else None
    service = WearService(db_session)
    first = await service.log_worn(studio_user, chosen, today, target_ids, caller="test")
    assert isinstance(first, Created)
    assert (first.outfit.id == chosen_id) is in_place
    assert first.outfit.feedback.worn_at == today
    assert first.outfit.feedback.actually_worn is True
    if not in_place:
        is_template = kind == "manual" and offset is None
        assert first.outfit.cloned_from_outfit_id == (chosen_id if is_template else None)
        assert first.outfit.replaces_outfit_id == (None if is_template else chosen_id)
        assert first.outfit.ai_raw_response is None
    if swap:
        assert first.outfit.feedback.worn_with_modifications is True
        assert first.outfit.feedback.wore_instead_items == [str(ids[2])]
    await db_session.commit()
    retry = await service.log_worn(studio_user, chosen, today, target_ids, caller="test")
    assert isinstance(retry, AlreadyThis)
    assert retry.outfit.id == first.outfit.id
    history = list(
        (
            await db_session.execute(
                select(ItemHistory).where(ItemHistory.outfit_id == first.outfit.id)
            )
        ).scalars()
    )
    assert {entry.item_id for entry in history} == set(target_ids or ids[:2])
    assert len(history) == 2
    assert all(entry.worn_at == today for entry in history)
    for item_id in ids:
        item = await db_session.get(ClothingItem, item_id, populate_existing=True)
        assert item.wear_count == int(item_id in (target_ids or ids[:2]))


@pytest.mark.parametrize("saved_from_suggestion", [False, True])
async def test_transitive_template_root_and_backfill(
    db_session, studio_user, wardrobe_items, saved_from_suggestion
):
    ids = [item.id for item in wardrobe_items]
    today = get_user_today(studio_user)
    template = await source(db_session, studio_user, ids[:2], kind="manual")
    if saved_from_suggestion:
        original = await source(db_session, studio_user, ids[:2], day=today)
        template.cloned_from_outfit_id = original.id
        await db_session.flush()
    service = WearService(db_session)
    first = await service.log_worn(studio_user, template, today, caller="test")
    await db_session.commit()
    past = today - timedelta(days=3)
    second = await service.log_worn(
        studio_user, first.outfit, past, [ids[0], ids[2]], caller="test"
    )
    await db_session.commit()
    assert second.outfit.cloned_from_outfit_id == template.id
    retry = await service.log_worn(studio_user, second.outfit, past, caller="test")
    assert isinstance(retry, AlreadyThis)
    assert retry.outfit.id == second.outfit.id
    item = await db_session.get(ClothingItem, ids[0], populate_existing=True)
    assert item.last_worn_at == today
    assert item.wear_count == 2
    assert first.outfit.feedback.worn_at == today
    assert template.scheduled_for is None


@pytest.mark.parametrize(
    "bad",
    [
        "empty",
        "duplicate",
        "foreign",
        "archived",
        "rejected",
        "expired",
        "future",
        "missing",
        "foreign_outfit",
    ],
)
async def test_validation_before_lock(db_session, studio_user, wardrobe_items, bad):
    ids = [item.id for item in wardrobe_items]
    today = get_user_today(studio_user)
    chosen = await source(db_session, studio_user, [] if bad == "missing" else ids[:2])
    supplied = None
    if bad == "empty":
        supplied = []
    elif bad == "duplicate":
        supplied = [ids[0], ids[0]]
    elif bad == "foreign":
        supplied = [uuid4()]
    elif bad == "archived":
        wardrobe_items[0].is_archived = True
    elif bad in ("rejected", "expired"):
        chosen.status = OutfitStatus(bad)
    elif bad == "foreign_outfit":
        chosen = Outfit(id=uuid4(), user_id=uuid4())
        with pytest.raises(HTTPException) as failure:
            await WearService(db_session).log_worn(studio_user, chosen, today, caller="test")
        assert failure.value.status_code == 404
        return
    await db_session.flush()
    statements = []
    execute = db_session.execute

    async def capture(statement, *args, **kwargs):
        statements.append(str(statement))
        return await execute(statement, *args, **kwargs)

    with patch.object(db_session, "execute", side_effect=capture):
        with pytest.raises(HTTPException) as failure:
            await WearService(db_session).log_worn(
                studio_user,
                chosen,
                today + timedelta(days=1) if bad == "future" else today,
                supplied,
                caller="test",
            )
    assert failure.value.status_code == 422
    assert not any("pg_advisory" in statement for statement in statements)
    assert (
        await db_session.scalar(
            select(func.count()).select_from(ItemHistory).where(ItemHistory.item_id.in_(ids))
        )
        == 0
    )


async def test_skip_and_double_day(db_session, studio_user, wardrobe_items):
    today = get_user_today(studio_user)
    ids = [item.id for item in wardrobe_items][:2]
    chosen = await source(db_session, studio_user, ids, day=today)
    siblings = []
    for status in OutfitStatus:
        siblings.append(await source(db_session, studio_user, ids, day=today, status=status.value))
    manual = await source(db_session, studio_user, ids, day=today, kind="manual")
    other_day = await source(db_session, studio_user, ids, day=today - timedelta(days=1))
    service = WearService(db_session)
    await service.log_worn(studio_user, chosen, today, caller="test")
    assert [outfit.status for outfit in siblings] == [
        OutfitStatus.skipped
        if status in (OutfitStatus.pending, OutfitStatus.sent, OutfitStatus.viewed)
        else status
        for status in OutfitStatus
    ]
    assert manual.status == other_day.status == OutfitStatus.pending
    manual.feedback = UserFeedback(outfit_id=manual.id, worn_at=today, actually_worn=True)
    await db_session.flush()
    result = await service.log_worn(
        studio_user, other_day, today, [wardrobe_items[2].id], caller="test"
    )
    assert isinstance(result, DayLogged)
    assert set(result.existing_ids) == {chosen.id, manual.id}


@pytest.mark.parametrize("writer", ["wear-today", "wore-instead", "studio", "feedback", "ha"])
@pytest.mark.parametrize("wore_first", [False, True])
async def test_writers_share_day_guard(
    client, db_session, test_user, auth_headers, writer, wore_first
):
    ids = [UUID(value) for value in await _items(db_session, test_user.id)]
    today = get_user_today(test_user)
    chosen = await source(db_session, test_user, ids[:1], day=today)
    rival = await source(
        db_session,
        test_user,
        ids[1:],
        day=None if writer == "wear-today" else today,
        kind="manual" if writer == "wear-today" else "scheduled",
    )
    await db_session.commit()

    async def wore():
        return await client.post(
            f"/api/v1/outfits/{chosen.id}/wore",
            headers=auth_headers,
            json={"date": today.isoformat()},
        )

    async def other():
        if writer == "ha":
            with (
                patch("app.api.ha.settings.wardrowbe_ha_token", "test-token"),
                patch("app.api.ha.settings.ha_user_email", test_user.email),
            ):
                return await client.post(
                    "/api/v1/ha/wore-today", headers={"x-ha-token": "test-token"}
                )
        endpoint = f"/api/v1/outfits/{rival.id}/{writer}"
        body = {}
        if writer == "studio":
            endpoint = "/api/v1/outfits/studio"
            body = {
                "items": [str(value) for value in ids[1:]],
                "occasion": "casual",
                "mark_worn": True,
            }
        elif writer == "wore-instead":
            body = {"items": [str(ids[2])], "scheduled_for": today.isoformat()}
        elif writer == "feedback":
            body = {"worn": True}
        return await client.post(endpoint, headers=auth_headers, json=body)

    first = await (wore() if wore_first else other())
    assert first.status_code in (200, 201), first.text
    second = await (other() if wore_first else wore())
    assert second.status_code == (200 if writer == "ha" and wore_first else 409), second.text
    if writer == "ha" and wore_first:
        assert second.json()["already_logged"] is True
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Outfit)
            .where(
                Outfit.user_id == test_user.id, Outfit.feedback.has(UserFeedback.worn_at == today)
            )
        )
        == 1
    )


async def test_accept_is_not_worn_and_inspiration_not_pending(
    client, db_session, test_user, auth_headers
):
    ids = [UUID(value) for value in await _items(db_session, test_user.id)]
    today = get_user_today(test_user)
    chosen = await source(db_session, test_user, ids, day=today)
    look = await source(db_session, test_user, ids, inspiration=True)
    await db_session.commit()
    db_session.expunge_all()
    response = await client.post(f"/api/v1/outfits/{chosen.id}/accept", headers=auth_headers)
    assert response.status_code == 200
    for filters in (
        {"worn": "true"},
        {"worn": "true", "date_from": str(today), "date_to": str(today)},
    ):
        response = await client.get("/api/v1/outfits", params=filters, headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["total"] == 0
    response = await client.get(
        "/api/v1/outfits", params={"status": "pending"}, headers=auth_headers
    )
    assert str(look.id) not in {outfit["id"] for outfit in response.json()["outfits"]}
    groups, total = await OutfitService(db_session).list_with_filters(
        OutfitListFilters(user_id=test_user.id, worn=False), 1, 20
    )
    assert total == 2


async def test_concurrent_different_sources(session_maker, db_session, studio_user, wardrobe_items):
    today = get_user_today(studio_user)
    ids = [item.id for item in wardrobe_items]
    first = await source(db_session, studio_user, ids[:2], day=today)
    second = await source(db_session, studio_user, ids[2:], day=today)
    await db_session.commit()
    acquired = asyncio.Event()
    waiting = asyncio.Event()
    release = asyncio.Event()

    async def log(source_id, owner):
        async with session_maker() as session:
            service = WearService(session)
            outfit = await service._load(studio_user.id, source_id)
            if owner:
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": f"{studio_user.id}:{today}"},
                )
                acquired.set()
                await release.wait()
            else:
                waiting.set()
            result = await service.log_worn(studio_user, outfit, today, caller="test")
            await session.commit()
            return result

    async def release_when_blocked():
        await waiting.wait()
        async with session_maker() as observer:
            async with asyncio.timeout(15):
                while not await observer.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND NOT granted)"
                    )
                ):
                    await asyncio.sleep(0)
        release.set()

    owner = asyncio.create_task(log(first.id, True))
    await acquired.wait()
    contender = asyncio.create_task(log(second.id, False))
    await release_when_blocked()
    results = await asyncio.gather(owner, contender)
    assert sum(isinstance(result, Created) for result in results) == 1
    assert sum(isinstance(result, DayLogged) for result in results) == 1
