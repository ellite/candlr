import logging
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, Response, Cookie, status
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Optional
from ..database import get_db
from ..models import User, UserSession, PasswordResetToken
from ..schemas import (
    UserRegister,
    UserLogin,
    UserOut,
    TwoFactorRequired,
    ChangePassword,
    AccountDelete,
    RegistrationStatusOut,
    PasswordResetStatusOut,
    ForgotPasswordRequest,
    ResetPasswordRequest,
)
from ..auth import hash_password, verify_password, create_access_token, decode_token
from ..dependencies import get_current_user
from ..config import settings
from ..notifiers import email as email_notifier
from ..notifiers.errors import NotifierError
from ..images import delete_image
from ..seed import seed_default_event_types
from ..two_factor import begin_login
from ..models import TwoFactorAuth
from ..limiter import limiter

logger = logging.getLogger("uvicorn.error")

RESET_TOKEN_MAX_AGE = timedelta(hours=1)

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_NAME = "candlr_token"
COOKIE_MAX_AGE = settings.access_token_expire_minutes * 60


def _set_auth_cookie(response: Response, token: str):
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        max_age=COOKIE_MAX_AGE,
        samesite="lax",
    )


def _create_session(db: Session, user: User) -> str:
    token, jti, expires_at = create_access_token(user.id)
    # prune expired sessions for this user opportunistically
    db.query(UserSession).filter(
        UserSession.user_id == user.id,
        UserSession.expires_at <= datetime.now(timezone.utc),
    ).delete()
    db.add(UserSession(jti=jti, user_id=user.id, expires_at=expires_at))
    db.commit()
    return token


def _registration_allowed(db: Session) -> bool:
    """Returns True if registration is currently open."""
    count = db.query(func.count()).select_from(User).scalar()

    # Always allow the very first user regardless of settings, since a
    # fresh instance would otherwise have no way to bootstrap an account.
    if count == 0:
        return True

    if not settings.enable_registrations:
        return False

    # 0 means unlimited; otherwise enforce the cap
    if settings.registration_max_allowed_users > 0:
        return count < settings.registration_max_allowed_users

    return True


@router.get("/registration-status", response_model=RegistrationStatusOut)
def registration_status(db: Session = Depends(get_db)):
    return RegistrationStatusOut(enabled=_registration_allowed(db))


@router.get("/password-reset-status", response_model=PasswordResetStatusOut)
def password_reset_status():
    return PasswordResetStatusOut(enabled=bool(settings.smtp_address))


@router.post("/forgot-password")
@limiter.limit("5/minute")
def forgot_password(request: Request, data: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Always returns 200, regardless of whether the email is registered,
    so this endpoint can't be used to find out who has an account."""
    if not settings.smtp_address:
        raise HTTPException(status_code=503, detail="Password reset is not configured")

    user = db.query(User).filter(User.email == data.email).first()
    if user:
        db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).delete()
        token = secrets.token_urlsafe(32)
        db.add(PasswordResetToken(user_id=user.id, token=token))
        db.commit()

        link = f"{settings.server_url}/reset-password/{token}"
        body = (
            f"We received a request to reset your Candlr password.\n\n"
            f"Reset it here: {link}\n\n"
            f"This link expires in 1 hour. If you didn't request this, you can ignore this email."
        )
        try:
            email_notifier.send(user.email, "Reset your Candlr password", body, {})
        except NotifierError as e:
            logger.error(f"Failed to send password reset email to {user.email}: {e}")

    return {"message": "If that email is registered, a reset link has been sent."}


@router.post("/reset-password/{token}")
@limiter.limit("10/minute")
def reset_password(request: Request, token: str, data: ResetPasswordRequest, db: Session = Depends(get_db)):
    record = db.query(PasswordResetToken).filter(PasswordResetToken.token == token).first()
    if not record:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has already been used")

    age = datetime.now(timezone.utc) - record.created_at.replace(tzinfo=timezone.utc)
    if age > RESET_TOKEN_MAX_AGE:
        db.delete(record)
        db.commit()
        raise HTTPException(status_code=400, detail="This reset link has expired")

    user = db.query(User).filter(User.id == record.user_id).first()
    if not user:
        db.delete(record)
        db.commit()
        raise HTTPException(status_code=400, detail="This reset link is invalid or has already been used")

    db.query(UserSession).filter(UserSession.user_id == user.id).delete()
    db.query(TwoFactorAuth).filter(TwoFactorAuth.user_id == user.id).update({"challenge_hash": None, "challenge_expires_at": None, "pending_secret": None, "pending_expires_at": None})
    user.hashed_password = hash_password(data.new_password)
    db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).delete()
    db.commit()
    return {"ok": True}


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def register(request: Request, data: UserRegister, response: Response, db: Session = Depends(get_db)):
    if not _registration_allowed(db):
        raise HTTPException(status_code=403, detail="Registration is disabled")

    username_taken = db.query(User).filter(User.username == data.username).first()
    email_taken = db.query(User).filter(User.email == data.email).first()
    if username_taken or email_taken:
        raise HTTPException(status_code=400, detail="Username or email already registered")

    is_first_user = db.query(User).count() == 0
    user = User(
        username=data.username,
        email=data.email,
        hashed_password=hash_password(data.password),
        is_admin=is_first_user,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    seed_default_event_types(db, user.id)

    token = _create_session(db, user)
    _set_auth_cookie(response, token)
    return user


@router.post("/login", response_model=UserOut | TwoFactorRequired)
@limiter.limit("10/minute")
def login(request: Request, data: UserLogin, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == data.username).first()
    if not user or not user.hashed_password or not verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if begin_login(db, user, response):
        return TwoFactorRequired()

    token = _create_session(db, user)
    _set_auth_cookie(response, token)
    return user


@router.post("/logout")
def logout(
    response: Response,
    candlr_token: Optional[str] = Cookie(default=None),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if candlr_token:
        decoded = decode_token(candlr_token)
        if decoded:
            _, jti = decoded
            db.query(UserSession).filter(UserSession.jti == jti).delete()
            db.commit()
    response.delete_cookie(key=COOKIE_NAME)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/change-password")
def change_password(
    data: ChangePassword,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user.hashed_password:
        raise HTTPException(status_code=400, detail="Password login is not available for this account")
    if not verify_password(data.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    current_user.hashed_password = hash_password(data.new_password)
    db.commit()
    return {"ok": True}


@router.delete("/me")
def delete_account(
    data: AccountDelete,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.hashed_password:
        if not data.password or not verify_password(data.password, current_user.hashed_password):
            raise HTTPException(status_code=400, detail="Incorrect password")

    # Cascading the DB rows (via the relationships in models.py) doesn't
    # touch image files on disk, so those have to be cleaned up explicitly.
    for person in current_user.people:
        if person.image_filename:
            delete_image(person.image_filename)

    db.delete(current_user)
    db.commit()
    response.delete_cookie(key=COOKIE_NAME)
    return {"ok": True}
