"""remember me

Revision ID: 009
Revises: 008
Create Date: 2026-10-27

Adds sessions.remember, set for "Keep me logged in" sessions that slide
forward as the app is used, and two_factor_auth.challenge_remember, which
carries that choice across the authenticator-code step. Off for existing rows.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("remember", sa.Boolean, nullable=False, server_default="0"))
    op.add_column("two_factor_auth", sa.Column("challenge_remember", sa.Boolean, nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("two_factor_auth") as batch:
        batch.drop_column("challenge_remember")
    with op.batch_alter_table("sessions") as batch:
        batch.drop_column("remember")
