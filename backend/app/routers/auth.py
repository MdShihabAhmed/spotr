from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from app.auth import (
    REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.dependencies import CurrentUser, DbSession
from app.errors import ApiError
from app.models import User
from app.schemas import RefreshRequest, Token, UserCreate, UserOut

router = APIRouter(tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=201)
async def register(payload: UserCreate, db: DbSession):
    existing = await db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise ApiError(409, "email_already_registered", "An account with this email already exists")
    user = User(email=payload.email, hashed_password=hash_password(payload.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=Token)
async def login(form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DbSession):
    user = await db.scalar(select(User).where(User.email == form.username))
    if user is None or not verify_password(form.password, user.hashed_password):
        raise ApiError(401, "invalid_credentials", "Incorrect email or password")
    return Token(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/refresh", response_model=Token)
async def refresh(payload: RefreshRequest, db: DbSession):
    """Exchange a refresh token for a fresh token pair.

    Hint sessions and pitfall dialogues outlive a 30-minute access token, so without
    this the user is logged out mid-problem.
    """
    user_id = decode_token(payload.refresh_token, REFRESH)
    if user_id is None:
        raise ApiError(401, "invalid_refresh_token", "Invalid or expired refresh token")
    user = await db.get(User, user_id)
    if user is None:
        raise ApiError(401, "invalid_refresh_token", "User no longer exists")
    return Token(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
    return user
