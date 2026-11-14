"""Application errors. Each one maps to an HTTP status and a contract-shaped body:

    {"detail": "Description of the problem"}

This module has no web-framework imports so services can raise these freely.
"""


class ApiError(Exception):
    status_code: int = 500
    default_detail: str = "Internal server error."

    def __init__(self, detail: str | None = None, *, status_code: int | None = None) -> None:
        self.detail = detail or self.default_detail
        if status_code is not None:
            self.status_code = status_code
        super().__init__(self.detail)


class NotFoundError(ApiError):
    status_code = 404
    default_detail = "Resource not found."


class ConflictError(ApiError):
    status_code = 409
    default_detail = "Conflict."


class ServiceNotConfiguredError(ApiError):
    status_code = 503
    default_detail = "A required server setting is missing."


class QueueUnavailableError(ApiError):
    status_code = 503
    default_detail = "The analysis queue (Redis) is unavailable. Try again later."


# --- GitHub ---------------------------------------------------------------------------------
# GitHub failures are surfaced as upstream errors (5xx) unless they are really about the
# resource the caller asked for (404 not found, 410 logs expired).


class GitHubError(ApiError):
    status_code = 502
    default_detail = "GitHub returned an unexpected response."


class GitHubNotFoundError(GitHubError):
    status_code = 404
    default_detail = "Not found on GitHub, or the configured token cannot access it."


class GitHubAuthError(GitHubError):
    status_code = 502
    default_detail = "GitHub rejected the server's token. Check GITHUB_TOKEN."


class GitHubForbiddenError(GitHubError):
    status_code = 502
    default_detail = (
        "GitHub denied access. The token may lack the 'Actions: read' permission "
        "for this repository."
    )


class GitHubRateLimitError(GitHubError):
    status_code = 503
    default_detail = "GitHub API rate limit reached. Try again later."


class GitHubLogsGoneError(GitHubError):
    status_code = 410
    default_detail = "The logs for this run have expired or been deleted on GitHub."


class GitHubTimeoutError(GitHubError):
    status_code = 504
    default_detail = "GitHub did not respond in time."


class LogArchiveError(GitHubError):
    status_code = 502
    default_detail = "The log archive from GitHub could not be processed."
