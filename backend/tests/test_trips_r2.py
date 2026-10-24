from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outfit import Outfit
from app.models.trip import Trip


async def test_close_nested_session_then_fail_rolls_back_all_rows(db_session, test_user):
    user_id = test_user.id
    events = []
    try:
        async with AsyncSession(
            bind=await db_session.connection(),
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        ) as planning:
            planning.add(
                Outfit(user_id=user_id, occasion="casual", scheduled_for=date(2030, 10, 24))
            )
            await planning.commit()
            await planning.close()
            events.append("closed-after-day-one-commit")
            raise ValueError("Day two failed")
    except ValueError:
        events.append("outer-rollback")
        await db_session.rollback()
    assert events == ["closed-after-day-one-commit", "outer-rollback"]
    for model in (Outfit, Trip):
        assert (
            await db_session.scalar(
                select(func.count()).select_from(model).where(model.user_id == user_id)
            )
            == 0
        )
