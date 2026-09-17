import os
import unittest

os.environ.setdefault("SECRET_KEY", "event-type-tests-only")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.database import Base
from app.models import User, EventType, Person, Event
from app.routers.event_types import delete_event_type
from app.seed import seed_default_event_types


class EventTypeDeletionTests(unittest.TestCase):
    def test_defaults_protected_custom_deletable_and_ownership_checked(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        try:
            with Session(engine) as db:
                user = User(username="test", email="test@example.com")
                db.add(user)
                db.commit()
                seed_default_event_types(db, user.id)
                defaults = db.query(EventType).all()
                person = Person(user_id=user.id, name="Alex")
                db.add(person)
                db.flush()
                db.add(Event(person_id=person.id, event_type_id=defaults[0].id, month=1, day=1))
                db.commit()
                for event_type in defaults:
                    with self.assertRaises(HTTPException) as error:
                        delete_event_type(event_type.id, user, db)
                    self.assertEqual(error.exception.status_code, 400)
                defaults[0].name = "Renamed birthday"
                db.commit()
                with self.assertRaises(HTTPException):
                    delete_event_type(defaults[0].id, user, db)
                self.assertEqual(db.query(Event).count(), 1)
                other = User(username="other", email="other@example.com")
                custom = EventType(user_id=user.id, name="Graduation", is_default=False)
                db.add_all([other, custom])
                db.commit()
                with self.assertRaises(HTTPException) as error:
                    delete_event_type(custom.id, other, db)
                self.assertEqual(error.exception.status_code, 404)
                self.assertEqual(delete_event_type(custom.id, user, db), {"ok": True})
                self.assertEqual(db.query(EventType).count(), 2)
        finally:
            engine.dispose()
