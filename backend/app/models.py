from typing import Optional
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Date, DateTime, JSON, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base
from .events_logic import days_until as _days_until


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    # Nullable: an OIDC-only account (auto-created on first SSO login) has no
    # local password to check against.
    hashed_password = Column(String, nullable=True)
    is_admin = Column(Boolean, default=False)
    # Time of day (24h "HH:MM", in TIMEZONE) to send reminders for any Event
    # with notify=True - always on the day itself.
    notify_time = Column(String, nullable=False, default="09:00")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    two_factor = relationship("TwoFactorAuth", uselist=False, cascade="all, delete-orphan")
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    notification_channels = relationship("NotificationChannel", cascade="all, delete-orphan")
    push_subscriptions = relationship("PushSubscription", cascade="all, delete-orphan")
    password_reset_tokens = relationship("PasswordResetToken", cascade="all, delete-orphan")
    event_types = relationship("EventType", cascade="all, delete-orphan")
    people = relationship("Person", cascade="all, delete-orphan")

    @property
    def has_password(self) -> bool:
        return self.hashed_password is not None


class UserSession(Base):
    __tablename__ = "sessions"

    jti = Column(String, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    user = relationship("User", back_populates="sessions")


class NotificationChannel(Base):
    __tablename__ = "notification_channels"
    __table_args__ = (UniqueConstraint("user_id", "channel", name="uq_notification_channel_user_channel"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    channel = Column(String, nullable=False)  # email | ntfy | discord | telegram | webpush
    enabled = Column(Boolean, nullable=False, default=False)
    # Channel-specific settings, e.g. {"topic": "..."} for ntfy, {"webhook_url": "..."}
    # for Discord. Unused (empty) for email and webpush, whose destination is
    # the account's own email / subscribed devices respectively.
    config = Column(JSON, nullable=False, default=dict)


class PushSubscription(Base):
    """One row per browser/device a user has enabled web push notifications on."""

    __tablename__ = "push_subscriptions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    endpoint = Column(String, unique=True, nullable=False)
    p256dh = Column(String, nullable=False)
    auth = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PasswordResetToken(Base):
    """A single-use, short-lived token emailed to a user who requested a
    password reset. Only ever exists if SMTP is configured."""

    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class EventType(Base):
    """A kind of date a user tracks (Birthday, Anniversary, or a custom one).
    Birthday and Anniversary are seeded for every new user (is_default=True);
    users can rename or delete any of them, and add their own."""

    __tablename__ = "event_types"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_event_type_user_name"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    is_default = Column(Boolean, nullable=False, default=False)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    events = relationship("Event", back_populates="event_type", cascade="all, delete-orphan")


class Person(Base):
    """One card - a person whose dates are being tracked. Can have several
    events attached (e.g. both a Birthday and a Wedding Anniversary)."""

    __tablename__ = "people"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    # Filename under backend/data/images/, not a full path - keeps the DB
    # portable if the data directory ever moves.
    image_filename = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    events = relationship(
        "Event", back_populates="person", cascade="all, delete-orphan", order_by="Event.month, Event.day"
    )

    @property
    def image_url(self) -> Optional[str]:
        return f"/images/{self.image_filename}" if self.image_filename else None


class Event(Base):
    """One dated occasion for a person (a birthday, an anniversary, ...).
    Stored as separate month/day/year fields rather than a single date so a
    year can be left unset (year_known=False) without needing a sentinel
    value - month/day alone is enough to compute the next occurrence."""

    __tablename__ = "events"

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("people.id", ondelete="CASCADE"), nullable=False)
    event_type_id = Column(Integer, ForeignKey("event_types.id", ondelete="CASCADE"), nullable=False)
    month = Column(Integer, nullable=False)
    day = Column(Integer, nullable=False)
    year = Column(Integer, nullable=True)
    year_known = Column(Boolean, nullable=False, default=True)
    notes = Column(Text, nullable=True)
    # Whether this date should trigger a reminder.
    notify = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    deliveries = relationship("ReminderDelivery", cascade="all, delete-orphan")
    person = relationship("Person", back_populates="events")
    event_type = relationship("EventType", back_populates="events")

    @property
    def days_until(self) -> int:
        return _days_until(self.month, self.day)


class ReminderDelivery(Base):
    __tablename__ = "reminder_deliveries"
    __table_args__ = (UniqueConstraint("event_id", "occurrence_date", "channel", name="uq_reminder_occurrence_channel"),)

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    occurrence_date = Column(Date, nullable=False)
    channel = Column(String, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)


class TwoFactorAuth(Base):
    __tablename__ = "two_factor_auth"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=False)
    secret = Column(Text, nullable=True)
    pending_secret = Column(Text, nullable=True)
    pending_expires_at = Column(DateTime, nullable=True)
    last_step = Column(Integer, nullable=False, default=-1)
    recovery_hashes = Column(JSON, nullable=False, default=list)
    failures = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime, nullable=True)
    challenge_hash = Column(String, unique=True, nullable=True)
    challenge_expires_at = Column(DateTime, nullable=True)
