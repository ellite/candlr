from datetime import datetime, timezone
from fastapi import Depends, HTTPException, status, Cookie
from sqlalchemy.orm import Session, joinedload
from typing import Optional
from .database import get_db
from .models import User, UserSession
from .auth import decode_token


def get_current_user(
    candlr_token: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )
    if not candlr_token:
        raise credentials_exception
    decoded = decode_token(candlr_token)
    if decoded is None:
        raise credentials_exception
    user_id, jti = decoded
    session = (
        db.query(UserSession)
        .options(joinedload(UserSession.user))
        .filter(
            UserSession.jti == jti,
            UserSession.user_id == user_id,
            UserSession.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )
    if session is None:
        raise credentials_exception
    return session.user


def get_admin_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin required")
    return current_user
