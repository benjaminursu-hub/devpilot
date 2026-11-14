"""Worker behaviour: retries, timeouts, validation failures, and state guarding."""

import json

import pytest
from conftest import make_log_zip, make_run

from app.ai.providers import AIProviderError
from app.errors import GitHubTimeoutError
from app.models import Analysis, Repository, WorkflowRun
from app.worker import run_analysis

LOG = "Step 3/7: mvn package\n[ERROR] package example does not exist\n"

GOOD_REPLY = json.dumps(
    {
        "summary": "Compilation failed because a dependency is missing.",
        "possible_causes": ["A required dependency is not declared."],
        "evidence": ["package example does not exist"],
        "suggested_fixes": ["Check the project's dependency configuration."],
        "confidence": "medium",
    }
)


class ScriptedProvider:
    """Returns (or raises) the scripted items in order."""

    name = "scripted"

    def __init__(self, *script):
        self.script = list(script)
        self.calls = 0

    def generate(self, run, logs):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def queued_analysis(session_factory, fake_github):
    fake_github.add_repository("example-user", "example-project")
    fake_github.add_run(
        "example-user/example-project",
        make_run(1),
        logs=make_log_zip({"0_build.txt": LOG}),
    )
    with session_factory() as db:
        repo = Repository(
            owner="example-user", name="example-project", full_name="example-user/example-project"
        )
        db.add(repo)
        db.flush()
        run = make_run(1)
        db.add(
            WorkflowRun(
                id=1,
                repository_id=repo.id,
                name=run.name,
                status=run.status,
                conclusion=run.conclusion,
                branch=run.branch,
                html_url=run.html_url,
                created_at=run.created_at,
            )
        )
        db.add(Analysis(id="analysis-1", run_id=1, status="queued"))
        db.commit()
    return "analysis-1"


def _run(queued_analysis, session_factory, fake_github, settings, provider, sleeps=None):
    run_analysis(
        queued_analysis,
        session_factory=session_factory,
        github=fake_github,
        provider=provider,
        settings=settings,
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
    )
    with session_factory() as db:
        return db.get(Analysis, queued_analysis)


def test_success_stores_validated_result(queued_analysis, session_factory, fake_github, settings):
    analysis = _run(
        queued_analysis, session_factory, fake_github, settings, ScriptedProvider(GOOD_REPLY)
    )

    assert analysis.status == "completed"
    assert analysis.error is None
    assert analysis.result_json["confidence"] == "medium"
    assert analysis.result_json["evidence"] == ["package example does not exist"]


def test_transient_errors_are_retried_with_backoff(
    queued_analysis, session_factory, fake_github, settings
):
    provider = ScriptedProvider(
        AIProviderError("overloaded", transient=True),
        AIProviderError("rate limited", transient=True, retry_after=7),
        GOOD_REPLY,
    )
    sleeps: list[float] = []

    analysis = _run(queued_analysis, session_factory, fake_github, settings, provider, sleeps)

    assert analysis.status == "completed"
    assert provider.calls == 3
    assert sleeps == [2.0, 7.0]  # exponential backoff, then the provider's Retry-After


def test_permanent_error_fails_without_retry(
    queued_analysis, session_factory, fake_github, settings
):
    provider = ScriptedProvider(AIProviderError("HTTP 401: invalid x-api-key", transient=False))

    analysis = _run(queued_analysis, session_factory, fake_github, settings, provider)

    assert provider.calls == 1
    assert analysis.status == "failed"
    assert analysis.error == "AI provider error: HTTP 401: invalid x-api-key"
    assert analysis.result_json is None


def test_invalid_output_retried_then_failed(
    queued_analysis, session_factory, fake_github, settings
):
    provider = ScriptedProvider("not json", '{"summary": 1}', "still not json")

    analysis = _run(queued_analysis, session_factory, fake_github, settings, provider)

    assert provider.calls == settings.ai_max_attempts == 3
    assert analysis.status == "failed"
    assert "did not return a valid report" in analysis.error


def test_invalid_output_then_valid_succeeds(
    queued_analysis, session_factory, fake_github, settings
):
    provider = ScriptedProvider("Sure! Here you go:", GOOD_REPLY)
    analysis = _run(queued_analysis, session_factory, fake_github, settings, provider)
    assert analysis.status == "completed"


def test_github_timeout_retried(queued_analysis, session_factory, fake_github, settings):
    original = fake_github.download_run_logs
    attempts = []

    def flaky(*args):
        attempts.append(1)
        if len(attempts) == 1:
            raise GitHubTimeoutError()
        return original(*args)

    fake_github.download_run_logs = flaky
    analysis = _run(
        queued_analysis, session_factory, fake_github, settings, ScriptedProvider(GOOD_REPLY)
    )

    assert len(attempts) == 2
    assert analysis.status == "completed"


def test_expired_logs_fail_with_readable_error(
    queued_analysis, session_factory, fake_github, settings
):
    fake_github.expired_logs.add(1)
    provider = ScriptedProvider(GOOD_REPLY)

    analysis = _run(queued_analysis, session_factory, fake_github, settings, provider)

    assert provider.calls == 0
    assert analysis.status == "failed"
    assert "expired" in analysis.error


def test_unexpected_crash_is_recorded(queued_analysis, session_factory, fake_github, settings):
    provider = ScriptedProvider(ZeroDivisionError("bug"))
    analysis = _run(queued_analysis, session_factory, fake_github, settings, provider)
    assert analysis.status == "failed"
    assert analysis.error == "Unexpected error while analyzing the run."


def test_only_queued_analyses_are_processed(
    queued_analysis, session_factory, fake_github, settings
):
    with session_factory() as db:
        db.get(Analysis, queued_analysis).status = "failed"  # e.g. expired by the API
        db.commit()
    provider = ScriptedProvider(GOOD_REPLY)

    analysis = _run(queued_analysis, session_factory, fake_github, settings, provider)

    assert provider.calls == 0
    assert analysis.status == "failed"
