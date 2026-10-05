import os
import unittest

os.environ.setdefault("SECRET_KEY", "cadence-tests-only-not-a-real-secret-key")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.dependencies import get_current_user
from app.models import User
from app.routers import event_types, people
from app.seed import seed_default_event_types


class CadenceApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as db:
            db.add(User(id=1, username="Tester", email="one@example.com"))
            db.commit()
            seed_default_event_types(db, 1)
        app = FastAPI()
        app.include_router(event_types.router)
        app.include_router(people.router)

        def get_test_db():
            with self.sessions() as db:
                yield db

        def get_test_user():
            with self.sessions() as db:
                return db.get(User, 1)

        app.dependency_overrides[get_db] = get_test_db
        app.dependency_overrides[get_current_user] = get_test_user
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def _type(self, name, interval=None, unit=None):
        body = {"name": name}
        if interval is not None:
            body["interval"] = interval
        if unit is not None:
            body["unit"] = unit
        return self.client.post("/event-types", json=body)

    def _card(self, type_id, year, name="Alex"):
        return self.client.post(
            "/people",
            json={"name": name, "event": {"event_type_id": type_id, "month": 3, "day": 8, "year": year, "year_known": year is not None}},
        )

    def test_types_default_to_yearly_and_accept_any_cadence(self):
        defaults = self.client.get("/event-types").json()
        self.assertEqual([(t["interval"], t["unit"]) for t in defaults], [(1, "year"), (1, "year")])
        for interval, unit in [(1, "week"), (37, "day"), (7, "month"), (4, "week"), (5, "year")]:
            res = self._type(f"Every {interval} {unit}", interval, unit)
            self.assertEqual(res.status_code, 201, res.text)
            self.assertEqual((res.json()["interval"], res.json()["unit"]), (interval, unit))
        self.assertEqual(self._type("Plain").json()["unit"], "year")

    def test_invalid_cadences_are_rejected(self):
        for interval, unit in [(0, "day"), (-1, "week"), (1001, "day"), (2, "fortnight")]:
            self.assertEqual(self._type("Bad", interval, unit).status_code, 422, (interval, unit))

    def test_dates_of_a_repeating_type_need_a_year(self):
        kind = self._type("Check-in", 2, "week").json()
        res = self._card(kind["id"], None)
        self.assertEqual(res.status_code, 400)
        self.assertIn("full start date", res.json()["detail"])
        ok = self._card(kind["id"], 2026)
        self.assertEqual(ok.status_code, 201, ok.text)
        event = ok.json()["events"][0]
        self.assertEqual(event["event_type"]["unit"], "week")
        self.assertGreaterEqual(event["days_until"], 0)
        # Types that only depend on month and day still accept undated cards.
        monthly = self._type("Monthiversary", 1, "month").json()
        self.assertEqual(self._card(monthly["id"], None, "Mo").status_code, 201)

    def test_changing_a_type_checks_its_existing_dates(self):
        birthday = self.client.get("/event-types").json()[0]
        self.assertEqual(self._card(birthday["id"], None).status_code, 201)
        res = self.client.put(f"/event-types/{birthday['id']}", json={"name": "Birthday", "interval": 5, "unit": "year"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("no year", res.json()["detail"])
        # Nothing changed.
        self.assertEqual(self.client.get("/event-types").json()[0]["interval"], 1)
        ok = self.client.put(f"/event-types/{birthday['id']}", json={"name": "Birthday", "interval": 1, "unit": "month"})
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_renaming_keeps_the_cadence(self):
        kind = self._type("Check-in", 3, "week").json()
        res = self.client.put(f"/event-types/{kind['id']}", json={"name": "Weekly-ish"})
        self.assertEqual((res.json()["name"], res.json()["interval"], res.json()["unit"]), ("Weekly-ish", 3, "week"))

    def test_moving_dates_to_a_type_that_needs_a_year_is_blocked(self):
        birthday, anniversary = self.client.get("/event-types").json()
        card = self._card(birthday["id"], None).json()
        strict = self._type("Check-in", 2, "week").json()
        res = self.client.post(
            "/people/bulk",
            json={"ids": [card["id"]], "action": "set_type", "from_event_type_id": birthday["id"], "to_event_type_id": strict["id"]},
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("Alex", res.json()["detail"])
        self.assertEqual(self.client.get(f"/people/{card['id']}").json()["events"][0]["event_type"]["id"], birthday["id"])
        edit = self.client.put(
            f"/events/{card['events'][0]['id']}",
            json={"event_type_id": strict["id"], "month": 3, "day": 8, "year": None, "year_known": False},
        )
        self.assertEqual(edit.status_code, 400)


if __name__ == "__main__":
    unittest.main()
