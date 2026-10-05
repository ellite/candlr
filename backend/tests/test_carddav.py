import os
import unittest
from datetime import datetime, timedelta
from xml.sax.saxutils import escape

os.environ.setdefault("SECRET_KEY", "carddav-tests-only-not-a-real-secret-key")
os.environ.setdefault("DATABASE_URL", "sqlite://")

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import carddav
from app.config import settings
from app.database import Base, get_db
from app.dependencies import get_current_user
from app.models import CardDavAccount, Event, EventType, Person, User
from app.routers import carddav as carddav_router
from app.seed import seed_default_event_types

URL = "http://127.0.0.1:5232/alex/contacts/"


def vcard(uid, name, *props):
    lines = ["BEGIN:VCARD", "VERSION:3.0", f"UID:{uid}", f"FN:{name}", *props, "END:VCARD"]
    return "\r\n".join(lines) + "\r\n"


def multistatus(*cards):
    """cards: (href, vcard text)"""
    body = "".join(
        f"<d:response><d:href>{href}</d:href><d:propstat><d:prop><d:getetag>\"1\"</d:getetag>"
        f"<c:address-data>{escape(text)}</c:address-data></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
        for href, text in cards
    )
    return (
        '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">'
        + body
        + "</d:multistatus>"
    )


class Server:
    """A fake address book whose contents tests can change between syncs."""

    def __init__(self):
        self.cards = []
        self.status = 207
        self.requests = []
        self.location = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status in (301, 302):
            return httpx.Response(self.status, headers={"location": self.location})
        if self.status != 207:
            return httpx.Response(self.status)
        return httpx.Response(207, text=multistatus(*self.cards), headers={"content-type": "application/xml"})

    @property
    def transport(self):
        return httpx.MockTransport(self)


class CardDavTestBase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as db:
            db.add_all([User(id=1, username="Tester", email="one@example.com"), User(id=2, username="Other", email="two@example.com")])
            db.commit()
            seed_default_event_types(db, 1)
            seed_default_event_types(db, 2)
        self.previous = (settings.carddav_allow_private_hosts, settings.carddav_sync_interval_minutes)
        settings.carddav_allow_private_hosts = True
        self.server = Server()

    def tearDown(self):
        settings.carddav_allow_private_hosts, settings.carddav_sync_interval_minutes = self.previous
        self.engine.dispose()

    def connect(self, user_id=1, password="s3cret"):
        with self.sessions() as db:
            db.add(CardDavAccount(user_id=user_id, url=URL, username="alex", password=carddav.encrypt_password(password)))
            db.commit()

    def sync(self, user_id=1):
        with self.sessions() as db:
            return carddav.sync_user(db, user_id, self.server.transport)

    def cards(self, user_id=1):
        with self.sessions() as db:
            rows = {}
            for person in db.query(Person).filter_by(user_id=user_id).all():
                rows[person.name] = sorted(
                    (e.event_type.name, e.month, e.day, e.year) for e in person.events
                )
            return rows


class FetchTests(CardDavTestBase):
    def test_report_request_and_parsing(self):
        self.server.cards = [("/a.vcf", vcard("u1", "Alex Doe", "BDAY:1990-03-05")), ("/b.vcf", vcard("u2", "Sam", "BDAY:--0704"))]
        cards = carddav.fetch_cards(URL, "alex", "s3cret", self.server.transport)
        self.assertEqual([c.href for c in cards], ["/a.vcf", "/b.vcf"])
        request = self.server.requests[0]
        self.assertEqual((request.method, request.headers["depth"]), ("REPORT", "1"))
        self.assertTrue(request.headers["authorization"].startswith("Basic "))
        self.assertIn(b"addressbook-query", request.content)

    def test_errors_are_explained(self):
        for status, text in [(401, "username or password"), (403, "username or password"), (404, "No address book"), (500, "HTTP 500")]:
            self.server.status = status
            with self.assertRaises(carddav.CardDavError) as error:
                carddav.fetch_cards(URL, "", "", self.server.transport)
            self.assertIn(text, str(error.exception))
        self.server.status, self.server.location = 302, "https://elsewhere.example/dav/"
        with self.assertRaises(carddav.CardDavError) as error:
            carddav.fetch_cards(URL, "", "", self.server.transport)
        self.assertIn("https://elsewhere.example/dav/", str(error.exception))
        self.assertEqual(len(self.server.requests), 5)  # the redirect was not followed

    def test_non_dav_answer_is_rejected(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<html><body>Login"))
        with self.assertRaises(carddav.CardDavError):
            carddav.fetch_cards(URL, "", "", transport)

    def test_private_and_link_local_addresses(self):
        settings.carddav_allow_private_hosts = False
        for url in ["http://127.0.0.1/dav/", "http://10.0.0.5/dav/", "http://192.168.1.2/dav/"]:
            with self.assertRaises(carddav.CardDavError) as error:
                carddav.check_url(url)
            self.assertIn("CARDDAV_ALLOW_PRIVATE_HOSTS", str(error.exception))
        settings.carddav_allow_private_hosts = True
        carddav.check_url("http://127.0.0.1/dav/")
        with self.assertRaises(carddav.CardDavError):
            carddav.check_url("http://169.254.169.254/latest/meta-data/")  # always refused
        for url in ["ftp://example.com/", "file:///etc/passwd", "not a url"]:
            with self.assertRaises(carddav.CardDavError):
                carddav.check_url(url)

    def test_password_round_trip_and_unreadable_after_key_change(self):
        stored = carddav.encrypt_password("hunter2")
        self.assertNotIn("hunter2", stored)
        self.assertEqual(carddav.decrypt_password(stored), "hunter2")
        previous = settings.secret_key
        settings.secret_key = "another-key"
        try:
            with self.assertRaises(carddav.CardDavError):
                carddav.decrypt_password(stored)
        finally:
            settings.secret_key = previous


class SyncTests(CardDavTestBase):
    def setUp(self):
        super().setUp()
        self.connect()
        self.server.cards = [
            ("/a.vcf", vcard("u1", "Alex Doe", "BDAY:1990-03-05", "ANNIVERSARY:20150601")),
            ("/b.vcf", vcard("u2", "Sam Smith", "BDAY:--0229")),
            ("/c.vcf", vcard("u3", "No Dates")),
        ]

    def test_first_sync_creates_cards_and_records_the_result(self):
        result = self.sync()
        self.assertEqual((result.cards, result.added, result.updated, result.removed), (2, 3, 0, 0))
        self.assertEqual(
            self.cards(),
            {"Alex Doe": [("Anniversary", 6, 1, 2015), ("Birthday", 3, 5, 1990)], "Sam Smith": [("Birthday", 2, 29, None)]},
        )
        with self.sessions() as db:
            account = db.get(CardDavAccount, 1)
            self.assertIsNone(account.last_error)
            self.assertEqual(account.last_result["cards"], 2)
            self.assertIsNotNone(account.last_sync_at)
            self.assertEqual({p.source for p in db.query(Person).all()}, {"carddav"})

    def test_second_sync_changes_nothing(self):
        self.sync()
        result = self.sync()
        self.assertEqual((result.added, result.updated, result.removed), (0, 0, 0))
        self.assertEqual(len(self.cards()), 2)

    def test_rename_and_date_change_update_the_same_card(self):
        self.sync()
        with self.sessions() as db:
            person_id = db.query(Person).filter_by(name="Alex Doe").one().id
        self.server.cards[0] = ("/a.vcf", vcard("u1", "Alexandra Doe", "BDAY:1990-03-06", "ANNIVERSARY:20150601"))
        result = self.sync()
        self.assertEqual((result.added, result.updated, result.removed), (0, 1, 0))
        with self.sessions() as db:
            person = db.query(Person).filter_by(name="Alexandra Doe").one()
            self.assertEqual(person.id, person_id)
            self.assertEqual(db.query(Person).count(), 2)
        self.assertEqual(self.cards()["Alexandra Doe"][1], ("Birthday", 3, 6, 1990))

    def test_notes_and_reminder_toggle_survive_a_sync(self):
        self.sync()
        with self.sessions() as db:
            event = db.query(Event).join(Person).filter(Person.name == "Alex Doe").first()
            event.notes, event.notify = "Likes tea", False
            db.commit()
        self.server.cards[0] = ("/a.vcf", vcard("u1", "Alex Doe", "BDAY:1990-03-06", "ANNIVERSARY:20150602"))
        self.sync()
        with self.sessions() as db:
            events = db.query(Event).join(Person).filter(Person.name == "Alex Doe").all()
            self.assertEqual({(e.notes, e.notify) for e in events}, {("Likes tea", False), (None, True)})

    def test_a_date_removed_from_the_contact_is_removed(self):
        self.sync()
        self.server.cards[0] = ("/a.vcf", vcard("u1", "Alex Doe", "BDAY:1990-03-05"))
        result = self.sync()
        self.assertEqual(result.removed, 1)
        self.assertEqual(self.cards()["Alex Doe"], [("Birthday", 3, 5, 1990)])

    def test_a_deleted_contact_removes_its_card(self):
        self.sync()
        del self.server.cards[1]
        result = self.sync()
        self.assertEqual(result.removed, 1)
        self.assertEqual(list(self.cards()), ["Alex Doe"])

    def test_a_deleted_contact_keeps_a_card_that_has_hand_made_dates(self):
        self.sync()
        with self.sessions() as db:
            person = db.query(Person).filter_by(name="Sam Smith").one()
            kind = db.query(EventType).filter_by(user_id=1, name="Anniversary").one()
            db.add(Event(person_id=person.id, event_type_id=kind.id, month=8, day=8, year=2000))
            db.commit()
        del self.server.cards[1]
        self.sync()
        self.assertEqual(self.cards()["Sam Smith"], [("Anniversary", 8, 8, 2000)])
        with self.sessions() as db:
            person = db.query(Person).filter_by(name="Sam Smith").one()
            self.assertIsNone(person.source)
            self.assertEqual([e.source_key for e in person.events], [None])

    def test_an_empty_answer_never_wipes_synced_cards(self):
        self.sync()
        self.server.cards = [("/c.vcf", vcard("u3", "No Dates"))]
        with self.assertRaises(carddav.CardDavError) as error:
            self.sync()
        self.assertIn("nothing was changed", str(error.exception))
        self.assertEqual(len(self.cards()), 2)
        with self.sessions() as db:
            self.assertIn("nothing was changed", db.get(CardDavAccount, 1).last_error)

    def test_a_hand_made_card_with_the_same_name_is_adopted_not_duplicated(self):
        with self.sessions() as db:
            person = Person(user_id=1, name="alex doe")
            db.add(person)
            db.flush()
            birthday = db.query(EventType).filter_by(user_id=1, name="Birthday").one()
            db.add(Event(person_id=person.id, event_type_id=birthday.id, month=3, day=5, year=1990, notes="mine"))
            db.commit()
        result = self.sync()
        self.assertEqual(result.added, 2)  # the anniversary and Sam's birthday; Alex's birthday was claimed
        with self.sessions() as db:
            self.assertEqual(db.query(Person).filter(Person.name.in_(["alex doe", "Alex Doe"])).count(), 1)
            person = db.query(Person).filter_by(user_id=1, source_uid="u1").one()
            self.assertEqual(person.name, "Alex Doe")
            notes = {e.notes for e in person.events}
            self.assertIn("mine", notes)
            self.assertTrue(all(e.source_key for e in person.events))

    def test_dates_that_need_a_year_are_skipped_and_reported(self):
        with self.sessions() as db:
            db.query(EventType).filter_by(user_id=1, name="Birthday").update({"interval": 2, "unit": "year"})
            db.commit()
        result = self.sync()
        self.assertEqual(result.skipped, 1)
        self.assertIn("full start date", result.errors[0])
        self.assertNotIn("Sam Smith", self.cards())

    def test_other_users_are_untouched(self):
        self.sync()
        self.assertEqual(self.cards(2), {})

    def test_failed_sync_records_the_error_and_keeps_the_cards(self):
        self.sync()
        self.server.status = 401
        with self.assertRaises(carddav.CardDavError):
            self.sync()
        with self.sessions() as db:
            self.assertIn("username or password", db.get(CardDavAccount, 1).last_error)
        self.assertEqual(len(self.cards()), 2)

    def test_run_due_respects_the_interval(self):
        settings.carddav_sync_interval_minutes = 60
        now = datetime(2026, 10, 5, 12, 0)
        carddav.run_due(self.sessions, now, self.server.transport)
        self.assertEqual(len(self.cards()), 2)
        calls = len(self.server.requests)
        carddav.run_due(self.sessions, now + timedelta(minutes=59), self.server.transport)
        self.assertEqual(len(self.server.requests), calls)
        with self.sessions() as db:  # last_attempt_at was set by the first run (real clock)
            db.get(CardDavAccount, 1).last_attempt_at = now
            db.commit()
        carddav.run_due(self.sessions, now + timedelta(minutes=61), self.server.transport)
        self.assertEqual(len(self.server.requests), calls + 1)
        settings.carddav_sync_interval_minutes = 0
        carddav.run_due(self.sessions, now + timedelta(days=9), self.server.transport)
        self.assertEqual(len(self.server.requests), calls + 1)


class ApiTests(CardDavTestBase):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(carddav_router.router)

        def get_test_db():
            with self.sessions() as db:
                yield db

        def get_test_user():
            with self.sessions() as db:
                return db.get(User, 1)

        app.dependency_overrides[get_db] = get_test_db
        app.dependency_overrides[get_current_user] = get_test_user
        self.client = TestClient(app)
        self.real_sync = carddav.sync_user
        carddav_router.sync_user = lambda db, user_id: self.real_sync(db, user_id, self.server.transport)

    def tearDown(self):
        carddav_router.sync_user = self.real_sync
        self.client.close()
        super().tearDown()

    def test_save_never_returns_the_password_and_keeps_it_when_omitted(self):
        self.assertIsNone(self.client.get("/carddav").json())
        res = self.client.put("/carddav", json={"url": URL, "username": "alex", "password": "s3cret"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotIn("s3cret", res.text)
        self.assertTrue(res.json()["has_password"])
        self.client.put("/carddav", json={"url": URL, "username": "alex2"})
        with self.sessions() as db:
            account = db.get(CardDavAccount, 1)
            self.assertEqual(account.username, "alex2")
            self.assertEqual(carddav.decrypt_password(account.password), "s3cret")
        self.client.put("/carddav", json={"url": URL, "username": "alex2", "password": ""})
        with self.sessions() as db:
            self.assertEqual(db.get(CardDavAccount, 1).password, "")

    def test_save_rejects_bad_urls(self):
        for url in ["ftp://example.com/", "http://169.254.169.254/"]:
            self.assertEqual(self.client.put("/carddav", json={"url": url}).status_code, 400, url)
        settings.carddav_allow_private_hosts = False
        self.assertEqual(self.client.put("/carddav", json={"url": "http://192.168.1.2/dav/"}).status_code, 400)

    def test_sync_now_cooldown_and_errors(self):
        self.assertEqual(self.client.post("/carddav/sync").status_code, 400)  # nothing connected
        self.client.put("/carddav", json={"url": URL, "username": "alex", "password": "s3cret"})
        self.server.cards = [("/a.vcf", vcard("u1", "Alex Doe", "BDAY:1990-03-05"))]
        res = self.client.post("/carddav/sync")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["last_result"]["cards"], 1)
        self.assertIsNone(res.json()["last_error"])
        self.assertTrue(res.json()["last_sync_at"].endswith("Z") or "+" in res.json()["last_sync_at"])
        self.assertEqual(self.client.post("/carddav/sync").status_code, 429)
        with self.sessions() as db:
            db.get(CardDavAccount, 1).last_attempt_at = datetime(2020, 1, 1)
            db.commit()
        self.server.status = 401
        res = self.client.post("/carddav/sync")
        self.assertEqual(res.status_code, 400)
        self.assertIn("username or password", res.json()["detail"])
        self.assertIn("username or password", self.client.get("/carddav").json()["last_error"])

    def test_disconnect_keeps_cards_unless_asked(self):
        for delete_cards, expected in [(False, ["Alex Doe"]), (True, [])]:
            with self.sessions() as db:
                db.query(Person).delete()
                db.query(CardDavAccount).delete()
                db.commit()
            self.connect()
            self.server.cards = [("/a.vcf", vcard("u1", "Alex Doe", "BDAY:1990-03-05"))]
            self.sync()
            res = self.client.delete(f"/carddav?delete_cards={'true' if delete_cards else 'false'}")
            self.assertEqual(res.json(), {"ok": True, "cards": 1})
            self.assertIsNone(self.client.get("/carddav").json())
            self.assertEqual(list(self.cards()), expected)
            with self.sessions() as db:
                for person in db.query(Person).all():
                    self.assertIsNone(person.source)
                    self.assertTrue(all(e.source_key is None for e in person.events))


if __name__ == "__main__":
    unittest.main()
