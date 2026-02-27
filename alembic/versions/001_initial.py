"""Initial schema: teams, datasets, jobs, job_teams, logs, metrics, artifacts + campaign view.

Revision ID: 001
Revises: None
Create Date: 2026-02-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("team_id", sa.String, primary_key=True),
        sa.Column("git_url", sa.Text, nullable=False),
        sa.Column("needs_gpu", sa.Boolean, nullable=True),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("updated_at", sa.String, nullable=False),
    )

    op.create_table(
        "datasets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String, unique=True, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("row_count", sa.Integer, nullable=True),
        sa.Column("is_public_test", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.String, nullable=False),
    )

    op.create_table(
        "jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", sa.String, nullable=False, index=True),
        sa.Column("status", sa.String, nullable=False, server_default="PENDING"),
        sa.Column("triggered_by", sa.String, nullable=False),
        sa.Column("dataset_id", UUID(as_uuid=True), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("fail_fast", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("started_at", sa.String, nullable=True),
        sa.Column("completed_at", sa.String, nullable=True),
    )

    op.create_table(
        "job_teams",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("team_id", sa.String, sa.ForeignKey("teams.team_id"), nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="PENDING"),
        sa.Column("current_stage", sa.String, nullable=True),
        sa.Column("failed_stage", sa.String, nullable=True),
        sa.Column("elapsed_s", sa.Float, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("completed_at", sa.String, nullable=True),
        sa.UniqueConstraint("job_id", "team_id", name="uq_job_team"),
    )

    op.create_table(
        "team_run_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("job_team_id", UUID(as_uuid=True), sa.ForeignKey("job_teams.id"), nullable=False),
        sa.Column("stage", sa.String, nullable=False),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("log_content", sa.Text, nullable=True),
        sa.Column("created_at", sa.String, nullable=False),
    )

    op.create_table(
        "team_run_metrics",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("job_team_id", UUID(as_uuid=True), sa.ForeignKey("job_teams.id"), unique=True, nullable=False),
        sa.Column("precision", sa.Float, nullable=False),
        sa.Column("recall", sa.Float, nullable=False),
        sa.Column("f1", sa.Float, nullable=False),
        sa.Column("support_harmful", sa.Integer, nullable=False),
        sa.Column("support_safe", sa.Integer, nullable=False),
        sa.Column("total_samples", sa.Integer, nullable=False),
        sa.Column("latency_ms_mean", sa.Float, nullable=True),
        sa.Column("latency_ms_total", sa.Float, nullable=True),
        sa.Column("created_at", sa.String, nullable=False),
    )

    op.create_table(
        "team_run_artifacts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("job_team_id", UUID(as_uuid=True), sa.ForeignKey("job_teams.id"), nullable=False),
        sa.Column("artifact_type", sa.String, nullable=False),
        sa.Column("content", sa.Text, nullable=True),
        sa.Column("created_at", sa.String, nullable=False),
    )

    op.execute("""
        CREATE VIEW latest_team_results_by_run AS
        SELECT DISTINCT ON (j.run_id, jt.team_id)
            j.run_id,
            jt.team_id,
            j.id           AS job_id,
            jt.id          AS job_team_id,
            jt.status,
            jt.failed_stage,
            jt.elapsed_s,
            jt.error,
            jt.completed_at
        FROM job_teams jt
        JOIN jobs j ON j.id = jt.job_id
        ORDER BY j.run_id, jt.team_id, jt.completed_at DESC NULLS LAST
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS latest_team_results_by_run")
    op.drop_table("team_run_artifacts")
    op.drop_table("team_run_metrics")
    op.drop_table("team_run_logs")
    op.drop_table("job_teams")
    op.drop_table("jobs")
    op.drop_table("datasets")
    op.drop_table("teams")
