import os
import subprocess
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.outfit import Outfit

PREVIOUS = "921b8d3bc2b8"


def _alembic(*args: str) -> None:
    env = {**os.environ, "DATABASE_URL": os.environ["TEST_DATABASE_URL"]}
    subprocess.run(["python", "-m", "alembic", *args], env=env, check=True, capture_output=True)


@pytest.mark.asyncio
async def test_clears_only_generated_wore_instead_names(db_session: AsyncSession, test_user: User):
    user_id = test_user.id
    await db_session.commit()
    _alembic("downgrade", PREVIOUS)
    try:

        async def insert_outfit(occasion="casual", name=None, replaces=None):
            outfit_id = uuid4()
            await db_session.execute(
                text(
                    "INSERT INTO outfits (id, user_id, occasion, source, status, replaces_outfit_id, name) "
                    "VALUES (:id, :user, :occasion, 'manual', 'pending', :replaces, :name)"
                ),
                {
                    "id": outfit_id,
                    "user": user_id,
                    "occasion": occasion,
                    "replaces": replaces,
                    "name": name,
                },
            )
            return outfit_id

        original = await insert_outfit()
        ids = {
            "generated": await insert_outfit("casual", "Casual (wore instead)", original),
            "multiword": await insert_outfit("date_night", "Date_Night (wore instead)", original),
            "renamed": await insert_outfit("casual", "My favourite look", original),
            "kept": await insert_outfit(name="Casual (wore instead)"),
        }
        await db_session.commit()
    finally:
        _alembic("upgrade", "head")

    db_session.expire_all()
    assert (await db_session.get(Outfit, ids["generated"])).name is None
    assert (await db_session.get(Outfit, ids["multiword"])).name is None
    assert (await db_session.get(Outfit, ids["renamed"])).name == "My favourite look"
    assert (await db_session.get(Outfit, ids["kept"])).name == "Casual (wore instead)"
