"""calendar feed token

Revision ID: 002
Revises: 001
Create Date: 2026-09-19

Adds users.calendar_token, the secret in the URL of a user's subscribable
iCalendar feed. Null means the feed is off.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("calendar_token", sa.String, nullable=True))
    op.create_index("ix_users_calendar_token", "users", ["calendar_token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_calendar_token", table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("calendar_token")
