import os
import unittest

os.environ.setdefault("SECRET_KEY", "login-tests-only-not-a-real-secret-key")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import hash_password
from app.database import Base, get_db
from app.models import User
from app.routers import auth


class LoginIdentifierTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine)
        with sessions() as db:
            db.add(User(username="alice", email="Alice@Example.com", hashed_password=hash_password("pw-12345678")))
            db.commit()
        app = FastAPI()
        app.state.limiter = auth.limiter
        app.include_router(auth.router)

        def get_test_db():
            with sessions() as db:
                yield db

        app.dependency_overrides[get_db] = get_test_db
        self.client = TestClient(app)

    def login(self, identifier, password="pw-12345678"):
        return self.client.post("/auth/login", json={"username": identifier, "password": password})

    def test_username(self):
        self.assertEqual(self.login("alice").status_code, 200)

    def test_email_case_insensitive(self):
        self.assertEqual(self.login("alice@example.com").status_code, 200)
        self.assertEqual(self.login(" ALICE@example.com ").status_code, 200)

    def test_wrong_password_and_unknown(self):
        self.assertEqual(self.login("alice@example.com", "nope").status_code, 401)
        self.assertEqual(self.login("bob@example.com").status_code, 401)


if __name__ == "__main__":
    unittest.main()
