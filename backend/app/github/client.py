"""Thin, synchronous client for the parts of the GitHub REST API DevPilot uses.

Only this module talks to GitHub. Everything it returns is plain data (dataclasses / bytes),
so the rest of the app never sees raw HTTP responses.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import httpx

from app.errors import (
    ServiceNotConfiguredError,
    GitHubAuthError,
    GitHubError,
    GitHubForbiddenError,
    GitHubLogsGoneError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubTimeoutError,
    LogArchiveError,
)

if TYPE_CHECKING:
    from app.config import Settings

USER_AGENT = "devpilot-backend"
# Compressed size limit for a run's log archive. Real archives are usually a few MB.
MAX_LOG_ARCHIVE_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class GitHubRepository:
    owner: str
    name: str
    full_name: str


@dataclass(frozen=True)
class GitHubWorkflowRun:
    id: int
    name: str
    status: str
    conclusion: str | None
    branch: str | None
    html_url: str
    created_at: datetime


class GitHubClient:
    @classmethod
    def from_settings(cls, settings: "Settings") -> "GitHubClient":
        token = settings.github_token_value
        if token is None:
            raise ServiceNotConfiguredError("GITHUB_TOKEN is not configured on the server.")
        return cls(
            token=token,
            base_url=settings.github_api_url,
            timeout=settings.github_timeout_seconds,
        )

    def __init__(
        self,
        *,
        token: str | None,
        base_url: str = "https://api.github.com",
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        # API client: carries the token, never follows redirects by itself.
        self._api = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        )
        # Download client: used for the pre-signed URL GitHub redirects log downloads to.
        # It deliberately has NO Authorization header, so the token never leaves api.github.com.
        self._download = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._api.close()
        self._download.close()

    # --- public API -------------------------------------------------------------------------

    def get_repository(self, owner: str, name: str) -> GitHubRepository:
        response = self._request(
            "GET",
            f"/repos/{owner}/{name}",
            not_found=f"Repository {owner}/{name} was not found on GitHub, "
            "or the configured token cannot access it.",
        )
        data = response.json()
        return GitHubRepository(
            owner=data["owner"]["login"], name=data["name"], full_name=data["full_name"]
        )

    def list_workflow_runs(
        self, owner: str, name: str, *, per_page: int = 20, conclusion: str | None = None
    ) -> list[GitHubWorkflowRun]:
        """Most recent runs first. `conclusion` (e.g. "failure") filters on GitHub's side."""
        params: dict[str, str | int] = {"per_page": per_page}
        if conclusion:
            # GitHub's `status` query parameter also accepts conclusions such as "failure".
            params["status"] = conclusion
        response = self._request(
            "GET",
            f"/repos/{owner}/{name}/actions/runs",
            params=params,
            not_found=f"Repository {owner}/{name} was not found on GitHub, "
            "or the configured token cannot access it.",
        )
        return [_parse_run(run) for run in response.json().get("workflow_runs", [])]

    def get_workflow_run(self, owner: str, name: str, run_id: int) -> GitHubWorkflowRun:
        response = self._request(
            "GET",
            f"/repos/{owner}/{name}/actions/runs/{run_id}",
            not_found=f"Run {run_id} was not found on GitHub.",
        )
        return _parse_run(response.json())

    def download_run_logs(self, owner: str, name: str, run_id: int) -> bytes:
        """Return the run's log archive (a ZIP file) as bytes."""
        response = self._request(
            "GET",
            f"/repos/{owner}/{name}/actions/runs/{run_id}/logs",
            not_found=f"Logs for run {run_id} were not found on GitHub.",
        )
        if not response.is_redirect:
            return response.content

        location = response.headers.get("location")
        if not location:
            raise GitHubError("GitHub redirected the log download without a location.")
        return self._download_archive(location)

    # --- internals --------------------------------------------------------------------------

    def _download_archive(self, url: str) -> bytes:
        try:
            with self._download.stream("GET", url) as response:
                if response.status_code != 200:
                    raise GitHubError(
                        f"Downloading the log archive failed with HTTP {response.status_code}."
                    )
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_LOG_ARCHIVE_BYTES:
                        raise LogArchiveError("The log archive is too large to process.")
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.TimeoutException as exc:
            raise GitHubTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise GitHubError(f"Could not download the log archive: {type(exc).__name__}.") from exc

    def _request(
        self,
        method: str,
        url: str,
        *,
        not_found: str,
        params: dict[str, str | int] | None = None,
    ) -> httpx.Response:
        try:
            response = self._api.request(method, url, params=params)
        except httpx.TimeoutException as exc:
            raise GitHubTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise GitHubError(f"Could not reach GitHub: {type(exc).__name__}.") from exc

        status = response.status_code
        if status < 300 or response.is_redirect:
            return response
        if status == 401:
            raise GitHubAuthError()
        rate_limited = response.headers.get("x-ratelimit-remaining") == "0"
        if status == 429 or (status == 403 and rate_limited):
            raise GitHubRateLimitError()
        if status == 403:
            raise GitHubForbiddenError()
        if status == 404:
            raise GitHubNotFoundError(not_found)
        if status == 410:
            raise GitHubLogsGoneError()
        raise GitHubError(f"GitHub returned HTTP {status}.")


def _parse_run(run: dict) -> GitHubWorkflowRun:
    return GitHubWorkflowRun(
        id=int(run["id"]),
        name=run.get("name") or run.get("display_title") or "Unnamed workflow",
        status=run.get("status") or "unknown",
        conclusion=run.get("conclusion"),
        branch=run.get("head_branch"),
        html_url=run["html_url"],
        created_at=datetime.fromisoformat(run["created_at"]),
    )
