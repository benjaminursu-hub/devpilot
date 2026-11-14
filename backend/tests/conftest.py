"""Shared test fixtures.

Tests run against in-memory SQLite and a fake GitHub client, so they need no network,
no token and no Postgres. CI additionally applies the Alembic migrations to real Postgres.
"""

import io
import os
import zipfile
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401  (registers tables)
from app.api.deps import get_analysis_queue, get_github_client
from app.config import Settings, get_settings
from app.db import Base, get_db
from app.errors import GitHubLogsGoneError, GitHubNotFoundError, QueueUnavailableError
from app.github.client import GitHubRepository, GitHubWorkflowRun
from app.main import create_app


class FakeGitHub:
    """In-memory stand-in for GitHubClient with the same public methods."""

    def __init__(self) -> None:
        self.repositories: dict[str, GitHubRepository] = {}
        self.runs: dict[str, list[GitHubWorkflowRun]] = {}
        self.logs: dict[int, bytes] = {}
        self.expired_logs: set[int] = set()
        self.calls: list[tuple] = []

    # setup helpers
    def add_repository(self, owner: str, name: str) -> GitHubRepository:
        repo = GitHubRepository(owner=owner, name=name, full_name=f"{owner}/{name}")
        self.repositories[repo.full_name.lower()] = repo
        self.runs.setdefault(repo.full_name.lower(), [])
        return repo

    def add_run(self, full_name: str, run: GitHubWorkflowRun, logs: bytes | None = None) -> None:
        self.runs[full_name.lower()].insert(0, run)  # newest first, like GitHub
        if logs is not None:
            self.logs[run.id] = logs

    def complete_run(self, run_id: int, conclusion: str) -> None:
        for key, runs in self.runs.items():
            self.runs[key] = [
                replace(r, status="completed", conclusion=conclusion) if r.id == run_id else r
                for r in runs
            ]

    # GitHubClient interface
    def get_repository(self, owner: str, name: str) -> GitHubRepository:
        self.calls.append(("get_repository", owner, name))
        try:
            return self.repositories[f"{owner}/{name}".lower()]
        except KeyError:
            message = f"Repository {owner}/{name} was not found on GitHub."
            raise GitHubNotFoundError(message) from None

    def list_workflow_runs(self, owner, name, *, per_page=20, conclusion=None):
        self.calls.append(("list_workflow_runs", owner, name, per_page, conclusion))
        runs = self.runs.get(f"{owner}/{name}".lower(), [])
        if conclusion:
            runs = [r for r in runs if r.conclusion == conclusion]
        return runs[:per_page]

    def get_workflow_run(self, owner, name, run_id):
        self.calls.append(("get_workflow_run", owner, name, run_id))
        for run in self.runs.get(f"{owner}/{name}".lower(), []):
            if run.id == run_id:
                return run
        raise GitHubNotFoundError(f"Run {run_id} was not found on GitHub.")

    def download_run_logs(self, owner, name, run_id):
        self.calls.append(("download_run_logs", owner, name, run_id))
        if run_id in self.expired_logs:
            raise GitHubLogsGoneError()
        if run_id not in self.logs:
            raise GitHubNotFoundError(f"Logs for run {run_id} were not found on GitHub.")
        return self.logs[run_id]


def make_run(
    run_id: int,
    *,
    conclusion: str | None = "failure",
    status: str = "completed",
    name: str = "Build and Test",
    branch: str | None = "main",
) -> GitHubWorkflowRun:
    return GitHubWorkflowRun(
        id=run_id,
        name=name,
        status=status,
        conclusion=conclusion,
        branch=branch,
        html_url=f"https://github.com/example-user/example-project/actions/runs/{run_id}",
        created_at=datetime(2026, 11, 7, 12, 0, tzinfo=UTC),
    )


def make_log_zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buffer.getvalue()


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture(autouse=True)
def _isolate_settings_from_environment(monkeypatch) -> Iterator[None]:
    """Hide the developer's DevPilot configuration from every test.

    `Settings(_env_file=None, ...)` stops pydantic-settings reading `.env` files, but it still
    reads process environment variables, and Docker Compose injects `.env` into the container
    via `env_file`. A local `AI_BASE_URL`, `AI_API_KEY` or `AI_MODEL` (e.g. for Ollama) then
    leaks into any test that relies on a default. Remove every variable that maps to a
    Settings field (pydantic-settings matches names case-insensitively), and drop the cached
    `get_settings()` so nothing built from the real environment survives into a test.
    """
    fields = set(Settings.model_fields)
    for name in list(os.environ):
        if name.lower() in fields:
            monkeypatch.delenv(name)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fake_github() -> FakeGitHub:
    return FakeGitHub()


class FakeQueue:
    """Records enqueued analysis ids instead of talking to Redis."""

    def __init__(self) -> None:
        self.enqueued: list[str] = []
        self.fail = False

    def enqueue(self, analysis_id: str) -> None:
        if self.fail:
            raise QueueUnavailableError()
        self.enqueued.append(analysis_id)


@pytest.fixture
def fake_queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
def settings() -> Settings:
    # _env_file=None: tests must not pick up the developer's real .env (tokens, AI provider).
    return Settings(
        _env_file=None,
        github_token="test-token",
        log_max_chars=5_000,
        ai_provider="heuristic",
        ai_api_key=None,
        ai_model=None,
        ai_max_attempts=3,
        ai_log_max_chars=5_000,
    )


@pytest.fixture
def client(session_factory, fake_github, fake_queue, settings, monkeypatch) -> Iterator[TestClient]:
    # create_app() reads settings directly (for CORS), not through the dependency override,
    # so point it at the test settings too; otherwise a local .env could change CORS origins.
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    app = create_app()

    def _db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_github_client] = lambda: fake_github
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_analysis_queue] = lambda: fake_queue
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def registered_repo(client, fake_github) -> dict:
    fake_github.add_repository("example-user", "example-project")
    response = client.post(
        "/api/v1/repositories", json={"owner": "example-user", "name": "example-project"}
    )
    assert response.status_code == 201
    return response.json()
