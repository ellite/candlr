"""TOTP secrets are encrypted; recovery and challenge tokens are stored hashed."""
import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import pyotp
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import update

from .config import settings
from .models import TwoFactorAuth

CHALLENGE_COOKIE = "candlr_2fa"


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def cipher():
    key = hashlib.sha256(b"candlr-totp-v1\0" + settings.secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def locked_state(db, user_id):
    # Obtain a SQLite write lock before reading mutable authentication state.
    # This serializes code consumption, challenge use, setup and disable.
    db.execute(update(TwoFactorAuth).where(TwoFactorAuth.user_id == user_id).values(
        failures=TwoFactorAuth.failures))
    row = db.get(TwoFactorAuth, user_id, populate_existing=True)
    if row is None:
        row = TwoFactorAuth(user_id=user_id)
        db.add(row)
        db.flush()
    return row


def check_limit(row):
    if row.locked_until and row.locked_until > now():
        raise HTTPException(429, "Too many attempts. Try again in five minutes.")
    if row.locked_until:
        row.failures = 0
        row.locked_until = None


def fail(db, row, detail="Invalid or already used code"):
    row.failures += 1
    if row.failures >= 5:
        row.locked_until = now() + timedelta(minutes=5)
    db.commit()
    raise HTTPException(400, detail)


def totp_step(secret, code, last_step=-1):
    if len(code) != 6 or not code.isascii() or not code.isdigit():
        return None
    current = int(now().replace(tzinfo=timezone.utc).timestamp()) // 30
    otp = pyotp.TOTP(secret)
    for step in (current, current - 1, current + 1):
        if step > last_step and hmac.compare_digest(otp.at(step * 30), code):
            return step
    return None


def verify_code(db, row, code):
    check_limit(row)
    code = code.strip().replace(" ", "")
    step = totp_step(cipher().decrypt(row.secret.encode()).decode(), code, row.last_step)
    if step is not None:
        row.last_step = step
    else:
        hashed = digest(code.replace("-", "").lower())
        remaining = list(row.recovery_hashes)
        if hashed not in remaining:
            fail(db, row)
        remaining.remove(hashed)
        row.recovery_hashes = remaining
    row.failures = 0
    row.locked_until = None


def recovery_codes(row):
    codes = [secrets.token_hex(8) for _ in range(10)]
    row.recovery_hashes = [digest(code) for code in codes]
    return ["-".join(code[i:i+4] for i in range(0, 16, 4)) for code in codes]


def begin_login(db, user, response):
    row = locked_state(db, user.id)
    if not row.enabled:
        return False
    check_limit(row)
    token = secrets.token_urlsafe(32)
    row.challenge_hash = digest(token)
    row.challenge_expires_at = now() + timedelta(minutes=5)
    db.commit()
    response.set_cookie(CHALLENGE_COOKIE, token, httponly=True, secure=settings.cookie_secure,
                        samesite="lax", max_age=300, path="/")
    response.headers["Cache-Control"] = "no-store"
    return True
