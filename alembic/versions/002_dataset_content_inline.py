"""Store dataset CSV content inline in DB instead of file_path.

Revision ID: 002
Revises: 001
Create Date: 2026-02-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("datasets", sa.Column("content", sa.Text, nullable=True))

    # Backfill: copy file contents into the new column for any existing rows.
    # This is a best-effort migration — if the file is missing it sets content
    # to an empty string so the NOT NULL constraint can be applied.
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, file_path FROM datasets")).fetchall()
    for row_id, file_path in rows:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = f.read()
        except Exception:
            data = ""
        conn.execute(
            sa.text("UPDATE datasets SET content = :content WHERE id = :id"),
            {"content": data, "id": row_id},
        )

    op.alter_column("datasets", "content", nullable=False)
    op.drop_column("datasets", "file_path")


def downgrade() -> None:
    op.add_column("datasets", sa.Column("file_path", sa.Text, nullable=True))
    op.alter_column("datasets", "file_path", nullable=False, server_default="")
    op.drop_column("datasets", "content")
