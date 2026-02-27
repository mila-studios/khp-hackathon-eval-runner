from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "teams"

    team_id = Column(String, primary_key=True)
    git_url = Column(Text, nullable=False)
    created_at = Column(
        String, nullable=False, default=lambda: _utcnow().isoformat()
    )
    updated_at = Column(
        String, nullable=False, default=lambda: _utcnow().isoformat(), onupdate=lambda: _utcnow().isoformat()
    )

    job_teams = relationship("JobTeam", back_populates="team")


class Dataset(Base):
    __tablename__ = "datasets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text, nullable=True)
    content = Column(Text, nullable=False)
    row_count = Column(Integer, nullable=True)
    is_public_test = Column(Boolean, nullable=False, default=False)
    created_at = Column(
        String, nullable=False, default=lambda: _utcnow().isoformat()
    )

    jobs = relationship("Job", back_populates="dataset")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    run_id = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="PENDING")
    triggered_by = Column(String, nullable=False)  # admin | public
    dataset_id = Column(UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False)
    fail_fast = Column(Boolean, nullable=False, default=False)
    created_at = Column(
        String, nullable=False, default=lambda: _utcnow().isoformat()
    )
    started_at = Column(String, nullable=True)
    completed_at = Column(String, nullable=True)

    dataset = relationship("Dataset", back_populates="jobs")
    job_teams = relationship("JobTeam", back_populates="job", cascade="all, delete-orphan")


class JobTeam(Base):
    __tablename__ = "job_teams"
    __table_args__ = (UniqueConstraint("job_id", "team_id", name="uq_job_team"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=False)
    team_id = Column(String, ForeignKey("teams.team_id"), nullable=False)
    status = Column(String, nullable=False, default="PENDING")
    current_stage = Column(String, nullable=True)
    failed_stage = Column(String, nullable=True)
    elapsed_s = Column(Float, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(
        String, nullable=False, default=lambda: _utcnow().isoformat()
    )
    completed_at = Column(String, nullable=True)

    job = relationship("Job", back_populates="job_teams")
    team = relationship("Team", back_populates="job_teams")
    logs = relationship("TeamRunLog", back_populates="job_team", cascade="all, delete-orphan")
    metrics = relationship("TeamRunMetric", back_populates="job_team", uselist=False, cascade="all, delete-orphan")
    artifacts = relationship("TeamRunArtifact", back_populates="job_team", cascade="all, delete-orphan")


class TeamRunLog(Base):
    __tablename__ = "team_run_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    job_team_id = Column(UUID(as_uuid=True), ForeignKey("job_teams.id"), nullable=False)
    stage = Column(String, nullable=False)
    success = Column(Boolean, nullable=False)
    log_content = Column(Text, nullable=True)
    created_at = Column(
        String, nullable=False, default=lambda: _utcnow().isoformat()
    )

    job_team = relationship("JobTeam", back_populates="logs")


class TeamRunMetric(Base):
    __tablename__ = "team_run_metrics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    job_team_id = Column(UUID(as_uuid=True), ForeignKey("job_teams.id"), unique=True, nullable=False)
    precision = Column(Float, nullable=False)
    recall = Column(Float, nullable=False)
    f1 = Column(Float, nullable=False)
    support_harmful = Column(Integer, nullable=False)
    support_safe = Column(Integer, nullable=False)
    total_samples = Column(Integer, nullable=False)
    latency_ms_mean = Column(Float, nullable=True)
    latency_ms_total = Column(Float, nullable=True)
    created_at = Column(
        String, nullable=False, default=lambda: _utcnow().isoformat()
    )

    job_team = relationship("JobTeam", back_populates="metrics")


class TeamRunArtifact(Base):
    __tablename__ = "team_run_artifacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    job_team_id = Column(UUID(as_uuid=True), ForeignKey("job_teams.id"), nullable=False)
    artifact_type = Column(String, nullable=False)  # predictions | metrics_csv | eval_metrics_json
    content = Column(Text, nullable=True)
    created_at = Column(
        String, nullable=False, default=lambda: _utcnow().isoformat()
    )

    job_team = relationship("JobTeam", back_populates="artifacts")
