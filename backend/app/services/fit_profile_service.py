import uuid

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fit_profile import UserFitProfile
from app.schemas.fit_profile import FitProfile


class FitProfileService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, user_id: uuid.UUID) -> FitProfile:
        stored = await self.db.scalar(
            select(UserFitProfile.profile).where(UserFitProfile.user_id == user_id)
        )
        return FitProfile.model_validate(stored if stored is not None else {"rev": "new"})

    async def update(self, user_id: uuid.UUID, profile: FitProfile) -> FitProfile:
        changes = {**profile.model_dump(exclude_unset=True), "rev": uuid.uuid4().hex}
        if profile.rev == "new":
            statement = (
                insert(UserFitProfile)
                .values(user_id=user_id, profile={**profile.model_dump(), **changes})
                .on_conflict_do_nothing(index_elements=[UserFitProfile.user_id])
            )
        else:
            statement = (
                update(UserFitProfile)
                .where(
                    UserFitProfile.user_id == user_id,
                    UserFitProfile.profile["rev"].astext == profile.rev,
                )
                .values(profile=UserFitProfile.profile.concat(changes))
            )
        result = await self.db.execute(statement.returning(UserFitProfile.profile))
        stored = result.scalar_one_or_none()
        if stored is None:
            await self.db.rollback()
            raise HTTPException(409, "Fit profile changed")
        await self.db.commit()
        return FitProfile.model_validate(stored)
