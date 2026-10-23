from datetime import date, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.item import ItemHistory
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
