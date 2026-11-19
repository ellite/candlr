"""user language

Revision ID: 010
Revises: 009
Create Date: 2026-11-19

Adds users.language, the account's UI language. Existing users stay on English.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("language", sa.String(10), nullable=False, server_default="en"))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("language")
