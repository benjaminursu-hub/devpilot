"""Redis-backed job queue (RQ) for AI analyses.

The API only enqueues a job id; the worker (app/worker.py) does the work. The job is
referenced by its import path, so the API process never imports worker code.
"""

from typing import Protocol

from app.config import Settings
from app.errors import QueueUnavailableError

QUEUE_NAME = "devpilot-analysis"
JOB_FUNCTION = "app.worker.process_analysis"


class AnalysisQueue(Protocol):
    def enqueue(self, analysis_id: str) -> None: ...


class RedisAnalysisQueue:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def enqueue(self, analysis_id: str) -> None:
        # Imported lazily so the rest of the API (and its tests) work without Redis installed.
        from redis import Redis
        from redis.exceptions import RedisError
        from rq import Queue

        try:
            connection = Redis.from_url(
                self._settings.redis_url, socket_connect_timeout=3, socket_timeout=5
            )
            Queue(QUEUE_NAME, connection=connection).enqueue(
                JOB_FUNCTION,
                analysis_id,
                job_id=analysis_id,
                job_timeout=self._settings.analysis_job_timeout_seconds,
                # Drop the job if no worker starts it in time; the API reports it as failed.
                ttl=self._settings.analysis_queue_timeout_seconds,
                result_ttl=3600,
                failure_ttl=24 * 3600,
            )
        except RedisError as exc:
            raise QueueUnavailableError() from exc
