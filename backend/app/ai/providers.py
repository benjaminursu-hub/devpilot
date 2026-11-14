"""AI providers. Each returns the model's raw reply text; app/ai/output.py validates it.

Providers talk HTTP directly (httpx) instead of using vendor SDKs, to keep dependencies small
and error handling uniform. Every failure becomes an AIProviderError marked transient (worth
retrying: timeouts, rate limits, overload, 5xx) or permanent (bad key, bad model, bad request).
Error messages never include the API key or the request body.
"""

import json
import re
from typing import Protocol

import httpx

from app.ai.prompt import SYSTEM_PROMPT, RunContext, build_user_message
from app.config import Settings

TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


class AIProviderError(Exception):
    def __init__(self, message: str, *, transient: bool, retry_after: float | None = None):
        super().__init__(message)
        self.transient = transient
        self.retry_after = retry_after


class AIProvider(Protocol):
    name: str

    def generate(self, run: RunContext, logs: str) -> str: ...


# --- LLM providers -----------------------------------------------------------------------------


class _HttpProvider:
    name = "http"

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        if not settings.ai_api_key_value:
            raise AIProviderError(
                f"AI_PROVIDER={self.name} needs AI_API_KEY to be set.", transient=False
            )
        if not settings.ai_model:
            raise AIProviderError(
                f"AI_PROVIDER={self.name} needs AI_MODEL to be set.", transient=False
            )
        self._key = settings.ai_api_key_value
        self._model = settings.ai_model
        self._max_tokens = settings.ai_max_output_tokens
        self._timeout = settings.ai_timeout_seconds
        self._transport = transport

    def _post(self, url: str, headers: dict[str, str], body: dict) -> dict:
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                response = client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise AIProviderError("The AI provider timed out.", transient=True) from exc
        except httpx.HTTPError as exc:
            raise AIProviderError(
                f"Could not reach the AI provider ({type(exc).__name__}).", transient=True
            ) from exc

        if response.status_code >= 400:
            raise AIProviderError(
                f"The AI provider returned HTTP {response.status_code}{_error_hint(response)}.",
                transient=response.status_code in TRANSIENT_STATUS,
                retry_after=_retry_after(response),
            )
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise AIProviderError(
                "The AI provider returned a non-JSON response.", transient=True
            ) from exc


class AnthropicProvider(_HttpProvider):
    name = "anthropic"
    default_base_url = "https://api.anthropic.com"

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        super().__init__(settings, transport)
        self._url = (settings.ai_base_url or self.default_base_url).rstrip("/") + "/v1/messages"

    def generate(self, run: RunContext, logs: str) -> str:
        data = self._post(
            self._url,
            headers={"x-api-key": self._key, "anthropic-version": "2023-06-01"},
            body={
                "model": self._model,
                "max_tokens": self._max_tokens,
                "temperature": 0,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": build_user_message(run, logs)}],
            },
        )
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if not text:
            raise AIProviderError("The AI provider returned an empty reply.", transient=True)
        return text


class OpenAIProvider(_HttpProvider):
    """OpenAI Chat Completions, or any compatible server via AI_BASE_URL."""

    name = "openai"
    default_base_url = "https://api.openai.com/v1"

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        super().__init__(settings, transport)
        base = (settings.ai_base_url or self.default_base_url).rstrip("/")
        self._url = base + "/chat/completions"
        # api.openai.com wants max_completion_tokens (newer models reject max_tokens and a
        # custom temperature); OpenAI-compatible servers generally only know max_tokens.
        self._official = not settings.ai_base_url

    def generate(self, run: RunContext, logs: str) -> str:
        body: dict = {
            "model": self._model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_message(run, logs)},
            ],
        }
        if self._official:
            body["max_completion_tokens"] = self._max_tokens
        else:
            body["max_tokens"] = self._max_tokens
            body["temperature"] = 0
        data = self._post(self._url, headers={"Authorization": f"Bearer {self._key}"}, body=body)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError(
                "The AI provider returned an unexpected response.", transient=True
            ) from exc
        if not text:
            raise AIProviderError("The AI provider returned an empty reply.", transient=True)
        return text


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers["retry-after"])
    except (KeyError, ValueError):
        return None


def _error_hint(response: httpx.Response) -> str:
    """A short, key-free hint from the provider's error body (e.g. 'model not found')."""
    try:
        body = response.json()
    except json.JSONDecodeError:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    if not isinstance(message, str):
        return ""
    message = re.sub(r"\s+", " ", message).strip()[:200]
    return f": {message}" if message else ""


# --- Offline heuristic -------------------------------------------------------------------------

# (pattern, likely cause, suggested fix). First matches win; checked against the whole log.
_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"package \S+ does not exist|cannot find symbol|ClassNotFoundException"),
        "A Java dependency or import is missing or not on the compile classpath.",
        "Check the dependency declarations in pom.xml / build.gradle and the import statements.",
    ),
    (
        re.compile(r"ModuleNotFoundError|ImportError: No module named"),
        "A Python module is not installed in the CI environment.",
        "Add the missing package to requirements/pyproject and make sure CI installs it.",
    ),
    (
        re.compile(r"Cannot find module|Module not found: Error|ERR_MODULE_NOT_FOUND"),
        "A JavaScript/TypeScript module cannot be resolved.",
        "Check package.json, the lockfile, and import paths (case-sensitive on Linux).",
    ),
    (
        re.compile(r"npm ERR!|npm error|ERESOLVE"),
        "npm failed while installing or running a script.",
        "Reproduce with `npm ci` locally and fix the reported dependency or script error.",
    ),
    (
        re.compile(r"\bFAILED\b.*test|Tests? failed|AssertionError|\d+ failed", re.IGNORECASE),
        "One or more automated tests failed.",
        "Run the failing tests locally and compare the expected and actual values in the log.",
    ),
    (
        re.compile(r"error TS\d+|SyntaxError|error\[E\d+\]|: error:"),
        "The code does not compile.",
        "Fix the compiler error at the file and line shown in the evidence.",
    ),
    (
        re.compile(r"exit code 137|Killed|OutOfMemoryError|JavaScript heap out of memory"),
        "The job ran out of memory.",
        "Reduce memory use or raise limits (e.g. NODE_OPTIONS / -Xmx), or use a larger runner.",
    ),
    (
        re.compile(
            r"Permission denied|EACCES|403 Forbidden|Resource not accessible by integration"
        ),
        "A step lacked permission for a file, registry or GitHub API.",
        "Check file permissions, registry credentials and the workflow's `permissions:` block.",
    ),
    (
        re.compile(r"timed out|timeout|The operation was canceled", re.IGNORECASE),
        "A step timed out or was cancelled.",
        "Look for a hanging step; raise `timeout-minutes` only after finding why it is slow.",
    ),
]

_ERROR_LINE = re.compile(
    r"(##\[error\]|\berror\b|\bfailed\b|\bfailure\b|\bfatal\b|\bexception\b|traceback)",
    re.IGNORECASE,
)
_NOISE = re.compile(r"\b0 errors?\b|\berror(s)?: 0\b|continue-on-error|##\[group\]", re.IGNORECASE)


class HeuristicProvider:
    """No AI: picks error lines and matches known failure patterns.

    Exists so the whole pipeline works locally without an API key. Its output says plainly
    that no AI model was used, and its confidence is never above "low".
    """

    name = "heuristic"

    def generate(self, run: RunContext, logs: str) -> str:
        lines = [line.strip() for line in logs.splitlines() if line.strip()]
        error_lines = [
            line[:300]
            for line in lines
            if _ERROR_LINE.search(line) and not _NOISE.search(line) and not line.startswith("=====")
        ]
        evidence = list(dict.fromkeys(error_lines))[-5:]

        causes, fixes = [], []
        for pattern, cause, fix in _RULES:
            if pattern.search(logs) and cause not in causes:
                causes.append(cause)
                fixes.append(fix)
            if len(causes) == 3:
                break
        if not causes:
            causes = ["The log does not match a known failure pattern."]
            fixes = ["Read the evidence lines and the step that failed in the GitHub log view."]

        last = evidence[-1] if evidence else "no explicit error line was found"
        summary = (
            "Offline heuristic analysis (no AI model configured). "
            f"The '{run.workflow}' run ended with '{run.conclusion or 'unknown'}'; "
            f"the last error line in the log is: {last}"
        )
        return json.dumps(
            {
                "summary": summary,
                "possible_causes": causes,
                "evidence": evidence,
                "suggested_fixes": fixes,
                "confidence": "low",
            }
        )


def build_ai_provider(settings: Settings) -> AIProvider:
    if settings.ai_provider == "anthropic":
        return AnthropicProvider(settings)
    if settings.ai_provider == "openai":
        return OpenAIProvider(settings)
    return HeuristicProvider()
