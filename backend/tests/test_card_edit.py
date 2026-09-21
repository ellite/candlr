import io
import os
import tempfile
import unittest

os.environ.setdefault("SECRET_KEY", "card-edit-tests-only")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.dependencies import get_current_user
from app.models import User, EventType
from app.routers.people import router


class CardEditTests(unittest.TestCase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        self.previous_data_dir = settings.data_dir
        from pathlib import Path
        settings.data_dir = Path(self.storage.name)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as db:
            db.add_all([User(id=1, username="Tester", email="one@example.com"), User(id=2, username="Other", email="two@example.com")])
            db.add_all([EventType(id=1, user_id=1, name="Birthday"), EventType(id=2, user_id=1, name="Anniversary"), EventType(id=3, user_id=2, name="Private")])
            db.commit()
        self.app = FastAPI()
        self.app.include_router(router)

        def get_test_db():
            with self.sessions() as db:
                yield db

        def get_test_user():
            with self.sessions() as db:
                return db.get(User, 1)

        self.app.dependency_overrides[get_db] = get_test_db
        self.app.dependency_overrides[get_current_user] = get_test_user
        self.client = TestClient(self.app)
        self.app.get("/auth/me")(lambda: {"username": "Tester"})
        self.app.get("/event-types")(lambda: [{"id": 1, "name": "Birthday"}, {"id": 2, "name": "Anniversary"}])
        self.payload = {"event_type_id": 1, "month": 3, "day": 5, "year": 2000, "year_known": True, "notes": "Birthday notes"}
        self.person = self.client.post("/people", json={"name": "Alex", "event": self.payload}).json()
        self.person = self.client.post(f"/people/{self.person['id']}/events", json={**self.payload, "event_type_id": 2, "notes": "Anniversary notes"}).json()

    def tearDown(self):
        self.client.close()
        self.engine.dispose()
        settings.data_dir = self.previous_data_dir
        self.storage.cleanup()

    def edits(self):
        return [{**self.payload, "id": event["id"], "event_type_id": event["event_type"]["id"]} for event in self.person["events"]]

    def test_updates_name_and_multiple_dates_without_replacing_ids(self):
        events = self.edits()
        events[0].update(month=2, day=29, year_known=False)
        events[1].update(notes="Updated notes")
        response = self.client.put("/people/1", json={"name": "Updated", "events": events})
        self.assertEqual(response.status_code, 200)
        saved = response.json()
        self.assertEqual(saved["name"], "Updated")
        self.assertEqual([e["id"] for e in saved["events"]], [e["id"] for e in events])
        self.assertIsNone(saved["events"][0]["year"])
        self.assertEqual(saved["events"][1]["notes"], "Updated notes")

    def test_invalid_date_or_foreign_event_type_does_not_save_name(self):
        for invalid in [{"month": 2, "day": 30}, {"event_type_id": 3}]:
            with self.subTest(invalid=invalid):
                events = self.edits()
                events[-1].update(invalid)
                response = self.client.put("/people/1", json={"name": "Should not save", "events": events})
                self.assertIn(response.status_code, [400, 422])
                self.assertEqual(self.client.get("/people/1").json(), self.person)

    def test_rejects_event_from_another_card_and_duplicate_ids(self):
        other = self.client.post("/people", json={"name": "Other card", "event": self.payload}).json()
        events = self.edits()
        for invalid_events in [[{**events[0], "id": other["events"][0]["id"]}], [events[0], events[0]]]:
            response = self.client.put("/people/1", json={"name": "Should not save", "events": invalid_events})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(self.client.get("/people/1").json(), self.person)

    def test_name_only_update_preserves_dates_and_photo_can_be_replaced_and_removed(self):
        data = io.BytesIO()
        Image.new("RGB", (30, 30), "red").save(data, format="PNG")
        first = self.client.post("/people/1/image", files={"file": ("photo.png", data.getvalue(), "image/png")})
        self.assertEqual(first.status_code, 200)
        second = self.client.post("/people/1/image", files={"file": ("photo.png", data.getvalue(), "image/png")}).json()
        self.assertNotEqual(first.json()["image_url"], second["image_url"])
        renamed = self.client.put("/people/1", json={"name": "Renamed"}).json()
        self.assertEqual(renamed["events"], self.person["events"])
        self.assertEqual(renamed["image_url"], second["image_url"])
        image_response = self.client.get(second["image_url"])
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response.headers["cache-control"], "private, max-age=31536000, immutable")
        self.assertIsNone(self.client.delete("/people/1/image").json()["image_url"])


if __name__ == "__main__":
    unittest.main()
