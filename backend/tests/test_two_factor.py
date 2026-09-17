import os
import unittest
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "two-factor-tests-only")
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pyotp
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.auth import hash_password
from app.database import Base, get_db
from app.limiter import limiter
from app.models import User, UserSession, TwoFactorAuth, PasswordResetToken
from app.routers import auth, two_factor, oidc
from app import two_factor as mfa
from app.config import settings


class TwoFactorTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        limiter.reset()
        self.app = FastAPI()
        self.app.state.limiter = limiter
        self.app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        self.app.add_middleware(SlowAPIMiddleware)
        for router in (auth.router, two_factor.router, oidc.router):
            self.app.include_router(router)

        def database():
            with self.sessions() as db:
                yield db
        self.app.dependency_overrides[get_db] = database
        with self.sessions() as db:
            db.add(User(id=1, username="test", email="test@example.com", hashed_password=hash_password("password123")))
            db.commit()
        self.client = TestClient(self.app)
        self.login()

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def login(self):
        return self.client.post("/auth/login", json={"username": "test", "password": "password123"})

    def setup(self):
        response = self.client.post("/auth/2fa/setup", json={"password": "password123"})
        self.assertEqual(response.status_code, 200, response.text)
        self.secret = response.json()["secret"]
        self.assertTrue(response.json()["qr_code"].startswith("data:image/png;base64,"))
        self.assertEqual(response.headers["cache-control"], "no-store")
        return self.secret

    def enable(self):
        self.setup()
        response = self.client.post("/auth/2fa/enable", json={"code": pyotp.TOTP(self.secret).now()})
        self.assertEqual(response.status_code, 200, response.text)
        self.codes = response.json()["recovery_codes"]
        return self.codes

    def challenge(self):
        self.client.cookies.clear()
        response = self.login()
        self.assertEqual(response.json(), {"requires_2fa": True})
        self.assertNotIn("candlr_token", self.client.cookies)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_enrollment_encryption_and_recovery_login(self):
        old_cookie = self.client.cookies.get("candlr_token")
        self.enable()
        with self.sessions() as db:
            row = db.get(TwoFactorAuth, 1)
            self.assertNotIn(self.secret, row.secret)
            self.assertNotIn(self.codes[0], row.recovery_hashes)
            self.assertEqual(len(row.recovery_hashes), 10)
            self.assertIsNone(row.pending_secret)
            self.assertEqual(db.query(UserSession).count(), 1)
        self.assertEqual(self.client.get("/auth/me", headers={"cookie": f"candlr_token={old_cookie}"}).status_code, 401)
        self.challenge()
        response = self.client.post("/auth/2fa/verify", json={"code": self.codes[0]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/auth/me").status_code, 200)
        self.assertEqual(self.client.post("/auth/2fa/verify", json={"code": self.codes[1]}).status_code, 401)
        self.challenge()
        self.assertEqual(self.client.post("/auth/2fa/verify", json={"code": self.codes[0]}).status_code, 400)
        self.assertEqual(self.client.post("/auth/2fa/verify", json={"code": self.codes[1]}).status_code, 200)

    def test_totp_replay_and_successful_next_step(self):
        self.enable()
        self.challenge()
        self.assertEqual(self.client.post("/auth/2fa/verify", json={"code": pyotp.TOTP(self.secret).now()}).status_code, 400)
        future = mfa.now() + timedelta(seconds=30)
        with patch("app.two_factor.now", return_value=future):
            code = pyotp.TOTP(self.secret).at(future.replace(tzinfo=timezone.utc))
            self.assertEqual(self.client.post("/auth/2fa/verify", json={"code": code}).status_code, 200)
            self.challenge()
            self.assertEqual(self.client.post("/auth/2fa/verify", json={"code": code}).status_code, 400)

    def test_attempt_limit_persists_across_new_challenges(self):
        self.enable()
        self.challenge()
        for _ in range(5):
            self.assertEqual(self.client.post("/auth/2fa/verify", json={"code": "invalid"}).status_code, 400)
        self.assertEqual(self.client.post("/auth/2fa/verify", json={"code": self.codes[0]}).status_code, 429)
        self.assertEqual(self.login().status_code, 429)
        with patch("app.two_factor.now", return_value=mfa.now()+timedelta(minutes=6)):
            self.assertEqual(self.login().status_code, 200)
            self.assertEqual(self.client.post("/auth/2fa/verify", json={"code": self.codes[0]}).status_code, 200)

    def test_expired_challenge_and_expired_setup(self):
        self.setup()
        with self.sessions() as db:
            db.get(TwoFactorAuth, 1).pending_expires_at = mfa.now()-timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.client.post("/auth/2fa/enable", json={"code": pyotp.TOTP(self.secret).now()}).status_code, 400)
        self.enable()
        self.challenge()
        with self.sessions() as db:
            db.get(TwoFactorAuth, 1).challenge_expires_at = mfa.now()-timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.client.post("/auth/2fa/verify", json={"code": self.codes[0]}).status_code, 401)

    def test_disable_and_regenerate_require_both_factors(self):
        self.enable()
        response = self.client.post("/auth/2fa/disable", json={"password": "wrong", "code": self.codes[0]})
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/auth/2fa/recovery-codes", json={"password": "password123", "code": self.codes[0]})
        self.assertEqual(response.status_code, 200)
        new_codes = response.json()["recovery_codes"]
        self.assertEqual(self.client.post("/auth/2fa/disable", json={"password": "password123", "code": self.codes[1]}).status_code, 400)
        response = self.client.post("/auth/2fa/disable", json={"password": "password123", "code": new_codes[0]})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.client.get("/auth/2fa").json()["enabled"])
        self.client.cookies.clear()
        self.assertIn("username", self.login().json())

    def test_password_reset_does_not_bypass_2fa(self):
        self.enable()
        self.challenge()
        with self.sessions() as db:
            db.add(PasswordResetToken(user_id=1, token="reset-test"))
            db.commit()
        response = self.client.post("/auth/reset-password/reset-test", json={"new_password": "newpassword123"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post("/auth/2fa/verify", json={"code": self.codes[0]}).status_code, 401)
        response = self.client.post("/auth/login", json={"username": "test", "password": "newpassword123"})
        self.assertEqual(response.json(), {"requires_2fa": True})

    def test_sso_only_setup_rejected_and_anonymous_setup_rejected(self):
        with self.sessions() as db:
            db.get(User, 1).hashed_password = None
            db.commit()
        self.assertEqual(self.client.post("/auth/2fa/setup", json={"password": ""}).status_code, 400)
        self.client.cookies.clear()
        self.assertEqual(self.client.post("/auth/2fa/setup", json={"password": "password123"}).status_code, 401)

    def test_oidc_cannot_bypass_enabled_2fa(self):
        self.enable()
        self.client.cookies.clear()
        with patch.object(settings, "oidc_enabled", True), patch("app.routers.oidc.httpx.Client") as client:
            http = client.return_value.__enter__.return_value
            http.post.return_value.is_success = True
            http.post.return_value.json.return_value = {"access_token": "test"}
            http.get.return_value.is_success = True
            http.get.return_value.json.return_value = {"email": "test@example.com"}
            response = self.client.post("/oidc/exchange", json={"code": "provider-code"})
        self.assertEqual(response.json(), {"requires_2fa": True})
        self.assertNotIn("candlr_token", self.client.cookies)

    def test_concurrent_challenge_consumption_creates_only_one_session(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine("sqlite:///" + str(Path(directory) / "concurrent.db"))
            Base.metadata.create_all(engine)
            sessions = sessionmaker(bind=engine, autoflush=False)
            try:
                with sessions() as db:
                    db.add(User(id=1, username="parallel", email="parallel@example.com"))
                    db.add(TwoFactorAuth(
                        user_id=1, enabled=True,
                        secret=mfa.cipher().encrypt(pyotp.random_base32().encode()).decode(),
                        recovery_hashes=[mfa.digest("1234abcd5678ef90")],
                        challenge_hash=mfa.digest("test-challenge"),
                        challenge_expires_at=mfa.now() + timedelta(minutes=5),
                    ))
                    db.commit()
                barrier = Barrier(2)

                def attempt(_):
                    with sessions() as db:
                        barrier.wait(timeout=5)
                        try:
                            # __wrapped__ bypasses the @limiter.limit decorator, which
                            # needs a real starlette Request; functools.wraps keeps the
                            # undecorated function reachable here. request is unused by
                            # the function body, so a placeholder is fine.
                            two_factor.verify.__wrapped__(SimpleNamespace(), two_factor.CodeInput(code="1234-abcd-5678-ef90"),
                                              Response(), "test-challenge", db)
                            return 200
                        except HTTPException as error:
                            return error.status_code
                with ThreadPoolExecutor(max_workers=2) as pool:
                    self.assertEqual(sorted(pool.map(attempt, range(2))), [200, 401])
                with sessions() as db:
                    self.assertEqual(db.query(UserSession).count(), 1)
                    self.assertEqual(db.get(TwoFactorAuth, 1).recovery_hashes, [])
            finally:
                engine.dispose()

    def test_account_deletion_removes_2fa(self):
        self.enable()
        self.assertEqual(self.client.request("DELETE", "/auth/me", json={"password": "password123"}).status_code, 200)
        with self.sessions() as db:
            self.assertIsNone(db.get(TwoFactorAuth, 1))
