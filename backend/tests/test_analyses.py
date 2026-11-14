"""Analysis endpoints + worker, end to end (fake GitHub, fake queue, real worker code)."""

from datetime import UTC, datetime, timedelta

from conftest import make_log_zip, make_run
from sqlalchemy import update

from app.ai.providers import HeuristicProvider
from app.models import Analysis
from app.worker import run_analysis

FAILING_LOG = make_log_zip(
    {
        "0_build.txt": (
            "[INFO] Compiling 12 source files\n"
            "[ERROR] /src/App.java:[3,1] package example does not exist\n"
            "[ERROR] BUILD FAILURE\n"
        )
    }
)


def _list_runs(client, repo):
    assert client.get(f"/api/v1/repositories/{repo['id']}/runs").status_code == 200


def _failed_run(client, fake_github, repo, run_id=123456, logs=FAILING_LOG, **kwargs):
    fake_github.add_run("example-user/example-project", make_run(run_id, **kwargs), logs=logs)
    _list_runs(client, repo)
    return run_id


# --- POST /runs/{run_id}/analyze ----------------------------------------------------------


def test_analyze_returns_202_and_queues_a_job(client, fake_github, fake_queue, registered_repo):
    run_id = _failed_run(client, fake_github, registered_repo)

    response = client.post(f"/api/v1/runs/{run_id}/analyze")

    assert response.status_code == 202
    body = response.json()
    assert set(body) == {"analysis_id", "run_id", "status"}
    assert body["run_id"] == run_id and body["status"] == "queued"
    assert body["analysis_id"].startswith("analysis-")
    assert fake_queue.enqueued == [body["analysis_id"]]


def test_analysis_is_queued_until_worker_runs(client, fake_github, registered_repo):
    run_id = _failed_run(client, fake_github, registered_repo)
    analysis_id = client.post(f"/api/v1/runs/{run_id}/analyze").json()["analysis_id"]

    response = client.get(f"/api/v1/analyses/{analysis_id}")

    assert response.status_code == 200
    assert response.json() == {
        "id": analysis_id,
        "run_id": run_id,
        "status": "queued",
        "result": None,
        "error": None,
    }


def test_full_flow_worker_completes_analysis(
    client, fake_github, registered_repo, session_factory, settings
):
    run_id = _failed_run(client, fake_github, registered_repo)
    analysis_id = client.post(f"/api/v1/runs/{run_id}/analyze").json()["analysis_id"]

    run_analysis(
        analysis_id,
        session_factory=session_factory,
        github=fake_github,
        provider=HeuristicProvider(),
        settings=settings,
        sleep=lambda _: None,
    )
    body = client.get(f"/api/v1/analyses/{analysis_id}").json()

    assert body["status"] == "completed" and body["error"] is None
    result = body["result"]
    assert set(result) == {
        "summary",
        "possible_causes",
        "evidence",
        "suggested_fixes",
        "confidence",
    }
    assert result["confidence"] == "low"  # heuristic never claims more
    assert "no AI model" in result["summary"]
    assert "[ERROR] /src/App.java:[3,1] package example does not exist" in result["evidence"]
    assert any("Java dependency" in cause for cause in result["possible_causes"])


def test_second_request_reuses_active_analysis(client, fake_github, fake_queue, registered_repo):
    run_id = _failed_run(client, fake_github, registered_repo)
    first = client.post(f"/api/v1/runs/{run_id}/analyze").json()
    second = client.post(f"/api/v1/runs/{run_id}/analyze")

    assert second.status_code == 202
    assert second.json()["analysis_id"] == first["analysis_id"]
    assert len(fake_queue.enqueued) == 1


def test_successful_run_cannot_be_analyzed(client, fake_github, fake_queue, registered_repo):
    run_id = _failed_run(client, fake_github, registered_repo, conclusion="success")

    response = client.post(f"/api/v1/runs/{run_id}/analyze")

    assert response.status_code == 409
    assert "Only failed runs" in response.json()["detail"]
    assert fake_queue.enqueued == []


def test_unknown_run_is_404(client):
    response = client.post("/api/v1/runs/999/analyze")
    assert response.status_code == 404


def test_in_progress_run_is_409(client, fake_github, registered_repo):
    run_id = _failed_run(
        client, fake_github, registered_repo, status="in_progress", conclusion=None
    )
    assert client.post(f"/api/v1/runs/{run_id}/analyze").status_code == 409


def test_redis_down_is_503_and_analysis_marked_failed(
    client, fake_github, fake_queue, registered_repo, session_factory
):
    run_id = _failed_run(client, fake_github, registered_repo)
    fake_queue.fail = True

    response = client.post(f"/api/v1/runs/{run_id}/analyze")

    assert response.status_code == 503
    assert "Redis" in response.json()["detail"] or "queue" in response.json()["detail"]
    with session_factory() as db:
        [analysis] = db.query(Analysis).all()
        assert analysis.status == "failed"


# --- GET /analyses/{analysis_id} -------------------------------------------------------------


def test_unknown_analysis_is_404(client):
    response = client.get("/api/v1/analyses/analysis-nope")
    assert response.status_code == 404
    assert isinstance(response.json()["detail"], str)


def test_queued_too_long_becomes_failed(
    client, fake_github, registered_repo, session_factory, settings
):
    run_id = _failed_run(client, fake_github, registered_repo)
    analysis_id = client.post(f"/api/v1/runs/{run_id}/analyze").json()["analysis_id"]
    _age(session_factory, analysis_id, seconds=settings.analysis_queue_timeout_seconds + 5)

    body = client.get(f"/api/v1/analyses/{analysis_id}").json()

    assert body["status"] == "failed"
    assert body["result"] is None
    assert "worker" in body["error"]


def test_new_request_after_stale_analysis_creates_fresh_one(
    client, fake_github, fake_queue, registered_repo, session_factory, settings
):
    run_id = _failed_run(client, fake_github, registered_repo)
    first = client.post(f"/api/v1/runs/{run_id}/analyze").json()["analysis_id"]
    _age(session_factory, first, seconds=settings.analysis_queue_timeout_seconds + 5)

    second = client.post(f"/api/v1/runs/{run_id}/analyze").json()["analysis_id"]

    assert second != first
    assert fake_queue.enqueued == [first, second]


def _age(session_factory, analysis_id: str, *, seconds: int) -> None:
    with session_factory() as db:
        db.execute(
            update(Analysis)
            .where(Analysis.id == analysis_id)
            .values(updated_at=datetime.now(UTC) - timedelta(seconds=seconds))
        )
        db.commit()
