import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.access_token import PersonalAccessToken
from app.models.user import User

TOKEN_PREFIX = "wbt_"


class AccessTokenService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, user_id: UUID, name: str) -> tuple[PersonalAccessToken, str]:
        secret = TOKEN_PREFIX + secrets.token_urlsafe(32)
        token = PersonalAccessToken(
            user_id=user_id, name=name, token_hash=hashlib.sha256(secret.encode()).hexdigest()
        )
        self.db.add(token)
        await self.db.flush()
        return token, secret

    async def list(self, user_id: UUID) -> list[PersonalAccessToken]:
        return list(
            await self.db.scalars(
                select(PersonalAccessToken)
                .where(
                    PersonalAccessToken.user_id == user_id,
                    PersonalAccessToken.revoked_at.is_(None),
                )
                .order_by(PersonalAccessToken.created_at.desc())
                .execution_options(populate_existing=True)
            )
        )

    async def revoke(self, user_id: UUID, token_id: UUID) -> bool:
        result = await self.db.execute(
            update(PersonalAccessToken)
            .where(
                PersonalAccessToken.id == token_id,
                PersonalAccessToken.user_id == user_id,
                PersonalAccessToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        return result.rowcount == 1

    async def authenticate(self, secret: str) -> User | None:
        if len(secret) != 47 or not secret.startswith(TOKEN_PREFIX):
            return None
        token = await self.db.scalar(
            select(PersonalAccessToken).where(
                PersonalAccessToken.token_hash == hashlib.sha256(secret.encode()).hexdigest(),
                PersonalAccessToken.revoked_at.is_(None),
            )
        )
        if token is None:
            return None
        now = datetime.now(UTC)
        # The predicate throttles writes across workers, not just within one process.
        await self.db.execute(
            update(PersonalAccessToken)
            .where(
                PersonalAccessToken.id == token.id,
                PersonalAccessToken.revoked_at.is_(None),
                or_(
                    PersonalAccessToken.last_used_at.is_(None),
                    PersonalAccessToken.last_used_at < now - timedelta(minutes=1),
                ),
            )
            .values(last_used_at=now)
        )
        return await self.db.get(User, token.user_id)
