import base64
import io
from datetime import timedelta

import pyotp
import qrcode
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import verify_password
from ..database import get_db
from ..dependencies import get_current_user
from ..limiter import limiter
from ..models import User, UserSession, TwoFactorAuth
from .. import two_factor as mfa
from .auth import _create_session, _set_auth_cookie

router = APIRouter(prefix="/auth/2fa", tags=["auth"])


class PasswordInput(BaseModel):
    password: str = Field(max_length=128)


class CodeInput(BaseModel):
    code: str = Field(min_length=1, max_length=64)


class ManageInput(PasswordInput, CodeInput):
    pass


def reauthenticate(db, user, row, password):
    mfa.check_limit(row)
    if not user.hashed_password:
        raise HTTPException(400, "Manage two-factor authentication through your SSO provider")
    if not verify_password(password, user.hashed_password):
        mfa.fail(db, row, "Incorrect password")


def rotate_session(db, user, response):
    db.query(UserSession).filter(UserSession.user_id == user.id).delete()
    _set_auth_cookie(response, _create_session(db, user))


@router.get("")
def status(response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    row = db.get(TwoFactorAuth, user.id)
    return {"enabled": bool(row and row.enabled), "recovery_codes_remaining": len(row.recovery_hashes) if row else 0}


@router.post("/setup")
def setup(data: PasswordInput, response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = mfa.locked_state(db, user.id)
    reauthenticate(db, user, row, data.password)
    if row.enabled:
        raise HTTPException(400, "Two-factor authentication is already enabled")
    secret = pyotp.random_base32()
    row.pending_secret = mfa.cipher().encrypt(secret.encode()).decode()
    row.pending_expires_at = mfa.now() + timedelta(minutes=10)
    db.commit()
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name="Candlr")
    image = io.BytesIO()
    qrcode.make(uri).save(image, format="PNG")
    response.headers["Cache-Control"] = "no-store"
    return {"secret": secret, "qr_code": "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()}


@router.post("/enable")
def enable(data: CodeInput, response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = mfa.locked_state(db, user.id)
    mfa.check_limit(row)
    if row.enabled or not row.pending_secret or row.pending_expires_at <= mfa.now():
        raise HTTPException(400, "Start two-factor setup again")
    step = mfa.totp_step(mfa.cipher().decrypt(row.pending_secret.encode()).decode(), data.code.strip())
    if step is None:
        mfa.fail(db, row)
    row.secret = row.pending_secret
    row.pending_secret = None
    row.pending_expires_at = None
    row.enabled = True
    row.last_step = step
    row.failures = 0
    codes = mfa.recovery_codes(row)
    rotate_session(db, user, response)
    response.headers["Cache-Control"] = "no-store"
    return {"recovery_codes": codes}


@router.post("/disable")
def disable(data: ManageInput, response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = mfa.locked_state(db, user.id)
    reauthenticate(db, user, row, data.password)
    if not row.enabled:
        raise HTTPException(400, "Two-factor authentication is not enabled")
    mfa.verify_code(db, row, data.code)
    row.enabled = False
    row.secret = row.pending_secret = row.pending_expires_at = None
    row.challenge_hash = row.challenge_expires_at = None
    row.recovery_hashes = []
    row.last_step = -1
    rotate_session(db, user, response)
    return {"ok": True}


@router.post("/recovery-codes")
def regenerate(data: ManageInput, response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = mfa.locked_state(db, user.id)
    reauthenticate(db, user, row, data.password)
    if not row.enabled:
        raise HTTPException(400, "Two-factor authentication is not enabled")
    mfa.verify_code(db, row, data.code)
    codes = mfa.recovery_codes(row)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"recovery_codes": codes}


@router.post("/verify")
@limiter.limit("10/minute")
def verify(request: Request, data: CodeInput, response: Response, candlr_2fa: str | None = Cookie(default=None), db: Session = Depends(get_db)):
    if not candlr_2fa:
        raise HTTPException(401, "Sign in again to verify your code")
    hashed = mfa.digest(candlr_2fa)
    candidate = db.query(TwoFactorAuth.user_id).filter(TwoFactorAuth.challenge_hash == hashed).first()
    if not candidate:
        raise HTTPException(401, "Sign in again to verify your code")
    row = mfa.locked_state(db, candidate.user_id)
    if not row.enabled or row.challenge_hash != hashed or not row.challenge_expires_at or row.challenge_expires_at <= mfa.now():
        raise HTTPException(401, "Your sign-in attempt expired. Sign in again.")
    mfa.verify_code(db, row, data.code)
    row.challenge_hash = row.challenge_expires_at = None
    user = db.get(User, row.user_id)
    _set_auth_cookie(response, _create_session(db, user))
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}
