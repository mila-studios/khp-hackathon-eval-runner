"""Drop unused needs_gpu column from teams table.

Revision ID: 003
Revises: 002
Create Date: 2026-02-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("teams", "needs_gpu")


def downgrade() -> None:
    op.add_column("teams", sa.Column("needs_gpu", sa.Boolean, nullable=True))
