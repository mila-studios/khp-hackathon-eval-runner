"""Add app_settings key-value table for leaderboard mode etc.

Revision ID: 004
Revises: 003
Create Date: 2026-02-27
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
        "app_settings",
        sa.Column("key", sa.String, primary_key=True),
        sa.Column("value", sa.Text, nullable=False, server_default=""),
    )
    conn = op.get_bind()
    conn.execute(
        sa.text("INSERT INTO app_settings (key, value) VALUES (:k, :v)"),
        {"k": "leaderboard_mode", "v": "off"},
    )
    conn.execute(
        sa.text("INSERT INTO app_settings (key, value) VALUES (:k, :v)"),
        {"k": "official_run_id", "v": ""},
    )


def downgrade() -> None:
    op.drop_table("app_settings")
