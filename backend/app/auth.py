import secrets
from datetime import datetime, timedelta, timezone
import jwt
from jwt.exceptions import InvalidTokenError
import bcrypt
from .config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def session_lifetime(remember: bool = False) -> timedelta:
    if remember:
        return timedelta(days=settings.remember_me_days)
    return timedelta(minutes=settings.access_token_expire_minutes)


def sign_token(user_id: int, jti: str, expire: datetime) -> str:
    return jwt.encode(
        {"sub": str(user_id), "jti": jti, "exp": expire},
        settings.secret_key,
        algorithm=settings.algorithm,
    )


def create_access_token(user_id: int, remember: bool = False) -> tuple[str, str, datetime]:
    """Returns (signed_token, jti, expires_at)."""
    jti = secrets.token_urlsafe(32)
    expire = datetime.now(timezone.utc) + session_lifetime(remember)
    return sign_token(user_id, jti, expire), jti, expire


def decode_token(token: str) -> tuple[int, str] | None:
    """Returns (user_id, jti) or None if invalid."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        user_id = payload.get("sub")
        jti = payload.get("jti")
        if user_id is None or not jti:
            return None
        return int(user_id), jti
    except (InvalidTokenError, TypeError, ValueError):
        return None
