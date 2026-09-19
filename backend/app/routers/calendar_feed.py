import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..data_transfer import load_user_people
from ..database import get_db
from ..dependencies import get_current_user
from ..ical import build_calendar
from ..models import User

router = APIRouter(prefix="/calendar", tags=["calendar"])


class FeedOut(BaseModel):
    token: Optional[str]


@router.get("/feed", response_model=FeedOut)
def get_feed(current_user: User = Depends(get_current_user)):
    return FeedOut(token=current_user.calendar_token)


@router.post("/feed", response_model=FeedOut)
def create_or_rotate_feed(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Turns the feed on, or replaces the token if it's already on (which
    stops every existing subscription from updating)."""
    current_user.calendar_token = secrets.token_urlsafe(32)
    db.commit()
    return FeedOut(token=current_user.calendar_token)


@router.delete("/feed", response_model=FeedOut)
def disable_feed(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    current_user.calendar_token = None
    db.commit()
    return FeedOut(token=None)


# Unauthenticated on purpose: calendar apps can't send the session cookie, so
# the unguessable token in the URL is the credential.
@router.get("/feed/{token}.ics")
def read_feed(token: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.calendar_token == token).first()
    if not user:
        raise HTTPException(status_code=404, detail="Not found")
    body = build_calendar(load_user_people(db, user.id), user.id)
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={"Cache-Control": "no-cache", "Content-Disposition": 'inline; filename="candlr.ics"'},
    )
