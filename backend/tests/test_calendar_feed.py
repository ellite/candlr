import os
import unittest

os.environ.setdefault("SECRET_KEY", "calendar-feed-tests-only")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.dependencies import get_current_user
from app.ical import fold_line
from app.models import Event, EventType, Person, User
from app.routers.calendar_feed import router
from app.seed import seed_default_event_types


def unfold(text: str) -> str:
    return text.replace("\r\n ", "")


class CalendarFeedTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as db:
            db.add_all([User(id=1, username="Tester", email="one@example.com"), User(id=2, username="Other", email="two@example.com")])
            db.commit()
            seed_default_event_types(db, 1)
            seed_default_event_types(db, 2)
        app = FastAPI()
        app.include_router(router)
        self.current_user_id = 1

        def get_test_db():
            with self.sessions() as db:
                yield db

        # Shares the request's session, like the real get_current_user, so
        # changes made to the user in a route are actually committed.
        def get_test_user(db=Depends(get_db)):
            return db.get(User, self.current_user_id)

        app.dependency_overrides[get_db] = get_test_db
        app.dependency_overrides[get_current_user] = get_test_user
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def _add(self, user_id, name, month, day, year=None, type_index=0, notes=None):
        with self.sessions() as db:
            event_type = db.query(EventType).filter_by(user_id=user_id).order_by(EventType.sort_order).all()[type_index]
            person = Person(user_id=user_id, name=name)
            db.add(person)
            db.flush()
            db.add(Event(person_id=person.id, event_type_id=event_type.id, month=month, day=day, year=year, year_known=year is not None, notes=notes))
            db.commit()

    def _feed(self):
        token = self.client.post("/calendar/feed").json()["token"]
        res = self.client.get(f"/calendar/feed/{token}.ics")
        self.assertEqual(res.status_code, 200)
        return token, res

    def test_token_lifecycle(self):
        self.assertIsNone(self.client.get("/calendar/feed").json()["token"])
        first, _ = self._feed()
        self.assertEqual(self.client.get("/calendar/feed").json()["token"], first)

        second = self.client.post("/calendar/feed").json()["token"]
        self.assertNotEqual(first, second)
        self.assertEqual(self.client.get(f"/calendar/feed/{first}.ics").status_code, 404)
        self.assertEqual(self.client.get(f"/calendar/feed/{second}.ics").status_code, 200)

        self.assertIsNone(self.client.delete("/calendar/feed").json()["token"])
        self.assertEqual(self.client.get(f"/calendar/feed/{second}.ics").status_code, 404)

    def test_unknown_token_is_404_and_needs_no_login(self):
        self.assertEqual(self.client.get("/calendar/feed/nope.ics").status_code, 404)
        # A token-less account (feed off) must not match a null lookup.
        self.assertEqual(self.client.get("/calendar/feed/None.ics").status_code, 404)

    def test_feed_content(self):
        self._add(1, "Alex", 3, 5, 1990, notes="Likes tea, cake; and\nbooks")
        self._add(1, "Sam", 7, 4, None, type_index=1)
        self._add(1, "Leap", 2, 29, 2000)
        self._add(2, "Stranger", 1, 1, 1980)
        _, res = self._feed()

        self.assertTrue(res.headers["content-type"].startswith("text/calendar"))
        body = res.text
        self.assertTrue(body.startswith("BEGIN:VCALENDAR\r\n") and body.endswith("END:VCALENDAR\r\n"))
        self.assertNotIn("\n", body.replace("\r\n", ""))
        text = unfold(body)
        self.assertEqual(text.count("BEGIN:VEVENT"), 3)
        self.assertNotIn("Stranger", text)
        self.assertIn("SUMMARY:Alex's Birthday", text)
        self.assertIn("DTSTART;VALUE=DATE:19900305", text)
        self.assertIn("DTEND;VALUE=DATE:19900306", text)
        self.assertIn("DESCRIPTION:Likes tea\\, cake\; and\\nbooks", text)
        self.assertIn("SUMMARY:Sam's Anniversary", text)
        self.assertIn("DTSTART;VALUE=DATE:20000704", text)
        self.assertIn("BYMONTH=2;BYMONTHDAY=28,29;BYSETPOS=-1", text)
        self.assertEqual(text.count("RRULE:FREQ=YEARLY"), 3)
        self.assertEqual(len({line for line in text.split("\r\n") if line.startswith("UID:")}), 3)

    def test_feed_is_empty_but_valid_without_cards(self):
        _, res = self._feed()
        self.assertNotIn("BEGIN:VEVENT", res.text)
        self.assertIn("END:VCALENDAR", res.text)

    def test_long_lines_fold_at_75_octets_without_splitting_characters(self):
        note = "Ñandú ünïcode 🎂 " * 30
        self._add(1, "Alex", 3, 5, 1990, notes=note)
        _, res = self._feed()
        for line in res.content.decode("utf-8").split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75)
        self.assertIn(note.strip().replace(",", "\\,"), unfold(res.text))

    def test_fold_line_short_line_untouched(self):
        self.assertEqual(fold_line("SUMMARY:short"), "SUMMARY:short")


if __name__ == "__main__":
    unittest.main()
