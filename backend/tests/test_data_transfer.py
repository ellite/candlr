import csv
import io
import json
import os
import unittest

os.environ.setdefault("SECRET_KEY", "data-transfer-tests-only-not-a-real-secret-key")
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
        self.assertEqual(
            json.loads(res.text)["event_types"],
            [{"name": n, "interval": 1, "unit": "year"} for n in ["Birthday", "Anniversary", "Graduation"]],
        )

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

    def test_json_carries_type_cadence_and_creates_types_with_it(self):
        with self.sessions() as db:
            kind = EventType(user_id=1, name="Check-in", sort_order=5, interval=3, unit="week")
            db.add(kind)
            db.flush()
            person = Person(user_id=1, name="Pat")
            db.add(person)
            db.flush()
            db.add(Event(person_id=person.id, event_type_id=kind.id, month=3, day=8, year=2026))
            db.commit()
        exported = self.client.get("/data/export?format=json").text
        self.assertIn({"name": "Check-in", "interval": 3, "unit": "week"}, json.loads(exported)["event_types"])
        self.current_user_id = 2
        self.assertEqual(self._upload("c.json", exported).json()["event_types_created"], 1)
        with self.sessions() as db:
            kind = db.query(EventType).filter_by(user_id=2, name="Check-in").one()
            self.assertEqual((kind.interval, kind.unit), (3, "week"))

    def test_events_without_a_year_are_rejected_for_types_that_need_one(self):
        with self.sessions() as db:
            db.add(EventType(user_id=1, name="Check-in", sort_order=5, interval=2, unit="month"))
            db.commit()
        csv_text = "name,type,month,day,year\nNoYear,Check-in,3,8,\nDated,Check-in,3,8,2026\nBirthday,Birthday,1,2,\n"
        body = self._upload("a.csv", csv_text).json()
        self.assertEqual((body["people_created"], body["events_added"], body["invalid_rows"]), (2, 2, 1))
        self.assertIn("full start date", body["errors"][0])
        self.assertNotIn("NoYear", [r[0] for r in self._snapshot(1)])

    def test_vcf_import_maps_birthdays_and_anniversaries(self):
        vcf = (
            "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Alex Doe\r\nBDAY:1990-03-05\r\n"
            "ANNIVERSARY:20150601\r\nEND:VCARD\r\n"
            "BEGIN:VCARD\r\nVERSION:4.0\r\nN:Smith;Sam;;;\r\nBDAY:--0229\r\nEND:VCARD\r\n"
            "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Apple Person\r\nBDAY;X-APPLE-OMIT-YEAR=1604:1604-07-04\r\n"
            "item1.X-ANNIVERSARY:2001-09-09T00:00:00Z\r\nEND:VCARD\r\n"
            "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:No Dates\r\nEND:VCARD\r\n"
        )
        res = self._upload("contacts.vcf", vcf)
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual((body["people_created"], body["events_added"], body["invalid_rows"]), (3, 5, 0))
        rows = {(r[0], r[1], r[2], r[3], r[4]) for r in self._snapshot(1)}
        self.assertIn(("Alex Doe", "Birthday", 3, 5, 1990), rows)
        self.assertIn(("Alex Doe", "Anniversary", 6, 1, 2015), rows)
        self.assertIn(("Sam Smith", "Birthday", 2, 29, None), rows)
        self.assertIn(("Apple Person", "Birthday", 7, 4, None), rows)
        self.assertIn(("Apple Person", "Anniversary", 9, 9, 2001), rows)

    def test_vcf_folded_lines_and_reimport_is_noop(self):
        vcf = "BEGIN:VCARD\nVERSION:3.0\nFN:Long\n  Name\nBDAY:1980-01-02\nEND:VCARD\n"
        self.assertEqual(self._upload("a.vcf", vcf).json()["events_added"], 1)
        again = self._upload("a.vcf", vcf).json()
        self.assertEqual((again["events_added"], again["events_skipped"]), (0, 1))
        self.assertEqual(self._snapshot(1)[0][0], "Long Name")

    def test_vcf_bad_dates_are_reported_and_garbage_rejected(self):
        vcf = "BEGIN:VCARD\nFN:Bad\nBDAY:not-a-date\nEND:VCARD\nBEGIN:VCARD\nFN:Good\nBDAY:2000-04-05\nEND:VCARD\n"
        body = self._upload("a.vcf", vcf).json()
        self.assertEqual((body["people_created"], body["invalid_rows"]), (1, 1))
        self.assertEqual(self._upload("a.vcf", "hello").status_code, 400)


if __name__ == "__main__":
    unittest.main()
