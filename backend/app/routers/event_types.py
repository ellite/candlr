from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user
from ..models import EventType, User
from ..schemas import EventTypeCreate, EventTypeOut, EventTypeReorder

router = APIRouter(prefix="/event-types", tags=["event-types"])


@router.get("", response_model=list[EventTypeOut])
def list_event_types(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return (
        db.query(EventType)
        .filter(EventType.user_id == current_user.id)
        .order_by(EventType.sort_order)
        .all()
    )


@router.post("", response_model=EventTypeOut, status_code=201)
def create_event_type(
    data: EventTypeCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    max_order = (
        db.query(func.max(EventType.sort_order)).filter(EventType.user_id == current_user.id).scalar()
    )
    event_type = EventType(
        user_id=current_user.id,
        name=data.name.strip(),
        is_default=False,
        sort_order=(max_order if max_order is not None else -1) + 1,
    )
    db.add(event_type)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="You already have an event type with that name")
    db.refresh(event_type)
    return event_type


# Registered before /{event_type_id} so "reorder" isn't swallowed by that
# route's int path param first.
@router.put("/reorder", response_model=list[EventTypeOut])
def reorder_event_types(
    data: EventTypeReorder, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    event_types = db.query(EventType).filter(EventType.user_id == current_user.id).all()
    by_id = {et.id: et for et in event_types}
    if set(data.ids) != set(by_id.keys()):
        raise HTTPException(status_code=400, detail="ids must match your full set of event types")
    for index, event_type_id in enumerate(data.ids):
        by_id[event_type_id].sort_order = index
    db.commit()
    return (
        db.query(EventType)
        .filter(EventType.user_id == current_user.id)
        .order_by(EventType.sort_order)
        .all()
    )


@router.put("/{event_type_id}", response_model=EventTypeOut)
def update_event_type(
    event_type_id: int,
    data: EventTypeCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    event_type = (
        db.query(EventType).filter(EventType.id == event_type_id, EventType.user_id == current_user.id).first()
    )
    if not event_type:
        raise HTTPException(status_code=404, detail="Event type not found")
    event_type.name = data.name.strip()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="You already have an event type with that name")
    db.refresh(event_type)
    return event_type


@router.delete("/{event_type_id}")
def delete_event_type(
    event_type_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    event_type = (
        db.query(EventType).filter(EventType.id == event_type_id, EventType.user_id == current_user.id).first()
    )
    if not event_type:
        raise HTTPException(status_code=404, detail="Event type not found")
    if event_type.is_default:
        raise HTTPException(status_code=400, detail="Built-in event types cannot be deleted")
    # Cascades to delete every event of this type (see the relationship in
    # models.py); the frontend warns about this before calling delete.
    db.delete(event_type)
    db.commit()
    return {"ok": True}
