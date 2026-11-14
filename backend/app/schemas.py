"""Request/response shapes. These ARE the shared API contract — change with care."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# GitHub's rules: owner = alphanumerics and single hyphens (max 39);
# repo name = alphanumerics, '.', '_', '-' (max 100). Validating this also guarantees
# the values are safe to put into GitHub API URL paths.
OWNER_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$"
NAME_PATTERN = r"^[A-Za-z0-9._-]{1,100}$"

RunConclusion = Literal[
    "failure", "success", "cancelled", "timed_out", "action_required", "skipped", "neutral"
]


class ErrorResponse(BaseModel):
    detail: str


class HealthResponse(BaseModel):
    status: Literal["ok"]


class RepositoryCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    owner: str = Field(pattern=OWNER_PATTERN, examples=["example-user"])
    name: str = Field(pattern=NAME_PATTERN, examples=["example-project"])

    @field_validator("name")
    @classmethod
    def _not_dot_path(cls, value: str) -> str:
        if value in {".", ".."}:
            raise ValueError("is not a valid repository name")
        return value


class RepositoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner: str
    name: str
    full_name: str


class WorkflowRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    repository_id: int
    name: str
    status: str
    conclusion: str | None
    branch: str | None
    html_url: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _as_utc(cls, value: datetime) -> datetime:
        # Always serialize as UTC ("...Z"), whatever the database driver hands back.
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class RunLogsOut(BaseModel):
    run_id: int
    logs: str
    truncated: bool


# --- Analyses ----------------------------------------------------------------------------------

AnalysisStatus = Literal["queued", "running", "completed", "failed"]
Confidence = Literal["low", "medium", "high"]


class AnalysisResult(BaseModel):
    """The AI report. `confidence` is the model's own assessment, not a calibrated probability."""

    summary: str = Field(min_length=1, max_length=2_000)
    possible_causes: list[str] = Field(max_length=10)
    evidence: list[str] = Field(max_length=10)
    suggested_fixes: list[str] = Field(max_length=10)
    confidence: Confidence


class AnalysisStartOut(BaseModel):
    analysis_id: str
    run_id: int
    status: AnalysisStatus


class AnalysisOut(BaseModel):
    id: str
    run_id: int
    status: AnalysisStatus
    result: AnalysisResult | None  # null until status is "completed"
    error: str | None  # set only when status is "failed"
