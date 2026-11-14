"""End-to-end tests of the milestone-1 flow through the HTTP API."""

from conftest import make_log_zip, make_run

from app.api.deps import get_github_client
from app.config import Settings, get_settings


# --- health ----------------------------------------------------------------------------------


def test_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- repositories ----------------------------------------------------------------------------


def test_register_repository_uses_githubs_canonical_name(client, fake_github):
    fake_github.add_repository("Example-User", "Example-Project")

    response = client.post(
        "/api/v1/repositories", json={"owner": "example-user", "name": "example-project"}
    )

    assert response.status_code == 201
    assert response.json() == {
        "id": 1,
        "owner": "Example-User",
        "name": "Example-Project",
        "full_name": "Example-User/Example-Project",
    }


def test_register_repository_twice_conflicts_case_insensitively(client, registered_repo):
    response = client.post(
        "/api/v1/repositories", json={"owner": "EXAMPLE-USER", "name": "Example-Project"}
    )
    assert response.status_code == 409
    assert "already registered" in response.json()["detail"]


def test_register_unknown_repository_is_404(client):
    response = client.post("/api/v1/repositories", json={"owner": "nobody", "name": "nothing"})
    assert response.status_code == 404
    assert isinstance(response.json()["detail"], str)


def test_register_rejects_invalid_names_with_string_detail(client, fake_github):
    for payload in (
        {"owner": "bad/owner", "name": "x"},
        {"owner": "ok", "name": ".."},
        {"owner": "ok"},
    ):
        response = client.post("/api/v1/repositories", json=payload)
        assert response.status_code == 422, payload
        assert isinstance(response.json()["detail"], str)
    assert fake_github.calls == []  # never reached GitHub


def test_list_repositories(client, registered_repo):
    response = client.get("/api/v1/repositories")
    assert response.status_code == 200
    assert response.json() == [registered_repo]


def test_missing_github_token_is_503(client):
    # Use the real GitHub dependency (not the fake) with settings that have no token.
    del client.app.dependency_overrides[get_github_client]
    client.app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, github_token=None
    )

    response = client.post("/api/v1/repositories", json={"owner": "a", "name": "b"})
    assert response.status_code == 503
    assert response.json() == {"detail": "GITHUB_TOKEN is not configured on the server."}


# --- runs ------------------------------------------------------------------------------------


def test_list_runs_returns_contract_shape(client, fake_github, registered_repo):
    fake_github.add_run("example-user/example-project", make_run(123456))

    response = client.get(f"/api/v1/repositories/{registered_repo['id']}/runs")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": 123456,
            "repository_id": registered_repo["id"],
            "name": "Build and Test",
            "status": "completed",
            "conclusion": "failure",
            "branch": "main",
            "html_url": "https://github.com/example-user/example-project/actions/runs/123456",
            "created_at": "2026-11-07T12:00:00Z",
        }
    ]


def test_list_runs_filters_and_limits(client, fake_github, registered_repo):
    for run_id, conclusion in [(1, "success"), (2, "failure"), (3, "failure")]:
        fake_github.add_run("example-user/example-project", make_run(run_id, conclusion=conclusion))

    response = client.get(
        f"/api/v1/repositories/{registered_repo['id']}/runs",
        params={"conclusion": "failure", "limit": 1},
    )

    assert response.status_code == 200
    assert [r["id"] for r in response.json()] == [3]
    assert fake_github.calls[-1] == (
        "list_workflow_runs",
        "example-user",
        "example-project",
        1,
        "failure",
    )


def test_list_runs_twice_updates_instead_of_duplicating(client, fake_github, registered_repo):
    fake_github.add_run(
        "example-user/example-project", make_run(7, status="in_progress", conclusion=None)
    )
    url = f"/api/v1/repositories/{registered_repo['id']}/runs"
    assert client.get(url).json()[0]["status"] == "in_progress"

    fake_github.complete_run(7, "failure")
    runs = client.get(url).json()
    assert len(runs) == 1
    assert runs[0]["status"] == "completed" and runs[0]["conclusion"] == "failure"


def test_list_runs_unknown_repository(client):
    response = client.get("/api/v1/repositories/999/runs")
    assert response.status_code == 404
    assert response.json() == {"detail": "Repository 999 is not registered."}


def test_list_runs_rejects_unknown_conclusion(client, registered_repo):
    response = client.get(
        f"/api/v1/repositories/{registered_repo['id']}/runs", params={"conclusion": "nope"}
    )
    assert response.status_code == 422


# --- logs ------------------------------------------------------------------------------------


def _list_runs(client, repo):
    return client.get(f"/api/v1/repositories/{repo['id']}/runs").json()


def test_get_logs_for_failed_run(client, fake_github, registered_repo):
    archive = make_log_zip(
        {
            "0_build.txt": (
                "2026-11-07T12:00:01.1234567Z Compiling...\n"
                "2026-11-07T12:00:02.0000000Z \x1b[31mCompilation failed: "
                "package example does not exist\x1b[0m\n"
            ),
            "build/1_Set up job.txt": "step file, duplicated in 0_build.txt\n",
        }
    )
    fake_github.add_run("example-user/example-project", make_run(123456), logs=archive)
    _list_runs(client, registered_repo)

    response = client.get("/api/v1/runs/123456/logs")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == 123456
    assert body["truncated"] is False
    assert body["logs"] == (
        "===== build =====\nCompiling...\nCompilation failed: package example does not exist\n"
    )


def test_logs_are_redacted(client, fake_github, registered_repo):
    secret = "ghp_" + "a" * 36
    archive = make_log_zip({"0_deploy.txt": f"pushing with {secret}\nPASSWORD=hunter2hunter2\n"})
    fake_github.add_run("example-user/example-project", make_run(5), logs=archive)
    _list_runs(client, registered_repo)

    logs = client.get("/api/v1/runs/5/logs").json()["logs"]

    assert secret not in logs
    assert "hunter2hunter2" not in logs
    assert "[REDACTED]" in logs


def test_long_logs_keep_the_tail(client, fake_github, registered_repo, settings):
    lines = [f"line {i}" for i in range(5_000)] + ["FATAL: the actual error"]
    archive = make_log_zip({"0_test.txt": "\n".join(lines)})
    fake_github.add_run("example-user/example-project", make_run(9), logs=archive)
    _list_runs(client, registered_repo)

    body = client.get("/api/v1/runs/9/logs").json()

    assert body["truncated"] is True
    assert body["logs"].startswith("[... ")
    assert body["logs"].rstrip().endswith("FATAL: the actual error")
    assert len(body["logs"]) <= settings.log_max_chars + 100


def test_logs_for_unknown_run(client):
    response = client.get("/api/v1/runs/42/logs")
    assert response.status_code == 404
    assert "List its repository's runs first" in response.json()["detail"]


def test_logs_refresh_stale_in_progress_status(client, fake_github, registered_repo):
    archive = make_log_zip({"0_build.txt": "boom\n"})
    fake_github.add_run(
        "example-user/example-project",
        make_run(11, status="in_progress", conclusion=None),
        logs=archive,
    )
    _list_runs(client, registered_repo)  # stored as in_progress

    assert client.get("/api/v1/runs/11/logs").status_code == 409

    fake_github.complete_run(11, "failure")  # finishes on GitHub; DevPilot's copy is stale
    response = client.get("/api/v1/runs/11/logs")
    assert response.status_code == 200
    assert "boom" in response.json()["logs"]


def test_expired_logs_are_410(client, fake_github, registered_repo):
    fake_github.add_run("example-user/example-project", make_run(12))
    fake_github.expired_logs.add(12)
    _list_runs(client, registered_repo)

    response = client.get("/api/v1/runs/12/logs")
    assert response.status_code == 410
    assert "expired" in response.json()["detail"]


def test_corrupt_archive_is_502(client, fake_github, registered_repo):
    fake_github.add_run("example-user/example-project", make_run(13), logs=b"not a zip")
    _list_runs(client, registered_repo)

    response = client.get("/api/v1/runs/13/logs")
    assert response.status_code == 502


# --- unexpected errors -------------------------------------------------------------------------


def test_unhandled_error_keeps_cors_headers_and_contract_shape(client):
    """Regression: a crash used to return a 500 without CORS headers, which the browser hides,
    so the frontend reported "Could not reach the DevPilot API" instead of the real error."""

    def boom():
        raise RuntimeError("simulated crash")

    client.app.add_api_route("/api/v1/boom", boom)
    response = client.get("/api/v1/boom", headers={"Origin": "http://localhost:5173"})

    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.json() == {
        "detail": "Internal server error. Details were logged on the server."
    }


def test_run_already_stored_by_another_request_is_updated_not_duplicated(
    client, fake_github, registered_repo, session_factory
):
    """Regression for the concurrent-listing race: the row may already exist when we write."""
    from app.models import WorkflowRun

    fake_github.add_run("example-user/example-project", make_run(55))
    with session_factory() as other_request:
        other_request.add(
            WorkflowRun(
                id=55,
                repository_id=registered_repo["id"],
                name="stale name",
                status="in_progress",
                conclusion=None,
                branch="main",
                html_url="https://example.invalid",
                created_at=make_run(55).created_at,
            )
        )
        other_request.commit()

    response = client.get(f"/api/v1/repositories/{registered_repo['id']}/runs")

    assert response.status_code == 200
    [run] = response.json()
    assert (run["name"], run["status"], run["conclusion"]) == (
        "Build and Test",
        "completed",
        "failure",
    )
