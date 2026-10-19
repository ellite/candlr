"""share show addresses

Revision ID: 008
Revises: 007
Create Date: 2026-10-19

Adds shares.show_addresses, whether a shared calendar shows each card's
address. Off for existing shares.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("shares", sa.Column("show_addresses", sa.Boolean, nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("shares") as batch:
        batch.drop_column("show_addresses")
