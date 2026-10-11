"""shared calendar links

Revision ID: 005
Revises: 004
Create Date: 2026-10-11

Adds shares (a named, token-in-the-URL read-only web view of the calendar)
and share_event_types (which event types each share exposes).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shares",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("token", sa.String, nullable=False),
        sa.Column("show_photos", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_shares_token", "shares", ["token"], unique=True)
    op.create_table(
        "share_event_types",
        sa.Column("share_id", sa.Integer, sa.ForeignKey("shares.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("event_type_id", sa.Integer, sa.ForeignKey("event_types.id", ondelete="CASCADE"), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("share_event_types")
    op.drop_index("ix_shares_token", table_name="shares")
    op.drop_table("shares")
