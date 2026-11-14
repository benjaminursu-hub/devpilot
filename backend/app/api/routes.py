"""HTTP routes for /api/v1. Routes stay thin: validate input, call a service, return a schema."""

from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.api.deps import AppSettings, DbSession, GitHub, Queue
from app.schemas import (
    AnalysisOut,
    AnalysisStartOut,
    ErrorResponse,
    HealthResponse,
    RepositoryCreate,
    RepositoryOut,
    RunConclusion,
    RunLogsOut,
    WorkflowRunOut,
)
from app.services import analyses as analyses_service
from app.services import logs as logs_service
from app.services import repositories as repositories_service
from app.services import runs as runs_service

router = APIRouter()

_GITHUB_ERRORS: dict[int | str, dict] = {
    502: {"model": ErrorResponse, "description": "GitHub rejected the request or failed"},
    503: {"model": ErrorResponse, "description": "GITHUB_TOKEN missing or rate limit reached"},
    504: {"model": ErrorResponse, "description": "GitHub timed out"},
}


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.post(
    "/repositories",
    response_model=RepositoryOut,
    status_code=status.HTTP_201_CREATED,
    tags=["repositories"],
    responses={
        404: {"model": ErrorResponse, "description": "Repository not found on GitHub"},
        409: {"model": ErrorResponse, "description": "Repository already registered"},
        422: {"model": ErrorResponse, "description": "Invalid owner or name"},
        **_GITHUB_ERRORS,
    },
)
def register_repository(payload: RepositoryCreate, db: DbSession, github: GitHub):
    return repositories_service.register_repository(db, github, payload.owner, payload.name)


@router.get("/repositories", response_model=list[RepositoryOut], tags=["repositories"])
def list_repositories(db: DbSession):
    return repositories_service.list_repositories(db)


@router.get(
    "/repositories/{repository_id}/runs",
    response_model=list[WorkflowRunOut],
    tags=["runs"],
    responses={404: {"model": ErrorResponse}, **_GITHUB_ERRORS},
)
def list_runs(
    repository_id: Annotated[int, Path(ge=1)],
    db: DbSession,
    github: GitHub,
    conclusion: Annotated[
        RunConclusion | None,
        Query(description="Only return runs with this conclusion, e.g. 'failure'."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Number of recent runs.")] = 20,
):
    return runs_service.sync_recent_runs(
        db, github, repository_id, limit=limit, conclusion=conclusion
    )


@router.get(
    "/runs/{run_id}/logs",
    response_model=RunLogsOut,
    tags=["runs"],
    responses={
        404: {"model": ErrorResponse, "description": "Run unknown to DevPilot or GitHub"},
        409: {"model": ErrorResponse, "description": "Run has not completed yet"},
        410: {"model": ErrorResponse, "description": "Logs expired on GitHub"},
        **_GITHUB_ERRORS,
    },
)
def get_run_logs(
    run_id: Annotated[int, Path(ge=1)], db: DbSession, github: GitHub, settings: AppSettings
):
    return logs_service.get_run_logs(db, github, run_id, max_chars=settings.log_max_chars)


@router.post(
    "/runs/{run_id}/analyze",
    response_model=AnalysisStartOut,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["analyses"],
    responses={
        404: {"model": ErrorResponse, "description": "Run unknown to DevPilot or GitHub"},
        409: {"model": ErrorResponse, "description": "Run not completed, or it succeeded"},
        **_GITHUB_ERRORS,
        503: {"model": ErrorResponse, "description": "Token missing, rate limit, or Redis down"},
    },
)
def analyze_run(
    run_id: Annotated[int, Path(ge=1)],
    db: DbSession,
    github: GitHub,
    queue: Queue,
    settings: AppSettings,
):
    """Queue an AI analysis of a failed run. Poll GET /analyses/{analysis_id} for the result.
    If an analysis of this run is already queued or running, that one is returned."""
    return analyses_service.start_analysis(db, github, queue, settings, run_id)


@router.get(
    "/analyses/{analysis_id}",
    response_model=AnalysisOut,
    tags=["analyses"],
    responses={404: {"model": ErrorResponse, "description": "Analysis not found"}},
)
def get_analysis(
    analysis_id: Annotated[str, Path(max_length=64)], db: DbSession, settings: AppSettings
):
    return analyses_service.get_analysis(db, settings, analysis_id)
