from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import ConflictError
from app.github.client import GitHubClient
from app.models import Repository


def register_repository(db: Session, github: GitHubClient, owner: str, name: str) -> Repository:
    """Verify the repository exists on GitHub (and the token can see it), then store it."""
    _ensure_not_registered(db, f"{owner}/{name}")

    # Use GitHub's canonical owner/name (correct casing, follows renames/transfers).
    gh_repo = github.get_repository(owner, name)
    _ensure_not_registered(db, gh_repo.full_name)

    repository = Repository(owner=gh_repo.owner, name=gh_repo.name, full_name=gh_repo.full_name)
    db.add(repository)
    try:
        db.commit()
    except IntegrityError as exc:  # registered concurrently by another request
        db.rollback()
        raise ConflictError(f"Repository {gh_repo.full_name} is already registered.") from exc
    db.refresh(repository)
    return repository


def list_repositories(db: Session) -> list[Repository]:
    return list(db.scalars(select(Repository).order_by(Repository.id)))


def _ensure_not_registered(db: Session, full_name: str) -> None:
    existing = db.scalar(
        select(Repository).where(func.lower(Repository.full_name) == full_name.lower())
    )
    if existing is not None:
        raise ConflictError(
            f"Repository {existing.full_name} is already registered (id {existing.id})."
        )
