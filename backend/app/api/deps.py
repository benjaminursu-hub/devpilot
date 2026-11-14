from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.github.client import GitHubClient
from app.task_queue import AnalysisQueue, RedisAnalysisQueue


def get_github_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Iterator[GitHubClient]:
    client = GitHubClient.from_settings(settings)  # 503 if GITHUB_TOKEN is missing
    try:
        yield client
    finally:
        client.close()


def get_analysis_queue(settings: Annotated[Settings, Depends(get_settings)]) -> AnalysisQueue:
    return RedisAnalysisQueue(settings)


DbSession = Annotated[Session, Depends(get_db)]
GitHub = Annotated[GitHubClient, Depends(get_github_client)]
AppSettings = Annotated[Settings, Depends(get_settings)]
Queue = Annotated[AnalysisQueue, Depends(get_analysis_queue)]
