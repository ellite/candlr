import os
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SECRET_KEY", "remember-tests-only-not-a-real-secret-key")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import hash_password
from app.config import settings
from app.database import Base, get_db
from app.limiter import limiter
from app.models import User, UserSession
from app.routers import auth


def max_age(response):
    cookie = next(c for c in response.headers.get_list("set-cookie") if c.startswith("candlr_token="))
    return int(cookie.split("Max-Age=")[1].split(";")[0])


class RememberMeTests(unittest.TestCase):
    def setUp(self):
        limiter.reset()
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.sessions = sessionmaker(bind=engine)
        with self.sessions() as db:
            db.add(User(username="alice", email="alice@example.com", hashed_password=hash_password("pw-12345678")))
            db.commit()
        app = FastAPI()
        app.state.limiter = auth.limiter
        app.include_router(auth.router)

        def get_test_db():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = get_test_db
        self.client = TestClient(app)

    def login(self, **extra):
        return self.client.post("/auth/login", json={"username": "alice", "password": "pw-12345678", **extra})

    def age_session(self, to):
        with self.sessions() as db:
            row = db.query(UserSession).one()
            row.expires_at = to
            db.commit()

    def expires_in(self):
        with self.sessions() as db:
            expires_at = db.query(UserSession).one().expires_at.replace(tzinfo=timezone.utc)
        return expires_at - datetime.now(timezone.utc)

    def test_default_login_keeps_the_short_lifetime(self):
        res = self.login()
        self.assertEqual(max_age(res), settings.access_token_expire_minutes * 60)
        self.assertLess(abs(self.expires_in() - timedelta(minutes=settings.access_token_expire_minutes)), timedelta(minutes=1))

    def test_remember_login_lasts_thirty_days(self):
        res = self.login(remember=True)
        self.assertEqual(max_age(res), settings.remember_me_days * 86400)
        self.assertLess(abs(self.expires_in() - timedelta(days=settings.remember_me_days)), timedelta(minutes=1))
        self.assertEqual(self.client.get("/auth/me").status_code, 200)

    def test_refresh_extends_a_remembered_session(self):
        self.login(remember=True)
        self.age_session(datetime.now(timezone.utc) + timedelta(days=3))
        res = self.client.post("/auth/refresh")
        self.assertEqual(res.json(), {"refreshed": True})
        self.assertEqual(max_age(res), settings.remember_me_days * 86400)
        self.assertGreater(self.expires_in(), timedelta(days=settings.remember_me_days) - timedelta(minutes=1))
        # The reissued token must still pass the session check.
        self.assertEqual(self.client.get("/auth/me").status_code, 200)

    def test_refresh_is_throttled_for_a_fresh_session(self):
        self.login(remember=True)
        res = self.client.post("/auth/refresh")
        self.assertEqual(res.json(), {"refreshed": False})
        self.assertFalse([c for c in res.headers.get_list("set-cookie") if c.startswith("candlr_token=")])

    def test_refresh_never_extends_a_default_session(self):
        self.login()
        soon = datetime.now(timezone.utc) + timedelta(hours=2)
        self.age_session(soon)
        res = self.client.post("/auth/refresh")
        self.assertEqual(res.json(), {"refreshed": False})
        self.assertLess(self.expires_in(), timedelta(hours=3))

    def test_refresh_rejects_missing_and_expired_sessions(self):
        self.assertEqual(self.client.post("/auth/refresh").status_code, 401)
        self.login(remember=True)
        self.age_session(datetime.now(timezone.utc) - timedelta(minutes=1))
        self.assertEqual(self.client.post("/auth/refresh").status_code, 401)


if __name__ == "__main__":
    unittest.main()
