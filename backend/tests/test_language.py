import os
import unittest

os.environ.setdefault("SECRET_KEY", "language-tests-only-not-a-real-secret-key")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import hash_password
from app.database import Base, get_db
from app.models import User
from app.routers import auth, settings


class LanguageTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.sessions = sessionmaker(bind=engine)
        with self.sessions() as db:
            db.add(User(username="alice", email="a@example.com", hashed_password=hash_password("pw-12345678")))
            db.commit()
        app = FastAPI()
        app.state.limiter = auth.limiter
        app.include_router(auth.router)
        app.include_router(settings.router)

        def get_test_db():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = get_test_db
        self.client = TestClient(app)
        login = self.client.post("/auth/login", json={"username": "alice", "password": "pw-12345678"})
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["language"], "en")

    def test_update_persists_and_is_returned_by_me(self):
        for lang in ("pt-PT", "de", "es", "fr", "it", "en"):
            res = self.client.put("/settings/language", json={"language": lang})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["language"], lang)
            self.assertEqual(self.client.get("/auth/me").json()["language"], lang)

    def test_unsupported_language_rejected(self):
        res = self.client.put("/settings/language", json={"language": "xx"})
        self.assertEqual(res.status_code, 422)

    def test_requires_authentication(self):
        self.client.cookies.clear()
        res = self.client.put("/settings/language", json={"language": "en"})
        self.assertEqual(res.status_code, 401)


if __name__ == "__main__":
    unittest.main()
