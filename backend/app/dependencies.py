from typing import Annotated

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import ACCESS, decode_token
from app.database import get_db
from app.errors import ApiError
from app.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)], db: DbSession
) -> User:
    user_id = decode_token(token, ACCESS)
    if user_id is None:
        raise ApiError(401, "invalid_token", "Invalid or expired token")
    user = await db.get(User, user_id)
    if user is None:
        raise ApiError(401, "invalid_token", "User no longer exists")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
