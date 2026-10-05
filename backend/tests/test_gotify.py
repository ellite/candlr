import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "gotify-tests-only-not-a-real-secret-key")
os.environ.setdefault("DATABASE_URL", "sqlite://")

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.dependencies import get_current_user
from app.models import User
from app.notifiers import gotify
from app.notifiers.errors import NotifierError
from app.routers import notifications

GOOD = {"server": "https://gotify.example.com/", "app_token": "tok"}


class ParseConfigTests(unittest.TestCase):
    def test_valid_configs(self):
        self.assertEqual(gotify.parse_config(GOOD), ("https://gotify.example.com", "tok", None))
        self.assertEqual(gotify.parse_config({**GOOD, "server": "http://10.0.0.5:3001"})[0], "http://10.0.0.5:3001")
        self.assertEqual(gotify.parse_config({**GOOD, "server": "https://host/gotify/"})[0], "https://host/gotify")
        for raw, expected in [(None, None), ("", None), ("  ", None), ("0", 0), ("10", 10), (" 7 ", 7), (8, 8)]:
            self.assertEqual(gotify.parse_config({**GOOD, "priority": raw})[2], expected, raw)

    def test_missing_fields(self):
        for config in [{}, {"server": "https://h"}, {"app_token": "t"}, {"server": " ", "app_token": " "}]:
            with self.assertRaises(ValueError) as error:
                gotify.parse_config(config)
            self.assertIn("required", str(error.exception))

    def test_invalid_priority_is_a_value_error_never_a_crash(self):
        # "²" and "①" pass str.isdigit() but int() refuses them.
        for raw in ["11", "abc", "-1", "5.5", "²", "①", "١٢", 11, 5.5, True]:
            with self.assertRaises(ValueError, msg=repr(raw)) as error:
                gotify.parse_config({**GOOD, "priority": raw})
            self.assertIn("0 to 10", str(error.exception))

    def test_invalid_server_urls(self):
        for server in ["not a url", "ftp://example.com", "http://", "http://[::1", "http://a\r\nb", "file:///etc/passwd"]:
            with self.assertRaises(ValueError, msg=server):
                gotify.parse_config({**GOOD, "server": server})


class SendTests(unittest.TestCase):
    def post_through(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return patch("app.notifiers.gotify.httpx.post", lambda url, **kwargs: client.post(url, **kwargs))

    def test_request_shape(self):
        seen = {}

        def handler(request):
            seen["request"] = request
            return httpx.Response(200, json={})

        with self.post_through(handler):
            gotify.send("Title", "Body", {**GOOD, "priority": "8"})
        request = seen["request"]
        self.assertEqual(str(request.url), "https://gotify.example.com/message")
        self.assertEqual(request.headers["x-gotify-key"], "tok")
        self.assertEqual(json.loads(request.read()), {"title": "Title", "message": "Body", "priority": 8})

    def test_priority_is_left_out_when_unset(self):
        seen = {}

        def handler(request):
            seen["body"] = request.read()
            return httpx.Response(200, json={})

        with self.post_through(handler):
            gotify.send("T", "B", GOOD)
        self.assertNotIn(b"priority", seen["body"])

    def test_errors_become_notifier_errors(self):
        with self.post_through(lambda request: httpx.Response(401)):
            with self.assertRaises(NotifierError) as error:
                gotify.send("T", "B", GOOD)
        self.assertIn("Gotify error", str(error.exception))
        for bad in [{**GOOD, "server": "http://[::1"}, {**GOOD, "priority": "²"}, {}]:
            with self.assertRaises(NotifierError):
                gotify.send("T", "B", bad)  # rejected before any request is made


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as db:
            db.add(User(id=1, username="Tester", email="one@example.com"))
            db.commit()
        app = FastAPI()
        app.include_router(notifications.router)

        def get_test_db():
            with self.sessions() as db:
                yield db

        def get_test_user():
            with self.sessions() as db:
                return db.get(User, 1)

        app.dependency_overrides[get_db] = get_test_db
        app.dependency_overrides[get_current_user] = get_test_user
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def save(self, **config):
        return self.client.put("/notifications/channels/gotify", json={"enabled": True, "config": {**GOOD, **config}})

    def test_save_validates_without_ever_returning_a_500(self):
        self.assertEqual(self.save().status_code, 200)
        self.assertEqual(self.save(priority="8").status_code, 200)
        self.assertEqual(self.save(priority="").status_code, 200)
        for bad in [{"priority": "11"}, {"priority": "abc"}, {"priority": "²"}, {"server": "http://[::1"}, {"server": "ftp://h"}, {"server": ""}]:
            res = self.save(**bad)
            self.assertEqual(res.status_code, 400, (bad, res.text))

    def test_disabled_channels_are_not_validated(self):
        res = self.client.put("/notifications/channels/gotify", json={"enabled": False, "config": {}})
        self.assertEqual(res.status_code, 200)

    def test_gotify_is_listed(self):
        channels = [c["channel"] for c in self.client.get("/notifications/channels").json()]
        self.assertIn("gotify", channels)


if __name__ == "__main__":
    unittest.main()
