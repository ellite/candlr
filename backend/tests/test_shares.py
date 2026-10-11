import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("SECRET_KEY", "share-tests-only-not-a-real-secret-key")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.dependencies import get_current_user
from app.models import Event, EventType, Person, Share, User
from app.routers import shares
from app.seed import seed_default_event_types


class ShareTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.images = tempfile.TemporaryDirectory()
        with self.sessions() as db:
            db.add_all([User(id=1, username="Tester", email="one@example.com"), User(id=2, username="Other", email="two@example.com")])
            db.commit()
            seed_default_event_types(db, 1)
            seed_default_event_types(db, 2)
        app = FastAPI()
        app.include_router(shares.router)
        self.current_user_id = 1

        def get_test_db():
            with self.sessions() as db:
                yield db

        def get_test_user(db=Depends(get_db)):
            return db.get(User, self.current_user_id)

        app.dependency_overrides[get_db] = get_test_db
        app.dependency_overrides[get_current_user] = get_test_user
        self.client = TestClient(app)
        patcher = mock.patch.object(shares.settings, "data_dir", Path(self.images.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        (Path(self.images.name) / "images").mkdir()

        with self.sessions() as db:
            types = db.query(EventType).filter_by(user_id=1).order_by(EventType.sort_order).all()
            self.birthday, self.anniversary = types[0].id, types[1].id
            self.other_type = db.query(EventType).filter_by(user_id=2).first().id
            ann = Person(user_id=1, name="Ann", image_filename="ann.jpg")
            bob = Person(user_id=1, name="Bob", image_filename="bob.jpg")
            db.add_all([ann, bob])
            db.flush()
            db.add_all(
                [
                    Event(person_id=ann.id, event_type_id=self.birthday, month=3, day=4, year=1990, notes="secret"),
                    Event(person_id=ann.id, event_type_id=self.anniversary, month=6, day=7, notes="private"),
                    Event(person_id=bob.id, event_type_id=self.anniversary, month=8, day=9),
                ]
            )
            db.commit()
        (Path(self.images.name) / "images" / "ann.jpg").write_bytes(b"jpg")
        (Path(self.images.name) / "images" / "bob.jpg").write_bytes(b"jpg")

    def tearDown(self):
        self.client.close()
        self.engine.dispose()
        self.images.cleanup()

    def _create(self, **kwargs):
        body = {"name": "Family", "event_type_ids": [self.birthday], "show_photos": False, "show_ages": False, **kwargs}
        return self.client.post("/shares", json=body)

    def test_create_list_delete(self):
        res = self._create()
        self.assertEqual(res.status_code, 201)
        share = res.json()
        self.assertEqual(share["name"], "Family")
        self.assertGreaterEqual(len(share["token"]), 32)
        self.assertEqual([t["id"] for t in share["event_types"]], [self.birthday])
        self.assertEqual(len(self.client.get("/shares").json()), 1)
        self.assertEqual(self.client.delete(f"/shares/{share['id']}").status_code, 204)
        self.assertEqual(self.client.get("/shares").json(), [])
        self.assertEqual(self.client.get(f"/share/{share['token']}").status_code, 404)

    def test_validation(self):
        self.assertEqual(self._create(event_type_ids=[]).status_code, 422)
        self.assertEqual(self._create(name="   ").status_code, 422)
        self.assertEqual(self._create(event_type_ids=[self.other_type]).status_code, 422)

    def test_cannot_delete_or_list_another_users_share(self):
        share = self._create().json()
        self.current_user_id = 2
        self.assertEqual(self.client.delete(f"/shares/{share['id']}").status_code, 404)
        self.assertEqual(self.client.get("/shares").json(), [])

    def test_public_view_only_exposes_selected_types_and_hides_notes(self):
        token = self._create(event_type_ids=[self.birthday]).json()["token"]
        data = self.client.get(f"/share/{token}").json()
        self.assertEqual(data["name"], "Family")
        self.assertEqual(data["shared_by"], "Tester")
        self.assertEqual([p["name"] for p in data["people"]], ["Ann"])
        self.assertEqual(len(data["people"][0]["events"]), 1)
        self.assertNotIn("notes", data["people"][0]["events"][0])
        self.assertNotIn("secret", self.client.get(f"/share/{token}").text)
        self.assertIsNone(data["people"][0]["image_url"])

    def test_photos_only_when_enabled(self):
        off = self._create(event_type_ids=[self.birthday]).json()["token"]
        self.assertEqual(self.client.get(f"/share/{off}/images/ann.jpg").status_code, 404)
        on = self._create(event_type_ids=[self.birthday], show_photos=True).json()["token"]
        data = self.client.get(f"/share/{on}").json()
        self.assertEqual(data["people"][0]["image_url"], f"/share/{on}/images/ann.jpg")
        self.assertEqual(self.client.get(f"/share/{on}/images/ann.jpg").status_code, 200)
        # Bob has no date of a shared type, so his photo stays private.
        self.assertEqual(self.client.get(f"/share/{on}/images/bob.jpg").status_code, 404)

    def test_ages_hidden_unless_enabled(self):
        off = self._create(event_type_ids=[self.birthday]).json()
        self.assertFalse(off["show_ages"])
        event = self.client.get(f"/share/{off['token']}").json()["people"][0]["events"][0]
        self.assertIsNone(event["year"])
        self.assertFalse(event["year_known"])
        self.assertNotIn("1990", self.client.get(f"/share/{off['token']}").text)
        on = self._create(event_type_ids=[self.birthday], show_ages=True).json()["token"]
        data = self.client.get(f"/share/{on}").json()
        self.assertTrue(data["show_ages"])
        self.assertEqual(data["people"][0]["events"][0]["year"], 1990)

    def test_year_kept_when_cadence_needs_it(self):
        with self.sessions() as db:
            custom = EventType(user_id=1, name="Every 10 days", interval=10, unit="day", sort_order=9)
            db.add(custom)
            db.flush()
            db.add(Event(person_id=db.query(Person).filter_by(name="Bob").one().id, event_type_id=custom.id, month=1, day=1, year=2020))
            db.commit()
            custom_id = custom.id
        token = self._create(event_type_ids=[custom_id]).json()["token"]
        event = self.client.get(f"/share/{token}").json()["people"][0]["events"][0]
        self.assertEqual(event["year"], 2020)

    def test_unknown_token(self):
        self.assertEqual(self.client.get("/share/nope").status_code, 404)

    def test_deleting_event_type_drops_it_from_share(self):
        token = self._create(event_type_ids=[self.birthday, self.anniversary]).json()["token"]
        with self.sessions() as db:
            db.delete(db.get(EventType, self.anniversary))
            db.commit()
        data = self.client.get(f"/share/{token}").json()
        self.assertEqual([p["name"] for p in data["people"]], ["Ann"])


if __name__ == "__main__":
    unittest.main()
