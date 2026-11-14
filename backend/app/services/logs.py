from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.github.client import GitHubClient
from app.log_processing import combine_log_files, extract_log_files, truncate_keep_tail
from app.models import WorkflowRun
from app.redaction import redact_secrets
from app.schemas import RunLogsOut
from app.services.runs import ensure_run_completed


def get_known_run(db: Session, run_id: int) -> WorkflowRun:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise NotFoundError(
            f"Run {run_id} is not known to DevPilot. List its repository's runs first "
            "(GET /api/v1/repositories/{repository_id}/runs)."
        )
    return run


def get_run_logs(db: Session, github: GitHubClient, run_id: int, *, max_chars: int) -> RunLogsOut:
    run = get_known_run(db, run_id)
    ensure_run_completed(db, github, run)

    repository = run.repository
    archive = github.download_run_logs(repository.owner, repository.name, run.id)

    # Keep a generous per-file tail (4 bytes per char covers any UTF-8) — the final cut
    # to max_chars happens below on the combined text.
    files, cut_while_reading = extract_log_files(archive, keep_bytes_per_file=max_chars * 4)

    # Redact BEFORE truncating, so a secret split by the cut can't slip through half-hidden.
    text = redact_secrets(combine_log_files(files))
    text, cut = truncate_keep_tail(text, max_chars)

    return RunLogsOut(run_id=run.id, logs=text, truncated=cut or cut_while_reading)
