import os
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import column, insert, inspect, table, text
from sqlalchemy.ext.asyncio import AsyncSession


def alembic(*args: str) -> None:
    env = {**os.environ, "DATABASE_URL": os.environ["TEST_DATABASE_URL"]}
    result = subprocess.run(
        ["python", "-m", "alembic", *args], env=env, capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic {' '.join(args)} failed:\n{result.stderr[-4000:]}")


@asynccontextmanager
async def migration_downgrade(db_session: AsyncSession, revision: str) -> AsyncIterator[None]:
    # Migrations lock users, so the test's own transaction must end before each alembic run or
    # the migration waits on it until lock_timeout; a failed body leaves it idle or aborted.
    await db_session.commit()
    alembic("downgrade", revision)
    try:
        yield
    finally:
        await db_session.rollback()
        alembic("upgrade", "head")


async def insert_at_revision(
    db_session: AsyncSession, *rows: object, server_default: tuple[str, ...] = ()
) -> None:
    """Insert ORM rows into a downgraded schema, writing only the columns that schema has.

    Columns named in server_default are left out so the database default applies.

    session.add sends every mapped column, so a column from a later migration breaks the insert.
    """
    for row in rows:
        mapped = row.__table__
        present = set(
            await db_session.scalars(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = :name"),
                {"name": mapped.name},
            )
        )
        state = inspect(row)
        values = {}
        for col in mapped.columns:
            if col.name not in present or col.name in server_default:
                continue
            key = state.mapper.get_property_by_column(col).key
            if key in state.dict:
                values[col.name] = state.dict[key]
            elif col.default is not None and col.default.is_scalar:
                values[col.name] = col.default.arg
            elif col.default is not None and col.default.is_callable:
                values[col.name] = col.default.arg(None)
        target = table(mapped.name, *(column(name, mapped.c[name].type) for name in values))
        await db_session.execute(insert(target).values(values))
