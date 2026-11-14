from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from app.errors import ConflictError, NotFoundError
from app.github.client import GitHubClient
from app.models import Repository, WorkflowRun


def sync_recent_runs(
    db: Session,
    github: GitHubClient,
    repository_id: int,
    *,
    limit: int,
    conclusion: str | None,
) -> list[WorkflowRun]:
    """Fetch the latest runs from GitHub, upsert them, and return them newest first.

    Runs are stored so later requests (logs, analysis) can find a run's repository from the
    run id alone.

    The write is a single INSERT ... ON CONFLICT DO UPDATE per run. The earlier
    "SELECT, then INSERT if missing" version raced when two requests listed the same
    repository at once (React StrictMode sends every effect's request twice in development):
    both saw "missing", both inserted, and the loser crashed with a unique-key violation.
    """
    repository = db.get(Repository, repository_id)
    if repository is None:
        raise NotFoundError(f"Repository {repository_id} is not registered.")

    gh_runs = github.list_workflow_runs(
        repository.owner, repository.name, per_page=limit, conclusion=conclusion
    )

    run_ids: list[int] = []
    for gh_run in gh_runs:
        _upsert_run(
            db,
            {
                "id": gh_run.id,
                "repository_id": repository.id,
                "name": gh_run.name[:255],
                "status": gh_run.status,
                "conclusion": gh_run.conclusion,
                "branch": gh_run.branch[:255] if gh_run.branch else None,
                "html_url": gh_run.html_url,
                "created_at": gh_run.created_at,
            },
        )
        run_ids.append(gh_run.id)
    db.commit()

    if not run_ids:
        return []
    # populate_existing: refresh any copies already in this session with the new values.
    rows = db.scalars(
        select(WorkflowRun)
        .where(WorkflowRun.id.in_(run_ids))
        .execution_options(populate_existing=True)
    )
    by_id = {run.id: run for run in rows}
    return [by_id[run_id] for run_id in run_ids]  # keep GitHub's newest-first order


def ensure_run_completed(db: Session, github: GitHubClient, run: WorkflowRun) -> None:
    """Raise 409 unless the run has completed. Re-checks GitHub when our copy says it hasn't,
    because the run may have finished since it was listed."""
    if run.status == "completed":
        return
    repository = run.repository
    fresh = github.get_workflow_run(repository.owner, repository.name, run.id)
    run.status, run.conclusion = fresh.status, fresh.conclusion
    db.commit()
    if run.status != "completed":
        raise ConflictError(
            f"Run {run.id} is '{run.status}'. Try again once the run has completed."
        )


def _upsert_run(db: Session, values: dict[str, Any]) -> None:
    # PostgreSQL in production; SQLite in tests. Both support the same ON CONFLICT syntax.
    dialect = db.get_bind().dialect.name
    insert = {"postgresql": postgresql.insert, "sqlite": sqlite.insert}.get(dialect)
    if insert is None:
        raise RuntimeError(f"Unsupported database dialect: {dialect}")
    statement = insert(WorkflowRun).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[WorkflowRun.__table__.c.id],
        set_={key: value for key, value in values.items() if key != "id"},
    )
    db.execute(statement)
