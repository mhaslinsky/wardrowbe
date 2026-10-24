from datetime import timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from starlette.requests import Request

from app.api.ha import laundry_done
from app.api.outfits import FeedbackRequest, UndoWearRequest, submit_feedback, undo_outfit_wear
from app.models.item import ItemHistory, WashHistory
from app.models.outfit import OutfitStatus
from app.services.item_service import ItemService
from app.services.undo_wear_service import UndoWearService
from app.services.wear_service import WearService
from app.utils.timezone import get_user_today
from tests.test_wear_service import source

pytest_plugins = ["tests.test_studio_service"]


async def logged(db, user, items, day):
    chosen = await source(db, user, [item.id for item in items], day=day)
    result = await WearService(db).log_worn(user, chosen, day, caller="test")
    await db.commit()
    return result.outfit


async def state(db, items):
    values = []
    for item in items:
        await db.refresh(item)
        values.append(
            (
                item.wear_count,
                item.last_worn_at,
                item.last_washed_at,
                item.wears_since_wash,
                item.needs_wash,
            )
        )
    return values


async def row_count(db, model, ids):
    return await db.scalar(select(func.count()).select_from(model).where(model.item_id.in_(ids)))


async def test_no_wash_overlap_and_replacement(db_session, studio_user, wardrobe_items):
    today = get_user_today(studio_user)
    shirt, jeans, shoes = wardrobe_items[:3]
    await logged(db_session, studio_user, [shirt, shoes], today - timedelta(days=1))
    before = await state(db_session, [shirt, jeans])
    outfit = await logged(db_session, studio_user, [shirt, jeans], today)
    outfit.feedback.rating = 4
    outfit.feedback.comfort_rating = 3
    outfit.feedback.style_rating = 5
    outfit.feedback.comment = "Keep this"
    outfit.feedback.modification_notes = "Wrong log"
    await db_session.commit()
    await UndoWearService(db_session).undo(studio_user, outfit.id, None)
    await db_session.commit()
    assert await state(db_session, [shirt, jeans]) == before
    assert await row_count(db_session, ItemHistory, [shirt.id, jeans.id, shoes.id]) == 2
    assert outfit.status == OutfitStatus.skipped
    assert outfit.feedback.comment == "Keep this"
    for field in (
        "rating",
        "comfort_rating",
        "style_rating",
        "accepted",
        "worn_at",
        "actually_worn",
        "wore_instead_items",
        "modification_notes",
    ):
        assert getattr(outfit.feedback, field) is None
    assert outfit.feedback.worn_with_modifications is False
    replacement = await logged(db_session, studio_user, [jeans, shoes], today)
    assert replacement.feedback.worn_at == today


@pytest.mark.parametrize("confirmed", [False, True])
async def test_wash_confirmation(db_session, studio_user, wardrobe_items, confirmed):
    today = get_user_today(studio_user)
    items = wardrobe_items[:2]
    await logged(db_session, studio_user, items, today - timedelta(days=1))
    before = await state(db_session, items)
    outfit = await logged(db_session, studio_user, items, today)
    histories = list(
        (
            await db_session.scalars(select(ItemHistory).where(ItemHistory.outfit_id == outfit.id))
        ).all()
    )
    for history in histories:
        history.created_at -= timedelta(seconds=1)
    for item in items:
        await ItemService(db_session).log_wash(item, today)
    await db_session.commit()
    washed = await state(db_session, items)
    with pytest.raises(HTTPException) as error:
        await UndoWearService(db_session).undo(studio_user, outfit.id, None)
    assert error.value.status_code == 409
    assert error.value.detail["error_code"] == "WASH_SINCE_WEAR"
    assert {entry["id"] for entry in error.value.detail["items"]} == {
        str(item.id) for item in items
    }
    assert await state(db_session, items) == washed
    assert await row_count(db_session, ItemHistory, [item.id for item in items]) == 4
    assert await row_count(db_session, WashHistory, [item.id for item in items]) == 2
    chosen = [items[0].id] if confirmed else []
    await UndoWearService(db_session).undo(studio_user, outfit.id, chosen)
    await db_session.commit()
    after = await state(db_session, items)
    assert after[1] == before[1]
    if confirmed:
        assert after[0] == (before[0][0], before[0][1], today, 0, False)
    else:
        assert after[0] == before[0]
    assert await row_count(db_session, WashHistory, [item.id for item in items]) == int(confirmed)


@pytest.mark.parametrize("invalid", ["not_worn", "zero", "unknown_wash", "missing_history"])
async def test_rejected_undo_writes_nothing(db_session, studio_user, wardrobe_items, invalid):
    today = get_user_today(studio_user)
    items = wardrobe_items[:2]
    if invalid == "not_worn":
        outfit = await source(db_session, studio_user, [item.id for item in items], day=today)
    else:
        outfit = await logged(db_session, studio_user, items, today)
    if invalid == "zero":
        await db_session.refresh(items[1])
        items[1].wear_count = 0
    if invalid == "missing_history":
        history = await db_session.scalar(
            select(ItemHistory).where(ItemHistory.outfit_id == outfit.id)
        )
        await db_session.delete(history)
    await db_session.commit()
    before = await state(db_session, items)
    count = await row_count(db_session, ItemHistory, [item.id for item in items])
    with pytest.raises(HTTPException) as error:
        await UndoWearService(db_session).undo(
            studio_user, outfit.id, [uuid4()] if invalid == "unknown_wash" else None
        )
    assert error.value.status_code == (422 if invalid == "unknown_wash" else 409)
    if invalid != "unknown_wash":
        assert error.value.detail["error_code"] == (
            "NOT_WORN" if invalid == "not_worn" else "UNDO_INCONSISTENT"
        )
    assert await state(db_session, items) == before
    assert await row_count(db_session, ItemHistory, [item.id for item in items]) == count


@pytest.mark.parametrize("field", ["rating", "comfort_rating", "style_rating"])
async def test_rating_requires_wear(db_session, studio_user, wardrobe_items, field):
    outfit = await source(db_session, studio_user, [wardrobe_items[0].id])
    with pytest.raises(HTTPException) as error:
        await submit_feedback(outfit.id, FeedbackRequest(**{field: 4}), db_session, studio_user)
    assert error.value.status_code == 409
    assert error.value.detail["error_code"] == "NOT_WORN"


@pytest.mark.parametrize("fail_learning", [False, True])
async def test_route_commits_before_best_effort_learning(
    db_session, studio_user, wardrobe_items, fail_learning
):
    outfit = await logged(db_session, studio_user, wardrobe_items[:2], get_user_today(studio_user))

    async def rebuild(user_id):
        assert not db_session.in_transaction()
        assert user_id == studio_user.id
        if fail_learning:
            raise RuntimeError("Learning unavailable")

    with (
        patch("app.api.outfits.LearningService.recompute_learning_profile", side_effect=rebuild),
        patch("app.api.outfits.clear_suggestions", new_callable=AsyncMock) as clear,
    ):
        response = await undo_outfit_wear(outfit.id, db_session, studio_user, UndoWearRequest())
    assert response.learning_stale is fail_learning
    assert response.status == "skipped"
    clear.assert_awaited_once()
    assert await row_count(db_session, ItemHistory, [item.id for item in wardrobe_items[:2]]) == 0


async def test_laundry_locks_in_item_order():
    db = AsyncMock()
    result = AsyncMock()
    from unittest.mock import MagicMock

    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db.execute.return_value = result
    user = MagicMock()
    with (
        patch("app.api.ha._token_ok", return_value=True),
        patch("app.api.ha._ha_user", new_callable=AsyncMock, return_value=user),
        patch("app.api.ha.get_user_today"),
    ):
        await laundry_done(Request({"type": "http"}), db)
    statement = db.execute.call_args.args[0]
    sql = str(statement)
    assert "ORDER BY clothing_items.id FOR UPDATE" in sql
