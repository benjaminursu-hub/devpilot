"""Create and read analyses. The AI work itself happens in the worker (app/worker.py)."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import ConflictError, NotFoundError, QueueUnavailableError
from app.github.client import GitHubClient
from app.models import Analysis
from app.schemas import AnalysisOut, AnalysisResult, AnalysisStartOut
from app.services.logs import get_known_run
from app.services.runs import ensure_run_completed
from app.task_queue import AnalysisQueue

ACTIVE_STATUSES = ("queued", "running")
NOT_ANALYZABLE = {"success", "skipped", "neutral"}

QUEUE_TIMEOUT_ERROR = (
    "No worker picked up this analysis in time. Check that the DevPilot worker is running, "
    "then request the analysis again."
)
RUN_TIMEOUT_ERROR = "The analysis did not finish in time. Request it again."


def start_analysis(
    db: Session, github: GitHubClient, queue: AnalysisQueue, settings: Settings, run_id: int
) -> AnalysisStartOut:
    run = get_known_run(db, run_id)
    ensure_run_completed(db, github, run)
    if run.conclusion in NOT_ANALYZABLE:
        raise ConflictError(
            f"Run {run_id} concluded '{run.conclusion}'. Only failed runs can be analyzed."
        )

    # Re-use an analysis that is already queued/running for this run instead of paying for
    # a second AI call (double clicks, two open tabs).
    existing = db.scalar(
        select(Analysis)
        .where(Analysis.run_id == run_id, Analysis.status.in_(ACTIVE_STATUSES))
        .order_by(Analysis.created_at.desc())
        .limit(1)
    )
    if existing is not None and not _expire_if_stale(db, existing, settings):
        return AnalysisStartOut(analysis_id=existing.id, run_id=run_id, status=existing.status)

    analysis = Analysis(id=f"analysis-{uuid.uuid4().hex}", run_id=run_id, status="queued")
    db.add(analysis)
    db.commit()

    try:
        queue.enqueue(analysis.id)
    except QueueUnavailableError:
        analysis.status = "failed"
        analysis.error = "The analysis could not be queued because Redis was unavailable."
        db.commit()
        raise

    return AnalysisStartOut(analysis_id=analysis.id, run_id=run_id, status="queued")


def get_analysis(db: Session, settings: Settings, analysis_id: str) -> AnalysisOut:
    analysis = db.get(Analysis, analysis_id)
    if analysis is None:
        raise NotFoundError(f"Analysis {analysis_id} was not found.")
    _expire_if_stale(db, analysis, settings)

    result = None
    if analysis.status == "completed" and analysis.result_json is not None:
        result = AnalysisResult.model_validate(analysis.result_json)
    return AnalysisOut(
        id=analysis.id,
        run_id=analysis.run_id,
        status=analysis.status,
        result=result,
        error=analysis.error if analysis.status == "failed" else None,
    )


def _expire_if_stale(db: Session, analysis: Analysis, settings: Settings) -> bool:
    """Mark a queued/running analysis failed if it has clearly been abandoned.

    Covers a worker that is not running (stuck in "queued") and a job killed by its hard
    timeout or a worker crash (stuck in "running"). Returns True if it was expired.
    The UPDATE is conditional on the status, so it never overwrites a result the worker
    saved in the meantime.
    """
    if analysis.status == "queued":
        limit, message = settings.analysis_queue_timeout_seconds, QUEUE_TIMEOUT_ERROR
    elif analysis.status == "running":
        limit, message = settings.analysis_job_timeout_seconds + 120, RUN_TIMEOUT_ERROR
    else:
        return False

    if _as_utc(analysis.updated_at) > datetime.now(UTC) - timedelta(seconds=limit):
        return False

    expired = db.execute(
        update(Analysis)
        .where(Analysis.id == analysis.id, Analysis.status == analysis.status)
        .values(status="failed", error=message)
    ).rowcount
    db.commit()
    db.refresh(analysis)
    return bool(expired)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
