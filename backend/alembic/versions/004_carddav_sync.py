"""carddav sync

Revision ID: 004
Revises: 003
Create Date: 2026-10-05

Adds carddav_accounts (a user's connected address book) and the columns that
tie a synced card and its dates back to the contact they came from.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "carddav_accounts",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("url", sa.String, nullable=False),
        sa.Column("username", sa.String, nullable=False, server_default=""),
        sa.Column("password", sa.Text, nullable=False, server_default=""),
        sa.Column("last_attempt_at", sa.DateTime, nullable=True),
        sa.Column("last_sync_at", sa.DateTime, nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("last_result", sa.JSON, nullable=True),
    )
    op.add_column("people", sa.Column("source", sa.String, nullable=True))
    op.add_column("people", sa.Column("source_uid", sa.String, nullable=True))
    op.create_index("uq_people_source_uid", "people", ["user_id", "source", "source_uid"], unique=True)
    op.add_column("events", sa.Column("source_key", sa.String, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("events") as batch:
        batch.drop_column("source_key")
    op.drop_index("uq_people_source_uid", table_name="people")
    with op.batch_alter_table("people") as batch:
        batch.drop_column("source_uid")
        batch.drop_column("source")
    op.drop_table("carddav_accounts")
