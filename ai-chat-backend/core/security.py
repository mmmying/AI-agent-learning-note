"""密码哈希、JWT 签发与请求认证。"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from core.config import settings
from db.dependencies import SessionDep
from db.orm_models import User
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash

password_hash = PasswordHash.recommended()
dummy_hash = password_hash.hash("dummy-password-for-timing")
bearer = HTTPBearer(auto_error=False)


def create_access_token(user_id: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user_id,
            "iat": now,
            "exp": now + timedelta(minutes=settings.jwt_access_token_expire_minutes),
            "iss": "ai-chat-backend",
            "aud": "ai-chat-frontend",
        },
        settings.jwt_secret_key,
        algorithm="HS256",
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: SessionDep,
) -> User:
    unauthorized = HTTPException(
        status_code=401,
        detail="登录已过期或无效，请重新登录",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key,
            algorithms=["HS256"],
            issuer="ai-chat-backend",
            audience="ai-chat-frontend",
            options={"require": ["sub", "exp", "iat", "iss", "aud"]},
        )
        user = session.get(User, payload["sub"])
    except jwt.InvalidTokenError:
        raise unauthorized from None
    if user is None:
        raise unauthorized
    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]
