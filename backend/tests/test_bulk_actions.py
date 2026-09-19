import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "bulk-action-tests-only")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.dependencies import get_current_user
from app.models import Event, EventType, Person, ReminderDelivery, User
from app.routers.people import router


class BulkActionTests(unittest.TestCase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        self.previous_data_dir = settings.data_dir
        settings.data_dir = Path(self.storage.name)
        settings.images_dir.mkdir(parents=True, exist_ok=True)

        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as db:
            db.add_all([User(id=1, username="Tester", email="one@example.com"), User(id=2, username="Other", email="two@example.com")])
            db.add_all(
                [
                    EventType(id=1, user_id=1, name="Birthday"),
                    EventType(id=2, user_id=1, name="Anniversary"),
                    EventType(id=3, user_id=1, name="Bday"),
                    EventType(id=4, user_id=2, name="Private"),
                ]
            )
            db.commit()
            # 1: Alex (birthday + anniversary, with photo), 2: Sam (birthday), 3: Kim (anniversary), 10: someone else's
            self.photo = settings.images_dir / "alex.jpg"
            self.photo.write_bytes(b"jpeg")
            for pid, name, user, image, events in [
                (1, "Alex", 1, "alex.jpg", [(1, 3, 5), (2, 6, 1)]),
                (2, "Sam", 1, None, [(1, 7, 4)]),
                (3, "Kim", 1, None, [(2, 8, 8)]),
                (10, "Stranger", 2, None, [(4, 1, 1)]),
            ]:
                db.add(Person(id=pid, user_id=user, name=name, image_filename=image))
                db.flush()
                for type_id, month, day in events:
                    db.add(Event(person_id=pid, event_type_id=type_id, month=month, day=day, year_known=False))
            db.flush()
            first_event = db.query(Event).filter_by(person_id=1).first()
            db.add(ReminderDelivery(event_id=first_event.id, occurrence_date=__import__("datetime").date(2026, 3, 5), channel="email"))
            db.commit()

        app = FastAPI()
        app.include_router(router)

        def get_test_db():
            with self.sessions() as db:
                yield db

        def get_test_user(db=Depends(get_db)):
            return db.get(User, 1)

        app.dependency_overrides[get_db] = get_test_db
        app.dependency_overrides[get_current_user] = get_test_user
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()
        settings.data_dir = self.previous_data_dir
        self.storage.cleanup()

    def _bulk(self, **body):
        return self.client.post("/people/bulk", json=body)

    def _types(self):
        with self.sessions() as db:
            return {(e.person_id, e.month): e.event_type_id for e in db.query(Event).all()}

    def test_delete_removes_cards_dates_deliveries_and_photos_but_only_own(self):
        res = self._bulk(action="delete", ids=[1, 3, 10, 999])
        self.assertEqual(res.json(), {"people": 2, "dates": 3})
        with self.sessions() as db:
            self.assertEqual(sorted(p.name for p in db.query(Person).all()), ["Sam", "Stranger"])
            self.assertEqual(db.query(Event).count(), 2)
            self.assertEqual(db.query(ReminderDelivery).count(), 0)
        self.assertFalse(self.photo.exists())

    def test_set_type_swaps_only_the_chosen_type_on_chosen_cards(self):
        res = self._bulk(action="set_type", ids=[1, 3], from_event_type_id=1, to_event_type_id=3)
        self.assertEqual(res.json(), {"people": 1, "dates": 1})
        types = self._types()
        self.assertEqual(types[(1, 3)], 3)  # Alex's birthday moved
        self.assertEqual(types[(1, 6)], 2)  # Alex's anniversary untouched
        self.assertEqual(types[(2, 7)], 1)  # Sam not selected
        self.assertEqual(types[(3, 8)], 2)

    def test_set_type_validates_types(self):
        self.assertEqual(self._bulk(action="set_type", ids=[1], from_event_type_id=1, to_event_type_id=1).status_code, 400)
        self.assertEqual(self._bulk(action="set_type", ids=[1], from_event_type_id=1, to_event_type_id=4).status_code, 400)
        self.assertEqual(self._bulk(action="set_type", ids=[1], from_event_type_id=99, to_event_type_id=2).status_code, 400)
        self.assertEqual(self._types()[(1, 3)], 1)

    def test_set_notify_applies_to_every_date_on_chosen_cards(self):
        res = self._bulk(action="set_notify", ids=[1, 2, 10], notify=False)
        self.assertEqual(res.json(), {"people": 2, "dates": 3})
        with self.sessions() as db:
            by_person = {(e.person_id): e.notify for e in db.query(Event).order_by(Event.id)}
            self.assertFalse(by_person[1] or by_person[2])
            self.assertTrue(by_person[3])
            self.assertTrue(by_person[10])  # other user's card untouched
        self._bulk(action="set_notify", ids=[1], notify=True)
        with self.sessions() as db:
            self.assertTrue(all(e.notify for e in db.query(Event).filter_by(person_id=1)))

    def test_rejects_bad_requests(self):
        self.assertEqual(self._bulk(action="delete", ids=[]).status_code, 422)
        self.assertEqual(self._bulk(action="nuke", ids=[1]).status_code, 422)
        self.assertEqual(self._bulk(action="set_notify", ids=[1]).status_code, 422)
        self.assertEqual(self._bulk(action="delete", ids=list(range(5001))).status_code, 422)
        with self.sessions() as db:
            self.assertEqual(db.query(Person).count(), 4)

    def test_bulk_route_does_not_shadow_person_routes(self):
        self.assertEqual(self.client.get("/people/1").status_code, 200)


if __name__ == "__main__":
    unittest.main()
