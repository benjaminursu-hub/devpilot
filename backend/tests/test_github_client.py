"""Tests for the real GitHubClient, using httpx's MockTransport (no network)."""

import httpx
import pytest

from app.errors import (
    GitHubAuthError,
    GitHubForbiddenError,
    GitHubLogsGoneError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubTimeoutError,
)
from app.github.client import GitHubClient

TOKEN = "ghp_" + "x" * 36


def make_client(handler) -> GitHubClient:
    return GitHubClient(token=TOKEN, transport=httpx.MockTransport(handler))


def test_get_repository_sends_auth_and_parses():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200, json={"name": "Proj", "full_name": "Owner/Proj", "owner": {"login": "Owner"}}
        )

    repo = make_client(handler).get_repository("owner", "proj")

    assert seen["url"] == "https://api.github.com/repos/owner/proj"
    assert seen["auth"] == f"Bearer {TOKEN}"
    assert (repo.owner, repo.name, repo.full_name) == ("Owner", "Proj", "Owner/Proj")


def test_list_workflow_runs_parses_and_passes_filter():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["status"] == "failure"
        assert request.url.params["per_page"] == "5"
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "workflow_runs": [
                    {
                        "id": 123456,
                        "name": "Build and Test",
                        "status": "completed",
                        "conclusion": "failure",
                        "head_branch": "main",
                        "html_url": "https://github.com/o/r/actions/runs/123456",
                        "created_at": "2026-11-07T12:00:00Z",
                    }
                ],
            },
        )

    runs = make_client(handler).list_workflow_runs("o", "r", per_page=5, conclusion="failure")

    assert len(runs) == 1
    assert runs[0].id == 123456 and runs[0].branch == "main"
    assert runs[0].created_at.isoformat() == "2026-11-07T12:00:00+00:00"


def test_log_download_follows_redirect_without_leaking_token():
    archive = b"PK\x05\x06" + b"\x00" * 18  # empty zip
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers.get("authorization")))
        if request.url.host == "api.github.com":
            signed_url = "https://blob.example.net/logs.zip?sig=1"
            return httpx.Response(302, headers={"location": signed_url})
        return httpx.Response(200, content=archive)

    data = make_client(handler).download_run_logs("o", "r", 1)

    assert data == archive
    assert seen == [("api.github.com", f"Bearer {TOKEN}"), ("blob.example.net", None)]


@pytest.mark.parametrize(
    ("status", "headers", "error"),
    [
        (401, {}, GitHubAuthError),
        (403, {"x-ratelimit-remaining": "0"}, GitHubRateLimitError),
        (429, {}, GitHubRateLimitError),
        (403, {}, GitHubForbiddenError),
        (404, {}, GitHubNotFoundError),
        (410, {}, GitHubLogsGoneError),
    ],
)
def test_error_mapping(status, headers, error):
    client = make_client(lambda request: httpx.Response(status, headers=headers))
    with pytest.raises(error):
        client.download_run_logs("o", "r", 1)


def test_timeout_maps_to_504():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(GitHubTimeoutError) as info:
        make_client(handler).get_repository("o", "r")
    assert info.value.status_code == 504
