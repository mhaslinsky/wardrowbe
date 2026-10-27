from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings
from app.database import DbSession
from app.models.access_token import PersonalAccessToken
from app.models.user import User
from app.schemas.auth import AuthSession, TokenPayload
from app.services.access_token_service import TOKEN_PREFIX, AccessTokenService
from app.services.user_service import FORWARD_AUTH, UserService
from app.utils.forward_auth import (
    FORWARD_AUTH_SECRET_HEADER,
    forward_auth_secret_matches,
    proxy_header,
    single_header,
)

settings = get_settings()

bearer_scheme = HTTPBearer(auto_error=False)


def decode_token(token: str) -> TokenPayload:
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=["HS256"],
            options={"verify_exp": True},
        )
        return TokenPayload(**payload)
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


async def get_current_user_optional(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbSession,
) -> User | None:
    if not credentials:
        return None

    try:
        user_service = UserService(db)
        user, access_token = await _resolve_bearer(credentials.credentials, db, user_service)
    except HTTPException:
        return None
    # A deactivated account or a switched proxy user is treated as anonymous, as
    # get_current_user refuses both.
    if user is None or not user.is_active:
        return None
    if await _proxy_user_switched(request, user, user_service):
        return None
    if access_token is not None:
        await AccessTokenService(db).touch(access_token)
    return user


# The browser reaches /api/v1 straight through nginx or the ingress, past the frontend middleware
# that ends a session when the proxy user changes, so a request the proxy vouched for is checked here.
async def _proxy_user_switched(request: Request, user: User, user_service: UserService) -> bool:
    presented_secret = single_header(request.headers, FORWARD_AUTH_SECRET_HEADER)
    if not forward_auth_secret_matches(presented_secret, settings.forward_auth_secret):
        return False
    remote_user = proxy_header(request.headers, "Remote-User")
    return bool(remote_user) and not await user_service.holds_identity(
        user.id, FORWARD_AUTH, remote_user
    )


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbSession,
) -> User:
    user_service = UserService(db)
    user = None
    access_token = None

    if credentials:
        user, access_token = await _resolve_bearer(credentials.credentials, db, user_service)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if await _proxy_user_switched(request, user, user_service):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The signed-in user changed at the proxy. Sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )

    if access_token is not None:
        await AccessTokenService(db).touch(access_token)
    return user


async def get_current_session(
    user: Annotated[User, Depends(get_current_user)],
) -> AuthSession:
    return AuthSession(
        user_id=user.id,
        external_id=user.external_id,
        email=user.email,
        display_name=user.display_name,
        family_id=user.family_id,
        role=user.role,
    )


CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentUserOptional = Annotated[User | None, Depends(get_current_user_optional)]
CurrentSession = Annotated[AuthSession, Depends(get_current_session)]
WriteUser = CurrentUser


async def _resolve_bearer(
    secret: str, db: DbSession, user_service: UserService
) -> tuple[User | None, PersonalAccessToken | None]:
    if secret.startswith(TOKEN_PREFIX):
        return await AccessTokenService(db).authenticate(secret)
    return await user_service.get_by_token(decode_token(secret)), None


async def get_session_user(
    user: CurrentUser,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    if credentials and credentials.credentials.startswith(TOKEN_PREFIX):
        raise HTTPException(status_code=403, detail="Sign in to manage tokens")
    return user


SessionUser = Annotated[User, Depends(get_session_user)]
