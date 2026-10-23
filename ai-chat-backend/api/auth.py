from uuid import uuid4

from core.security import CurrentUserDep, create_access_token, dummy_hash, password_hash
from db.dependencies import SessionDep
from db.orm_models import User
from fastapi import APIRouter, HTTPException
from models.schemas import AuthRequest, TokenResponse, UserResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


router = APIRouter(prefix="/auth", tags=["auth"])


def token_response(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user.id),
        user=UserResponse.model_validate(user),
    )


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(request: AuthRequest, session: SessionDep):
    user = User(
        id=str(uuid4()),
        username=request.username,
        password_hash=password_hash.hash(request.password),
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="用户名已存在") from None
    return token_response(user)


@router.post("/login", response_model=TokenResponse)
def login(request: AuthRequest, session: SessionDep):
    user = session.scalar(select(User).where(User.username == request.username))
    valid = password_hash.verify(
        request.password, user.password_hash if user else dummy_hash
    )
    if user is None or not valid:
        raise HTTPException(
            status_code=401,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token_response(user)


# 查询当前用户
@router.get("/me", response_model=UserResponse)
def me(user: CurrentUserDep):
    return user
