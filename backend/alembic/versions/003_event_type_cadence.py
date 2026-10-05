"""event type cadence

Revision ID: 003
Revises: 002
Create Date: 2026-10-05

Adds event_types.interval and event_types.unit: how often dates of that type
recur (every N days, weeks, months or years). Existing types stay yearly.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("event_types", sa.Column("interval", sa.Integer, nullable=False, server_default="1"))
    op.add_column("event_types", sa.Column("unit", sa.String, nullable=False, server_default="year"))


def downgrade() -> None:
    with op.batch_alter_table("event_types") as batch:
        batch.drop_column("unit")
        batch.drop_column("interval")
