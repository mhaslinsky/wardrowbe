import asyncio
from unittest.mock import patch
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import get_db
from app.main import app
from app.models.outfit import Outfit, OutfitStatus
from app.services.wear_service import Created, WearService
from app.utils.auth import get_current_user
from app.utils.timezone import get_user_today
from tests.test_outfit_wear_history import _items
from tests.test_wear_service import source


@pytest.mark.parametrize("decision", ["accept", "reject"])
async def test_skip_preserves_concurrent_decision(db_session, session_maker, test_user, decision):
    ids = [UUID(value) for value in await _items(db_session, test_user.id)]
    today = get_user_today(test_user)
    chosen = await source(db_session, test_user, ids[:2], day=today)
    rival = await source(db_session, test_user, ids[:2], day=today)
    chosen_id, rival_id = chosen.id, rival.id
    await db_session.commit()
    skip_ready = asyncio.Event()
    decision_committed = asyncio.Event()

    async def database():
        async with session_maker() as session:
            yield session

    async def authenticated():
        return test_user

    previous = app.dependency_overrides.copy()
    app.dependency_overrides.update({get_db: database, get_current_user: authenticated})
    try:
        async with session_maker() as wear_session:
            execute = wear_session.execute

            async def interleave(statement, *args, **kwargs):
                sql = str(statement)
                skip_statement = "outfits.status IN" in sql and "outfits.scheduled_for" in sql
                if skip_statement and statement.is_select:
                    result = await execute(statement, *args, **kwargs)
                    skip_ready.set()
                    await decision_committed.wait()
                    return result
                if skip_statement and statement.is_update:
                    skip_ready.set()
                    await decision_committed.wait()
                return await execute(statement, *args, **kwargs)

            async def wear():
                await WearService(wear_session).begin(test_user, today)
                loaded = await WearService(wear_session)._load(test_user.id, chosen_id)
                result = await WearService(wear_session).log_worn(
                    test_user, loaded, today, caller="test"
                )
                assert isinstance(result, Created)
                await wear_session.commit()

            async def decide():
                await skip_ready.wait()
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(f"/api/v1/outfits/{rival_id}/{decision}")
                    assert response.status_code == 200, response.text
                decision_committed.set()

            with patch.object(wear_session, "execute", side_effect=interleave):
                async with asyncio.timeout(15):
                    await asyncio.gather(wear(), decide())
        async with session_maker() as verify:
            status = await verify.scalar(select(Outfit.status).where(Outfit.id == rival_id))
            assert status == (
                OutfitStatus.accepted if decision == "accept" else OutfitStatus.rejected
            )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
