"""person address

Revision ID: 007
Revises: 006
Create Date: 2026-10-19

Adds people.address, a free-text postal address for sending a card or gift.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("people", sa.Column("address", sa.Text, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("people") as batch:
        batch.drop_column("address")
