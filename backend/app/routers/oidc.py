import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import User
from ..schemas import OidcConfigOut, OidcAuthorizeOut, OidcExchangeRequest
from ..seed import seed_default_event_types
from .auth import _create_session, _set_auth_cookie
from ..two_factor import begin_login

router = APIRouter(prefix="/oidc", tags=["oidc"])


@router.get("/config", response_model=OidcConfigOut)
def oidc_config():
    return OidcConfigOut(
        enabled=settings.oidc_enabled,
        provider_name=settings.oidc_provider_name,
        disable_password_login=settings.oidc_disable_password_login,
    )


@router.get("/authorize", response_model=OidcAuthorizeOut)
def oidc_authorize():
    """Returns the provider's authorization URL and a random state token.

    The frontend SSR layer sets the state as an httpOnly cookie and redirects
    the browser to the provider; the browser never talks to this endpoint
    directly.
    """
    if not settings.oidc_enabled:
        raise HTTPException(status_code=400, detail="OIDC not enabled")

    state = secrets.token_urlsafe(32)
    params = {
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_url,
        "response_type": "code",
        "scope": settings.oidc_scopes,
        "state": state,
    }
    auth_url = f"{settings.oidc_auth_url}?{urlencode(params)}"
    return OidcAuthorizeOut(auth_url=auth_url, state=state)


@router.post("/exchange")
def oidc_exchange(
    payload: OidcExchangeRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    """Exchanges an authorization code (already validated by the frontend)
    for a session, and sets the same auth cookie a normal login would."""
    if not settings.oidc_enabled:
        raise HTTPException(status_code=400, detail="OIDC not enabled")

    try:
        with httpx.Client(timeout=10.0) as client:
            token_resp = client.post(
                settings.oidc_token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": payload.code,
                    "redirect_uri": settings.oidc_redirect_url,
                    "client_id": settings.oidc_client_id,
                    "client_secret": settings.oidc_client_secret,
                },
                headers={"Accept": "application/json"},
            )
            if not token_resp.is_success:
                raise HTTPException(status_code=400, detail="Token exchange failed")

            oidc_access_token = token_resp.json().get("access_token")
            if not oidc_access_token:
                raise HTTPException(status_code=400, detail="No access token in response")

            userinfo_resp = client.get(
                settings.oidc_userinfo_url,
                headers={"Authorization": f"Bearer {oidc_access_token}"},
            )
            if not userinfo_resp.is_success:
                raise HTTPException(status_code=400, detail="Failed to fetch user info")

            userinfo: dict = userinfo_resp.json()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="Provider connection failed")

    identifier = userinfo.get(settings.oidc_identifier_field)
    if not identifier:
        raise HTTPException(
            status_code=400,
            detail=f"Field '{settings.oidc_identifier_field}' not found in user info",
        )

    user = db.query(User).filter(User.email == str(identifier)).first()

    if not user:
        if not settings.oidc_auto_create_users:
            raise HTTPException(status_code=403, detail="No account found for this identity")

        raw_email = userinfo.get("email", str(identifier))
        raw_username = (
            userinfo.get("preferred_username")
            or userinfo.get("name")
            or (raw_email.split("@")[0] if "@" in raw_email else raw_email)
        )
        username = str(raw_username)[:64]

        base = username
        counter = 1
        while db.query(User).filter(User.username == username).first():
            username = f"{base}{counter}"
            counter += 1

        # First user (local or OIDC) becomes admin, same rule as local
        # registration in routers/auth.py.
        is_first_user = db.query(User).count() == 0

        user = User(
            email=str(identifier),
            username=username,
            hashed_password=None,
            is_admin=is_first_user,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        seed_default_event_types(db, user.id)

    if begin_login(db, user, response):
        return {"requires_2fa": True}

    token = _create_session(db, user)
    _set_auth_cookie(response, token)
    return {"ok": True}
