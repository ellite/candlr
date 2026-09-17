"""Durable, same-day reminders. Run one worker with python -m app.reminders."""
import logging
import signal
from datetime import datetime, timedelta, timezone
from threading import Event as StopEvent
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from .config import settings
from .database import SessionLocal
from .events_logic import next_occurrence
from .models import Event, Person, User, NotificationChannel, ReminderDelivery
from .notifiers import dispatch

log = logging.getLogger(__name__)


def local_now(now):
    try:
        zone = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    return now.astimezone(zone)


def message(event, today):
    name = event.person.name
    kind = event.event_type.name
    years = today.year - event.year if event.year_known and event.year is not None else None
    if kind.lower() == "birthday" and years is not None and years > 0:
        body = f"{name} turns {years} today."
    else:
        body = f"{name}'s {kind.lower()} is today"
        if years is not None and years > 0:
            body += f" ({years} {'year' if years == 1 else 'years'})"
        body += "."
    return f"{kind} reminder", body


def run_once(session_factory=SessionLocal, now=None, sender=None):
    """Scan current eligibility; claim each channel before doing network I/O.

    UTC timestamps are stored naive for consistent SQLite comparisons. A lease
    recovers interrupted attempts, but a crash after provider acceptance can
    still produce a duplicate. Never hold a write transaction during sending.
    """
    sender = sender or dispatch.send
    fixed_now = now
    now = now or datetime.now(timezone.utc)
    local = local_now(now)
    today = local.date()
    with session_factory() as db:
        candidates = db.query(Event.id, User.id, NotificationChannel.id).join(
            Person, Event.person_id == Person.id
        ).join(User, Person.user_id == User.id).join(
            NotificationChannel, NotificationChannel.user_id == User.id
        ).filter(Event.notify.is_(True), NotificationChannel.enabled.is_(True)).all()
    for event_id, user_id, channel_id in candidates:
        # Fresh sessions keep edits/deletions and notifier commits isolated.
        try:
            with session_factory() as db:
                instant = fixed_now or datetime.now(timezone.utc)
                local = local_now(instant)
                if local.date() != today:
                    return
                utc = instant.astimezone(timezone.utc).replace(tzinfo=None)
                event = db.get(Event, event_id)
                user = db.get(User, user_id)
                channel = db.get(NotificationChannel, channel_id)
                if not event or not user or not channel or not event.notify or not channel.enabled:
                    continue
                if local.strftime("%H:%M") < user.notify_time:
                    continue
                if next_occurrence(event.month, event.day, today) != today:
                    continue
                key = dict(event_id=event_id, occurrence_date=today, channel=channel.channel)
                delivery = db.query(ReminderDelivery).filter_by(**key).first()
                if delivery is None:
                    db.add(ReminderDelivery(**key))
                    try:
                        db.commit()
                    except IntegrityError:
                        db.rollback()  # Another worker inserted the same delivery.
                lease = utc + timedelta(minutes=10)
                claimed = db.query(ReminderDelivery).filter_by(**key).filter(
                    ReminderDelivery.sent_at.is_(None),
                    or_(ReminderDelivery.next_attempt_at.is_(None), ReminderDelivery.next_attempt_at <= utc),
                ).update({ReminderDelivery.next_attempt_at: lease,
                          ReminderDelivery.attempts: ReminderDelivery.attempts + 1}, synchronize_session=False)
                db.commit()
                if not claimed:
                    continue
                title, body = message(event, today)
                try:
                    if channel.channel == "webpush":
                        sender(db, user, channel.channel, channel.config, title, body,
                               url=f"/events?open={event.person_id}")
                    else:
                        sender(db, user, channel.channel, channel.config, title,
                               f"{body}\n{settings.server_url.rstrip('/')}/events?open={event.person_id}")
                except Exception as exc:
                    db.rollback()
                    delivery = db.query(ReminderDelivery).filter_by(**key).first()
                    if delivery and delivery.sent_at is None and delivery.next_attempt_at == lease:
                        delay = min(60, 5 * 2 ** min(delivery.attempts - 1, 4))
                        delivery.next_attempt_at = utc + timedelta(minutes=delay)
                        db.commit()
                    # Provider error text can contain credentials or private URLs.
                    log.warning("Reminder failed event=%s channel=%s error=%s", event_id, key['channel'], type(exc).__name__)
                else:
                    db.query(ReminderDelivery).filter_by(**key).filter(
                        ReminderDelivery.next_attempt_at == lease,
                        ReminderDelivery.sent_at.is_(None),
                    ).update({ReminderDelivery.sent_at: utc, ReminderDelivery.next_attempt_at: None}, synchronize_session=False)
                    db.commit()
                    log.info("Reminder sent event=%s channel=%s", event_id, key['channel'])
        except Exception:
            log.error("Reminder processing failed event=%s channel_id=%s", event_id, channel_id)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stopping = StopEvent()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopping.set())
    log.info("Reminder worker started (timezone=%s)", settings.timezone)
    while not stopping.is_set():
        try:
            run_once()
        except Exception:
            log.error("Reminder scan failed; retrying in one minute")
        stopping.wait(60)


if __name__ == "__main__":
    main()
