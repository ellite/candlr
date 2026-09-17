from sqlalchemy.orm import Session

from .models import EventType

DEFAULT_EVENT_TYPES = ["Birthday", "Anniversary"]


def seed_default_event_types(db: Session, user_id: int) -> None:
    for order, name in enumerate(DEFAULT_EVENT_TYPES):
        db.add(EventType(user_id=user_id, name=name, is_default=True, sort_order=order))
    db.commit()
