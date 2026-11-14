"""Application settings, read from environment variables (or a .env file).

Credentials live here and nowhere else. They are never returned by the API.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

AIProviderName = Literal["heuristic", "anthropic", "openai"]


class Settings(BaseSettings):
    # Looks for .env in the current directory, then in the repo root when run from backend/.
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql+psycopg://devpilot:devpilot@localhost:5433/devpilot"

    # --- GitHub ---------------------------------------------------------------------------
    github_token: SecretStr | None = None
    github_api_url: str = "https://api.github.com"
    github_timeout_seconds: float = Field(default=20.0, gt=0)

    # --- API ------------------------------------------------------------------------------
    # Maximum characters returned by GET /runs/{id}/logs (the tail is kept).
    log_max_chars: int = Field(default=200_000, ge=1_000)
    # Comma-separated list of origins allowed to call the API from a browser.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000"

    # --- Background analysis ----------------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    # How long a queued analysis may wait for a worker before it is marked failed.
    analysis_queue_timeout_seconds: int = Field(default=600, ge=30)
    # Hard limit for one analysis job (log download + all AI attempts).
    analysis_job_timeout_seconds: int = Field(default=600, ge=30)

    # --- AI provider ------------------------------------------------------------------------
    # heuristic: offline pattern matching, no AI model and no key (clearly labelled as such).
    # anthropic: Anthropic Messages API.  openai: OpenAI or any OpenAI-compatible endpoint.
    ai_provider: AIProviderName = "heuristic"
    ai_api_key: SecretStr | None = None
    ai_model: str | None = None
    # Override the provider's API base URL (e.g. a local OpenAI-compatible server).
    ai_base_url: str | None = None
    ai_timeout_seconds: float = Field(default=60.0, gt=0)
    ai_max_attempts: int = Field(default=3, ge=1, le=6)
    ai_max_output_tokens: int = Field(default=2_000, ge=256)
    # Log characters sent to the model (the end of the log is kept).
    ai_log_max_chars: int = Field(default=60_000, ge=2_000)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def github_token_value(self) -> str | None:
        return _secret(self.github_token)

    @property
    def ai_api_key_value(self) -> str | None:
        return _secret(self.ai_api_key)


def _secret(value: SecretStr | None) -> str | None:
    if value is None:
        return None
    text = value.get_secret_value().strip()
    return text or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
