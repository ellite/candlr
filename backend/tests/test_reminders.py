import os
import unittest
from datetime import datetime, date, timedelta, timezone
from unittest.mock import Mock

os.environ.setdefault("SECRET_KEY", "reminder-tests-only")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.config import settings
from app.models import User, Person, Event, EventType, NotificationChannel, ReminderDelivery
from app.reminders import run_once, message


class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.previous_timezone = settings.timezone
        settings.timezone = "Europe/Berlin"
        self.now = datetime(2026, 9, 16, 7, tzinfo=timezone.utc)
        self.sender = Mock()
        with self.sessions() as db:
            db.add(User(id=1, username="test", email="test@example.com", notify_time="09:00"))
            db.add(EventType(id=1, user_id=1, name="Birthday"))
            db.add(Person(id=1, user_id=1, name="Alex"))
            db.add(Event(id=1, person_id=1, event_type_id=1, month=9, day=16, year=1996, year_known=True, notify=True))
            db.add_all([NotificationChannel(id=1, user_id=1, channel="email", enabled=True), NotificationChannel(id=2, user_id=1, channel="ntfy", enabled=True)])
            db.commit()

    def tearDown(self):
        settings.timezone = self.previous_timezone
        self.engine.dispose()

    def run_scan(self, now=None):
        run_once(self.sessions, now or self.now, self.sender)

    def test_timing_repeat_scans_and_restart(self):
        self.run_scan(self.now - timedelta(minutes=1))
        self.sender.assert_not_called()
        self.run_scan()
        self.assertEqual(self.sender.call_count, 2)
        self.assertEqual(self.sender.call_args.args[4], "Birthday reminder")
        self.assertIn("Alex turns 30 today.", self.sender.call_args.args[5])
        self.run_scan(self.now + timedelta(hours=4))
        self.assertEqual(self.sender.call_count, 2)
        with self.sessions() as db:
            self.assertTrue(all(row.sent_at for row in db.query(ReminderDelivery)))

    def test_same_day_catchup_and_no_previous_day_backlog(self):
        self.run_scan(self.now + timedelta(hours=12))
        self.assertEqual(self.sender.call_count, 2)
        self.sender.reset_mock()
        self.run_scan(self.now + timedelta(days=1))
        self.sender.assert_not_called()

    def test_disabled_dates_channels_and_unknown_year(self):
        with self.sessions() as db:
            db.get(Event, 1).notify = False
            db.commit()
        self.run_scan()
        self.sender.assert_not_called()
        with self.sessions() as db:
            db.get(Event, 1).notify = True
            db.get(Event, 1).year_known = False
            db.get(NotificationChannel, 2).enabled = False
            db.commit()
        self.run_scan()
        self.assertEqual(self.sender.call_count, 1)
        self.assertEqual(self.sender.call_args.args[4], "Birthday reminder")
        self.assertIn("Alex's birthday is today.", self.sender.call_args.args[5])

    def test_retry_only_failed_channel_and_recheck_toggle(self):
        def send(db, user, channel, *args):
            if channel == "email":
                raise RuntimeError("provider unavailable")
        self.sender.side_effect = send
        self.run_scan()
        self.assertEqual(self.sender.call_count, 2)
        self.run_scan(self.now + timedelta(minutes=4))
        self.assertEqual(self.sender.call_count, 2)
        with self.sessions() as db:
            db.get(Event, 1).notify = False
            db.commit()
        self.run_scan(self.now + timedelta(minutes=5))
        self.assertEqual(self.sender.call_count, 2)
        with self.sessions() as db:
            db.get(Event, 1).notify = True
            db.commit()
        self.sender.side_effect = None
        self.run_scan(self.now + timedelta(minutes=6))
        self.assertEqual(self.sender.call_count, 3)
        self.assertEqual(self.sender.call_args.args[2], "email")

    def test_active_lease_and_crash_recovery(self):
        with self.sessions() as db:
            db.add(ReminderDelivery(event_id=1, occurrence_date=date(2026, 9, 16), channel="email", attempts=1, next_attempt_at=self.now.replace(tzinfo=None)+timedelta(minutes=10)))
            db.commit()
        self.run_scan()
        self.assertEqual(self.sender.call_count, 1)
        self.assertEqual(self.sender.call_args.args[2], "ntfy")
        self.run_scan(self.now+timedelta(minutes=10))
        self.assertEqual(self.sender.call_count, 2)
        self.assertEqual(self.sender.call_args.args[2], "email")

    def test_leap_day_and_next_year(self):
        with self.sessions() as db:
            db.get(Event, 1).month = 2
            db.get(Event, 1).day = 29
            db.commit()
        self.run_scan(datetime(2027, 2, 28, 10, tzinfo=timezone.utc))
        self.assertEqual(self.sender.call_count, 2)
        self.run_scan(datetime(2028, 2, 28, 10, tzinfo=timezone.utc))
        self.assertEqual(self.sender.call_count, 2)
        self.run_scan(datetime(2028, 2, 29, 10, tzinfo=timezone.utc))
        self.assertEqual(self.sender.call_count, 4)

    def test_timezone_midnight_and_invalid_timezone_fallback(self):
        with self.sessions() as db:
            db.get(User, 1).notify_time = "00:00"
            db.commit()
        self.run_scan(datetime(2026, 9, 15, 22, tzinfo=timezone.utc))
        self.assertEqual(self.sender.call_count, 2)
        settings.timezone = "Invalid/Zone"
        self.run_scan(datetime(2027, 9, 15, 23, tzinfo=timezone.utc))
        self.assertEqual(self.sender.call_count, 2)
        self.run_scan(datetime(2027, 9, 16, 0, tzinfo=timezone.utc))
        self.assertEqual(self.sender.call_count, 4)

    def test_anniversary_and_custom_messages(self):
        with self.sessions() as db:
            event = db.get(Event, 1)
            event.event_type.name = "Anniversary"
            event.year = 2025
            self.assertEqual(message(event, self.now.date()),
                             ("Anniversary reminder", "Alex's anniversary is today (1 year)."))
            event.year = 2020
            self.assertIn("(6 years)", message(event, self.now.date())[1])
            event.year_known = False
            self.assertEqual(message(event, self.now.date())[1], "Alex's anniversary is today.")
            event.event_type.name = "Graduation"
            self.assertEqual(message(event, self.now.date()),
                             ("Graduation reminder", "Alex's graduation is today."))

    def test_push_uses_hidden_card_destination(self):
        with self.sessions() as db:
            db.get(NotificationChannel, 2).channel = "webpush"
            db.commit()
        self.run_scan()
        calls = {call.args[2]: call for call in self.sender.call_args_list}
        self.assertNotIn("/events?", calls["webpush"].args[5])
        self.assertEqual(calls["webpush"].kwargs["url"], "/events?open=1")
        self.assertIn("/events?open=1", calls["email"].args[5])

    def test_account_deletion_cleans_delivery_history(self):
        self.run_scan()
        with self.sessions() as db:
            db.delete(db.get(User, 1))
            db.commit()
            self.assertEqual(db.query(ReminderDelivery).count(), 0)


if __name__ == "__main__":
    unittest.main()
