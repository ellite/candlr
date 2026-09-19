import csv
import io
import json
import os
import unittest

os.environ.setdefault("SECRET_KEY", "data-transfer-tests-only")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.dependencies import get_current_user
from app.models import Event, EventType, Person, User
from app.routers.data import router
from app.seed import seed_default_event_types


class DataTransferTests(unittest.TestCase):
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

        def get_test_db():
            with self.sessions() as db:
                yield db

        self.current_user_id = 1

        def get_test_user():
            with self.sessions() as db:
                return db.get(User, self.current_user_id)

        app.dependency_overrides[get_db] = get_test_db
        app.dependency_overrides[get_current_user] = get_test_user
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def _upload(self, name, content):
        data = content.encode("utf-8") if isinstance(content, str) else content
        return self.client.post("/data/import", files={"file": (name, data)})

    def _seed_cards(self):
        with self.sessions() as db:
            birthday, anniversary = db.query(EventType).filter_by(user_id=1).order_by(EventType.sort_order).all()
            custom = EventType(user_id=1, name="Graduation", sort_order=2)
            db.add(custom)
            db.flush()
            alex = Person(user_id=1, name="Alex, \"Al\" Ñandú")
            sam = Person(user_id=1, name="Sam")
            db.add_all([alex, sam])
            db.flush()
            db.add_all(
                [
                    Event(person_id=alex.id, event_type_id=birthday.id, month=3, day=5, year=1990, notes="Likes tea,\nand books"),
                    Event(person_id=alex.id, event_type_id=anniversary.id, month=6, day=1, year=None, year_known=False, notify=False),
                    Event(person_id=sam.id, event_type_id=custom.id, month=2, day=29, year=2000),
                ]
            )
            db.commit()

    def _snapshot(self, user_id):
        with self.sessions() as db:
            rows = (
                db.query(Person.name, EventType.name, Event.month, Event.day, Event.year, Event.year_known, Event.notes, Event.notify)
                .join(Event, Event.person_id == Person.id)
                .join(EventType, Event.event_type_id == EventType.id)
                .filter(Person.user_id == user_id)
                .all()
            )
            return sorted(tuple(r) for r in rows)

    def test_json_round_trip_into_another_account(self):
        self._seed_cards()
        res = self.client.get("/data/export?format=json")
        self.assertEqual(res.status_code, 200)
        self.assertIn("attachment", res.headers["content-disposition"])
        self.assertEqual(json.loads(res.text)["event_types"], ["Birthday", "Anniversary", "Graduation"])

        self.current_user_id = 2
        out = self._upload("candlr.json", res.text).json()
        self.assertEqual(out["people_created"], 2)
        self.assertEqual(out["events_added"], 3)
        self.assertEqual(out["event_types_created"], 1)
        self.assertEqual([r[1:] for r in self._snapshot(2)], [r[1:] for r in self._snapshot(1)])

    def test_csv_round_trip_preserves_quotes_newlines_and_unicode(self):
        self._seed_cards()
        res = self.client.get("/data/export?format=csv")
        self.assertTrue(res.content.startswith(b"\xef\xbb\xbf"))
        self.current_user_id = 2
        out = self._upload("candlr.csv", res.content).json()
        self.assertEqual(out["events_added"], 3)
        self.assertEqual(self._snapshot(2), self._snapshot(1))

    def test_reimport_is_a_no_op(self):
        self._seed_cards()
        exported = self.client.get("/data/export?format=csv").content
        out = self._upload("candlr.csv", exported).json()
        self.assertEqual((out["people_created"], out["events_added"], out["events_skipped"]), (0, 0, 3))
        self.assertEqual(len(self._snapshot(1)), 3)

    def test_csv_merges_by_name_and_adds_only_new_dates(self):
        self._seed_cards()
        out = self._upload(
            "more.csv",
            "Name,Type,Month,Day,Year\nsam,Graduation,2,29,2000\nSam,Birthday,7,4,\nNew Person,birthday,12,25,1980\nnew person,Anniversary,1,2,\n",
        ).json()
        self.assertEqual((out["people_created"], out["events_added"], out["events_skipped"]), (1, 3, 1))
        with self.sessions() as db:
            self.assertEqual(db.query(Person).filter_by(user_id=1).count(), 3)
            self.assertEqual(db.query(EventType).filter_by(user_id=1).count(), 3)

    def test_csv_date_column_blank_type_and_bad_rows_reported(self):
        out = self._upload(
            "dates.csv",
            "name,date,type\nA,1990-04-12,\nB,--05-06,Anniversary\nC,4-7,\nD,2023-02-30,Birthday\nE,nonsense,Birthday\n,01-01,\n",
        ).json()
        self.assertEqual((out["people_created"], out["events_added"], out["invalid_rows"]), (3, 3, 3))
        self.assertEqual(len(out["errors"]), 3)
        with self.sessions() as db:
            a = db.query(Event).join(Person).filter(Person.name == "A").one()
            self.assertEqual((a.year, a.year_known, a.event_type.name), (1990, True, "Birthday"))
            c = db.query(Event).join(Person).filter(Person.name == "C").one()
            self.assertEqual((c.month, c.day, c.year, c.year_known), (4, 7, None, False))

    def test_rejects_unreadable_files_without_writing(self):
        for name, content in [
            ("x.csv", "foo,bar\n1,2\n"),
            ("x.csv", "name,month\nA,1\n"),
            ("x.json", "{not json"),
            ("x.json", json.dumps({"people": "nope"})),
            ("x.csv", b"\xff\xfe\x00bad"),
            ("x.csv", "name,month,day\nA,13,1\n"),
        ]:
            res = self._upload(name, content)
            self.assertEqual(res.status_code, 400, (name, content))
        self.assertEqual(self._snapshot(1), [])

    def test_oversized_upload_rejected(self):
        res = self._upload("big.csv", "name,month,day\n" + "A,1,1\n" * 400_000)
        self.assertEqual(res.status_code, 413)

    def test_export_only_contains_own_data(self):
        self._seed_cards()
        self.current_user_id = 2
        rows = list(csv.reader(io.StringIO(self.client.get("/data/export?format=csv").text.lstrip("﻿"))))
        self.assertEqual(len(rows), 1)  # header only


if __name__ == "__main__":
    unittest.main()
