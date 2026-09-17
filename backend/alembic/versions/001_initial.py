"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-09-17

Squashed from the nine incremental migrations used during pre-release
development (users/sessions through two-factor auth). Since nothing has
been deployed yet, there's no upgrade path to preserve - this just creates
the schema as of v1 in one step.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String, unique=True, nullable=False, index=True),
        sa.Column("email", sa.String, unique=True, nullable=False, index=True),
        # Nullable: OIDC-only accounts have no local password.
        sa.Column("hashed_password", sa.String, nullable=True),
        sa.Column("is_admin", sa.Boolean, default=False),
        # Time of day (24h "HH:MM", in TIMEZONE) to send reminders for any
        # event with notify=True - always on the day itself.
        sa.Column("notify_time", sa.String, nullable=False, server_default="09:00"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "sessions",
        sa.Column("jti", sa.String, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        # Channel-specific settings, e.g. {"topic": "..."} for ntfy,
        # {"webhook_url": "..."} for Discord. Unused (empty) for email and
        # webpush, whose destination is the account's own email / subscribed
        # devices respectively.
        sa.Column("config", sa.JSON, nullable=False, server_default="{}"),
        sa.UniqueConstraint("user_id", "channel", name="uq_notification_channel_user_channel"),
    )
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("endpoint", sa.String, nullable=False, unique=True),
        sa.Column("p256dh", sa.String, nullable=False),
        sa.Column("auth", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token", sa.String, nullable=False, unique=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "event_types",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "name", name="uq_event_type_user_name"),
    )
    op.create_table(
        "people",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        # Filename under backend/data/images/, not a full path - keeps the DB
        # portable if the data directory ever moves.
        sa.Column("image_filename", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type_id", sa.Integer, sa.ForeignKey("event_types.id", ondelete="CASCADE"), nullable=False),
        sa.Column("month", sa.Integer, nullable=False),
        sa.Column("day", sa.Integer, nullable=False),
        sa.Column("year", sa.Integer, nullable=True),
        sa.Column("year_known", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text, nullable=True),
        # Whether this date should trigger a reminder.
        sa.Column("notify", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "reminder_deliveries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("occurrence_date", sa.Date, nullable=False),
        sa.Column("channel", sa.String, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime, nullable=True),
        sa.Column("sent_at", sa.DateTime, nullable=True),
        sa.UniqueConstraint("event_id", "occurrence_date", "channel", name="uq_reminder_occurrence_channel"),
    )
    op.create_table(
        "two_factor_auth",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("secret", sa.Text, nullable=True),
        sa.Column("pending_secret", sa.Text, nullable=True),
        sa.Column("pending_expires_at", sa.DateTime, nullable=True),
        sa.Column("last_step", sa.Integer, nullable=False, server_default="-1"),
        sa.Column("recovery_hashes", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("failures", sa.Integer, nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime, nullable=True),
        sa.Column("challenge_hash", sa.String, nullable=True, unique=True),
        sa.Column("challenge_expires_at", sa.DateTime, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("two_factor_auth")
    op.drop_table("reminder_deliveries")
    op.drop_table("events")
    op.drop_table("people")
    op.drop_table("event_types")
    op.drop_table("password_reset_tokens")
    op.drop_table("push_subscriptions")
    op.drop_table("notification_channels")
    op.drop_table("sessions")
    op.drop_table("users")
