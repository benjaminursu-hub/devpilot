"""Background worker: runs queued AI analyses.

Start with `python -m app.worker` (Docker Compose does this in the `worker` service).
RQ forks a child process per job and kills it after ANALYSIS_JOB_TIMEOUT_SECONDS; the API
marks such abandoned analyses as failed (see services/analyses.py).

Safety: the worker only reads logs and calls the AI provider. It never executes anything the
model or the log suggests, and it never writes to GitHub.
"""

import time
from collections.abc import Callable
from dataclasses import replace

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from app.ai.output import InvalidAIOutputError, parse_analysis_output
from app.ai.prompt import RunContext
from app.ai.providers import AIProvider, AIProviderError, build_ai_provider
from app.config import Settings, get_settings
from app.db import get_session_factory
from app.errors import (
    ApiError,
    GitHubError,
    GitHubRateLimitError,
    GitHubTimeoutError,
    LogArchiveError,
)
from app.github.client import GitHubClient
from app.logging_setup import logger
from app.models import Analysis
from app.schemas import AnalysisResult
from app.services.logs import get_run_logs
from app.task_queue import QUEUE_NAME

GITHUB_ATTEMPTS = 3
MAX_BACKOFF_SECONDS = 30.0


def process_analysis(analysis_id: str) -> None:
    """RQ entry point (referenced by name in app/task_queue.py)."""
    settings = get_settings()
    try:
        provider = build_ai_provider(settings)
    except AIProviderError as exc:  # e.g. AI_PROVIDER=anthropic without AI_API_KEY
        _finish(get_session_factory(), analysis_id, error=str(exc))
        return
    try:
        github = GitHubClient.from_settings(settings)
    except ApiError as exc:
        _finish(get_session_factory(), analysis_id, error=exc.detail)
        return
    try:
        run_analysis(
            analysis_id,
            session_factory=get_session_factory(),
            github=github,
            provider=provider,
            settings=settings,
        )
    finally:
        github.close()


def run_analysis(
    analysis_id: str,
    *,
    session_factory: sessionmaker[Session],
    github: GitHubClient,
    provider: AIProvider,
    settings: Settings,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Claim the analysis, fetch logs, ask the model, validate, save. Never raises for
    expected failures — those end as status "failed" with a readable `error`."""
    if not _claim(session_factory, analysis_id):
        logger.info("Analysis %s is no longer queued; skipping.", analysis_id)
        return

    logger.info("Analysis %s started (provider=%s).", analysis_id, provider.name)
    try:
        result = _analyze(analysis_id, session_factory, github, provider, settings, sleep)
    except ApiError as exc:  # GitHub / log problems (already user-readable)
        _finish(session_factory, analysis_id, error=exc.detail)
    except AIProviderError as exc:
        _finish(session_factory, analysis_id, error=f"AI provider error: {exc}")
    except InvalidAIOutputError:
        _finish(
            session_factory,
            analysis_id,
            error=(
                f"The AI model did not return a valid report after {settings.ai_max_attempts} "
                "attempt(s)."
            ),
        )
    except Exception:
        logger.exception("Analysis %s crashed.", analysis_id)
        _finish(session_factory, analysis_id, error="Unexpected error while analyzing the run.")
    else:
        _finish(session_factory, analysis_id, result=result)
        logger.info("Analysis %s completed.", analysis_id)


def _analyze(
    analysis_id: str,
    session_factory: sessionmaker[Session],
    github: GitHubClient,
    provider: AIProvider,
    settings: Settings,
    sleep: Callable[[float], None],
) -> AnalysisResult:
    with session_factory() as db:
        analysis = db.get(Analysis, analysis_id)
        run = analysis.run
        context = RunContext(
            repository=run.repository.full_name,
            workflow=run.name,
            branch=run.branch,
            conclusion=run.conclusion,
            logs_truncated=False,
        )
        logs = _with_retries(
            lambda: get_run_logs(db, github, run.id, max_chars=settings.ai_log_max_chars),
            attempts=GITHUB_ATTEMPTS,
            is_transient=_is_transient_github_error,
            sleep=sleep,
        )
    context = replace(context, logs_truncated=logs.truncated)

    def ask_model() -> AnalysisResult:
        reply = provider.generate(context, logs.logs)
        return parse_analysis_output(reply, logs.logs)

    return _with_retries(
        ask_model,
        attempts=settings.ai_max_attempts,
        is_transient=_is_transient_ai_error,
        sleep=sleep,
    )


def _with_retries(func, *, attempts: int, is_transient, sleep):
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:
            if attempt == attempts or not is_transient(exc):
                raise
            delay = min(2.0**attempt, MAX_BACKOFF_SECONDS)
            retry_after = getattr(exc, "retry_after", None)
            if retry_after:
                delay = min(max(delay, retry_after), 60.0)
            logger.warning(
                "Attempt %d/%d failed (%s); retrying in %.0fs.",
                attempt,
                attempts,
                type(exc).__name__,
                delay,
            )
            sleep(delay)
    raise AssertionError("unreachable")


def _is_transient_ai_error(exc: Exception) -> bool:
    # Invalid JSON is often a one-off; asking again usually fixes it.
    return isinstance(exc, InvalidAIOutputError) or (
        isinstance(exc, AIProviderError) and exc.transient
    )


def _is_transient_github_error(exc: Exception) -> bool:
    if isinstance(exc, (GitHubTimeoutError, GitHubRateLimitError)):
        return True
    # Plain GitHubError = network failure or 5xx. Subclasses (404, 401, 410...) are final.
    return type(exc) is GitHubError and not isinstance(exc, LogArchiveError)


def _claim(session_factory: sessionmaker[Session], analysis_id: str) -> bool:
    """queued → running, atomically. False if someone else claimed or it was expired."""
    with session_factory() as db:
        claimed = db.execute(
            update(Analysis)
            .where(Analysis.id == analysis_id, Analysis.status == "queued")
            .values(status="running")
        ).rowcount
        db.commit()
    return bool(claimed)


def _finish(
    session_factory: sessionmaker[Session],
    analysis_id: str,
    *,
    result: AnalysisResult | None = None,
    error: str | None = None,
) -> None:
    values = (
        {"status": "completed", "result_json": result.model_dump(), "error": None}
        if result is not None
        else {"status": "failed", "result_json": None, "error": (error or "Failed.")[:2000]}
    )
    with session_factory() as db:
        # Only finish analyses that are still active, so a late job can't overwrite the
        # "failed" state the API gave an analysis that timed out.
        db.execute(
            update(Analysis)
            .where(Analysis.id == analysis_id, Analysis.status.in_(("queued", "running")))
            .values(**values)
        )
        db.commit()
    if error:
        logger.warning("Analysis %s failed: %s", analysis_id, error)


def main() -> None:
    from redis import Redis
    from rq import Queue, Worker

    settings = get_settings()
    connection = Redis.from_url(settings.redis_url)
    logger.info("Worker listening on queue %r (AI provider: %s).", QUEUE_NAME, settings.ai_provider)
    Worker([Queue(QUEUE_NAME, connection=connection)], connection=connection).work()


if __name__ == "__main__":
    main()
