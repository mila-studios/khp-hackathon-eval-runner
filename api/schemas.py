from __future__ import annotations

from typing import Annotated, Dict, List, Optional, Union
from uuid import UUID

from pydantic import BaseModel, BeforeValidator

StrUUID = Annotated[str, BeforeValidator(lambda v: str(v) if isinstance(v, UUID) else v)]


# ── Teams ────────────────────────────────────────────────────────

class TeamCreate(BaseModel):
    team_id: str
    git_url: str

class TeamUpdate(BaseModel):
    git_url: str

class TeamOut(BaseModel):
    team_id: str
    git_url: str
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


# ── Datasets ─────────────────────────────────────────────────────

class DatasetCreate(BaseModel):
    name: str
    description: Optional[str] = None
    is_public_test: bool = False

class DatasetOut(BaseModel):
    id: StrUUID
    name: str
    description: Optional[str] = None
    row_count: Optional[int] = None
    is_public_test: bool
    created_at: str

    model_config = {"from_attributes": True}


# ── Jobs ─────────────────────────────────────────────────────────

class JobCreate(BaseModel):
    run_id: Optional[str] = None
    team_ids: Union[List[str], str]  # list of team_ids or "all"
    dataset_id: str
    fail_fast: bool = False

class JobOut(BaseModel):
    job_id: str
    run_id: str

class JobDetail(BaseModel):
    id: StrUUID
    run_id: str
    status: str
    triggered_by: str
    dataset_id: StrUUID
    fail_fast: bool
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    teams: List[JobTeamDetail] = []

class JobTeamDetail(BaseModel):
    id: StrUUID
    team_id: str
    status: str
    current_stage: Optional[str] = None
    failed_stage: Optional[str] = None
    elapsed_s: Optional[float] = None
    error: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None

    model_config = {"from_attributes": True}

# Rebuild JobDetail now that JobTeamDetail is defined
JobDetail.model_rebuild()


class JobListItem(BaseModel):
    id: StrUUID
    run_id: str
    status: str
    triggered_by: str
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


# ── Runs (campaign view) ────────────────────────────────────────

class RunSummary(BaseModel):
    run_id: str
    job_count: int
    team_count: int
    ok_count: int
    failed_count: int

class RunTeamResult(BaseModel):
    run_id: str
    team_id: str
    job_id: str
    job_team_id: str
    status: str
    failed_stage: Optional[str] = None
    elapsed_s: Optional[float] = None
    error: Optional[str] = None
    completed_at: Optional[str] = None


# ── Metrics ──────────────────────────────────────────────────────

class MetricsOut(BaseModel):
    precision: float
    recall: float
    f1: float
    support_harmful: int
    support_safe: int
    total_samples: int
    latency_ms_mean: Optional[float] = None
    latency_ms_total: Optional[float] = None

    model_config = {"from_attributes": True}


# ── Public API ───────────────────────────────────────────────────

class PublicEvalRequest(BaseModel):
    team_id: str

class PublicEvalResponse(BaseModel):
    job_id: str

class PublicJobStatus(BaseModel):
    job_id: str
    status: str
    team_id: str
    current_stage: Optional[str] = None
    stage_statuses: Dict[str, str] = {}
    metrics: Optional[MetricsOut] = None
