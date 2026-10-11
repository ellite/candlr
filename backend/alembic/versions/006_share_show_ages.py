"""share show ages

Revision ID: 006
Revises: 005
Create Date: 2026-10-11

Adds shares.show_ages, whether a shared calendar shows ages and counts
(and the birth years behind them). Off for existing shares.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("shares", sa.Column("show_ages", sa.Boolean, nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("shares") as batch:
        batch.drop_column("show_ages")
