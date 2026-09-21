from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, joinedload

from ..auth import verify_password
from ..config import settings
from ..database import get_db
from ..dependencies import get_current_user
from ..events_logic import days_until
from ..images import ImageError, download_image, normalize_image, save_image, delete_image
from ..models import Event, EventType, Person, User
from ..schemas import (
    AccountDelete,
    BulkRequest,
    BulkResult,
    EventInput,
    ImageUrlRequest,
    PersonCreate,
    PersonOut,
    PersonUpdate,
)

router = APIRouter(tags=["people"])


def _get_event_type(db: Session, user_id: int, event_type_id: int) -> EventType:
    event_type = db.query(EventType).filter(EventType.id == event_type_id, EventType.user_id == user_id).first()
    if not event_type:
        raise HTTPException(status_code=400, detail="Unknown event type")
    return event_type


def _get_person(db: Session, user_id: int, person_id: int) -> Person:
    person = (
        db.query(Person)
        .options(joinedload(Person.events).joinedload(Event.event_type))
        .filter(Person.id == person_id, Person.user_id == user_id)
        .first()
    )
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return person


def _get_event(db: Session, user_id: int, event_id: int) -> Event:
    event = (
        db.query(Event)
        .join(Person, Event.person_id == Person.id)
        .filter(Event.id == event_id, Person.user_id == user_id)
        .first()
    )
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.get("/people", response_model=list[PersonOut])
def list_people(
    sort: Literal["upcoming", "name"] = "upcoming",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    people = (
        db.query(Person)
        .options(joinedload(Person.events).joinedload(Event.event_type))
        .filter(Person.user_id == current_user.id)
        .all()
    )
    if sort == "name":
        people.sort(key=lambda p: p.name.lower())
    else:
        people.sort(key=lambda p: min((days_until(e.month, e.day) for e in p.events), default=9999))
    return people


@router.delete("/people")
def delete_all_people(
    data: AccountDelete, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    if current_user.hashed_password:
        if not data.password or not verify_password(data.password, current_user.hashed_password):
            raise HTTPException(status_code=400, detail="Incorrect password")

    for person in current_user.people:
        if person.image_filename:
            delete_image(person.image_filename)
        db.delete(person)
    db.commit()
    return {"ok": True}


@router.post("/people/bulk", response_model=BulkResult)
def bulk_people(data: BulkRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Applies one action to many of the caller's cards. Ids that don't
    belong to the caller (or no longer exist) are ignored, and the result
    counts what was actually changed."""
    people = (
        db.query(Person)
        .options(joinedload(Person.events))
        .filter(Person.user_id == current_user.id, Person.id.in_(set(data.ids)))
        .all()
    )

    if data.action == "delete":
        result = BulkResult(people=len(people), dates=sum(len(person.events) for person in people))
        filenames = [person.image_filename for person in people if person.image_filename]
        for person in people:
            db.delete(person)
        db.commit()
        # After the commit, so a failed delete never leaves rows pointing at missing files.
        for filename in filenames:
            delete_image(filename)
        return result

    if data.action == "set_type":
        if data.from_event_type_id == data.to_event_type_id:
            raise HTTPException(status_code=400, detail="Choose two different event types")
        _get_event_type(db, current_user.id, data.from_event_type_id)
        _get_event_type(db, current_user.id, data.to_event_type_id)
        changed_people = set()
        changed_dates = 0
        for person in people:
            for event in person.events:
                if event.event_type_id == data.from_event_type_id:
                    event.event_type_id = data.to_event_type_id
                    changed_people.add(person.id)
                    changed_dates += 1
        db.commit()
        return BulkResult(people=len(changed_people), dates=changed_dates)

    dates = 0
    for person in people:
        for event in person.events:
            event.notify = data.notify
            dates += 1
    db.commit()
    return BulkResult(people=len(people), dates=dates)


@router.post("/people", response_model=PersonOut, status_code=201)
def create_person(data: PersonCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _get_event_type(db, current_user.id, data.event.event_type_id)

    person = Person(user_id=current_user.id, name=data.name.strip())
    db.add(person)
    db.flush()
    db.add(Event(person_id=person.id, **data.event.model_dump()))
    db.commit()
    return _get_person(db, current_user.id, person.id)


@router.get("/people/{person_id}", response_model=PersonOut)
def get_person(person_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _get_person(db, current_user.id, person_id)


@router.put("/people/{person_id}", response_model=PersonOut)
def update_person(
    person_id: int,
    data: PersonUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    person = _get_person(db, current_user.id, person_id)
    if data.events is not None:
        events_by_id = {event.id: event for event in person.events}
        ids = [event.id for event in data.events]
        if len(ids) != len(set(ids)) or any(event_id not in events_by_id for event_id in ids):
            raise HTTPException(status_code=400, detail="Invalid card dates")
        # Validate ownership and event types before changing any fields.
        for event_data in data.events:
            _get_event_type(db, current_user.id, event_data.event_type_id)
        for event_data in data.events:
            for field, value in event_data.model_dump(exclude={"id"}).items():
                setattr(events_by_id[event_data.id], field, value)
    person.name = data.name.strip()
    db.commit()
    return _get_person(db, current_user.id, person_id)


@router.delete("/people/{person_id}")
def delete_person(person_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    person = _get_person(db, current_user.id, person_id)
    if person.image_filename:
        delete_image(person.image_filename)
    db.delete(person)
    db.commit()
    return {"ok": True}


@router.post("/people/{person_id}/events", response_model=PersonOut, status_code=201)
def add_event(
    person_id: int, data: EventInput, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    person = _get_person(db, current_user.id, person_id)
    _get_event_type(db, current_user.id, data.event_type_id)
    db.add(Event(person_id=person.id, **data.model_dump()))
    db.commit()
    return _get_person(db, current_user.id, person_id)


@router.put("/events/{event_id}", response_model=PersonOut)
def update_event(
    event_id: int, data: EventInput, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    event = _get_event(db, current_user.id, event_id)
    _get_event_type(db, current_user.id, data.event_type_id)
    for field, value in data.model_dump().items():
        setattr(event, field, value)
    db.commit()
    return _get_person(db, current_user.id, event.person_id)


@router.delete("/events/{event_id}")
def delete_event(event_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    event = _get_event(db, current_user.id, event_id)
    person_id = event.person_id
    if len(event.person.events) <= 1:
        raise HTTPException(status_code=400, detail="A person needs at least one date - delete the card instead")
    db.delete(event)
    db.commit()
    return _get_person(db, current_user.id, person_id)


@router.post("/people/{person_id}/image", response_model=PersonOut)
def upload_person_image(
    person_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    person = _get_person(db, current_user.id, person_id)
    data = file.file.read()
    try:
        filename = save_image(data)
    except ImageError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if person.image_filename:
        delete_image(person.image_filename)
    person.image_filename = filename
    db.commit()
    return _get_person(db, current_user.id, person_id)


@router.post("/images/preview")
def preview_image_url(data: ImageUrlRequest, current_user: User = Depends(get_current_user)):
    """Fetches and normalizes a URL for the client-side crop editor, without
    saving it anywhere or associating it with a person yet - the editor
    itself is what turns the result into an upload (via
    POST /people/{id}/image), so a person doesn't even need to exist yet
    (the add-a-date form calls this before a Person is created)."""
    try:
        raw = download_image(data.url.strip())
        normalized, ext = normalize_image(raw)
    except ImageError as e:
        raise HTTPException(status_code=400, detail=str(e))
    media_type = "image/png" if ext == "png" else "image/jpeg"
    return Response(content=normalized, media_type=media_type)


@router.delete("/people/{person_id}/image", response_model=PersonOut)
def remove_person_image(
    person_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    person = _get_person(db, current_user.id, person_id)
    if person.image_filename:
        delete_image(person.image_filename)
        person.image_filename = None
        db.commit()
    return _get_person(db, current_user.id, person_id)


@router.get("/images/{filename}")
def get_image(filename: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    person = (
        db.query(Person).filter(Person.image_filename == filename, Person.user_id == current_user.id).first()
    )
    if not person:
        raise HTTPException(status_code=404, detail="Not found")
    path = settings.images_dir / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    # Stored images get a new random filename whenever they are replaced, so
    # their URLs are immutable. Keep them in the browser cache long-term to
    # avoid reloading every card photo on later visits.
    return FileResponse(str(path), headers={"Cache-Control": "private, max-age=31536000, immutable"})
