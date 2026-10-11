import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from ..config import settings
from ..data_transfer import load_user_people
from ..database import get_db
from ..dependencies import get_current_user
from ..events_logic import needs_start_year
from ..models import EventType, Person, Share, User
from ..schemas import EventTypeOut

router = APIRouter(tags=["shares"])


class ShareCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    event_type_ids: list[int] = Field(min_length=1, max_length=200)
    show_photos: bool = False
    show_ages: bool = False


class ShareOut(BaseModel):
    id: int
    name: str
    token: str
    show_photos: bool
    show_ages: bool
    event_types: list[EventTypeOut]

    model_config = {"from_attributes": True}


class SharedEvent(BaseModel):
    id: int
    event_type: EventTypeOut
    month: int
    day: int
    year: Optional[int]
    year_known: bool


class SharedPerson(BaseModel):
    id: int
    name: str
    image_url: Optional[str]
    events: list[SharedEvent]


class SharedCalendar(BaseModel):
    name: str
    shared_by: str
    show_ages: bool
    people: list[SharedPerson]


def _visible_year(event, show_ages: bool) -> Optional[int]:
    """The year a shared page may see. Without show_ages it is dropped, except
    where the date's cadence can't be placed on the calendar without it."""
    if not event.year_known or event.year is None:
        return None
    if show_ages or needs_start_year(event.event_type.interval, event.event_type.unit):
        return event.year
    return None


def _get_share(db: Session, user_id: int, share_id: int) -> Share:
    share = db.query(Share).filter(Share.id == share_id, Share.user_id == user_id).first()
    if not share:
        raise HTTPException(status_code=404, detail="Share not found")
    return share


@router.get("/shares", response_model=list[ShareOut])
def list_shares(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return (
        db.query(Share)
        .options(joinedload(Share.event_types))
        .filter(Share.user_id == current_user.id)
        .order_by(Share.created_at, Share.id)
        .all()
    )


@router.post("/shares", response_model=ShareOut, status_code=201)
def create_share(data: ShareCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name is required")
    ids = set(data.event_type_ids)
    types = db.query(EventType).filter(EventType.user_id == current_user.id, EventType.id.in_(ids)).all()
    if len(types) != len(ids):
        raise HTTPException(status_code=422, detail="Unknown event type")
    share = Share(
        user_id=current_user.id,
        name=name,
        token=secrets.token_urlsafe(32),
        show_photos=data.show_photos,
        show_ages=data.show_ages,
        event_types=sorted(types, key=lambda t: t.sort_order),
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    return share


@router.delete("/shares/{share_id}", status_code=204)
def delete_share(share_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.delete(_get_share(db, current_user.id, share_id))
    db.commit()


# Unauthenticated on purpose: the unguessable token in the URL is the
# credential. Never returns notes, reminder flags or anything outside the
# share's event types.
def _find_share(db: Session, token: str) -> Share:
    share = db.query(Share).filter(Share.token == token).first()
    if not share:
        raise HTTPException(status_code=404, detail="Not found")
    return share


@router.get("/share/{token}", response_model=SharedCalendar)
def read_share(token: str, db: Session = Depends(get_db)):
    share = _find_share(db, token)
    allowed = {et.id for et in share.event_types}
    people = []
    for person in load_user_people(db, share.user_id):
        events = [e for e in person.events if e.event_type_id in allowed]
        if not events:
            continue
        people.append(
            SharedPerson(
                id=person.id,
                name=person.name,
                image_url=(
                    f"/share/{token}/images/{person.image_filename}"
                    if share.show_photos and person.image_filename
                    else None
                ),
                events=[
                    SharedEvent(
                        id=e.id,
                        event_type=EventTypeOut.model_validate(e.event_type),
                        month=e.month,
                        day=e.day,
                        year=_visible_year(e, share.show_ages),
                        year_known=_visible_year(e, share.show_ages) is not None,
                    )
                    for e in events
                ],
            )
        )
    owner = db.get(User, share.user_id)
    return SharedCalendar(name=share.name, shared_by=owner.username, show_ages=share.show_ages, people=people)


@router.get("/share/{token}/images/{filename}")
def read_share_image(token: str, filename: str, db: Session = Depends(get_db)):
    share = _find_share(db, token)
    if not share.show_photos:
        raise HTTPException(status_code=404, detail="Not found")
    allowed = {et.id for et in share.event_types}
    person = (
        db.query(Person)
        .options(joinedload(Person.events))
        .filter(Person.user_id == share.user_id, Person.image_filename == filename)
        .first()
    )
    # Only photos of cards the share actually shows.
    if not person or not any(e.event_type_id in allowed for e in person.events):
        raise HTTPException(status_code=404, detail="Not found")
    path = settings.images_dir / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(str(path), headers={"Cache-Control": "private, max-age=3600"})
