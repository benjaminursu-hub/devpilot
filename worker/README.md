# Analysis worker

The worker's code lives in the backend package — `backend/app/worker.py` — because it shares
the backend's models, GitHub client, log pipeline and settings. Docker Compose runs it from the
backend image as a separate `worker` service (`docker-entrypoint.sh worker` →
`python -m app.worker`).

What one job does:

1. Atomically claims the analysis (`queued` → `running`); skips it if it is no longer queued.
2. Downloads the run's logs with the same pipeline as `GET /runs/{id}/logs`: noise stripped,
   recognizable secrets redacted, size bounded (`AI_LOG_MAX_CHARS`, end of log kept).
   GitHub timeouts / rate limits / 5xx are retried.
3. Sends the logs to the configured provider (`AI_PROVIDER`), with the log fenced and marked
   as untrusted data.
4. Validates the reply (JSON shape, confidence value, string lengths, secret redaction) and
   drops "evidence" lines that do not appear in the log. Timeouts, rate limits, overload and
   invalid replies are retried with exponential backoff (`AI_MAX_ATTEMPTS`).
5. Saves `completed` + result, or `failed` + a readable `error`.

RQ kills a job after `ANALYSIS_JOB_TIMEOUT_SECONDS`; the API then reports that analysis as
failed. The worker never executes anything suggested by a log or a model and never writes to
GitHub.

RQ needs `fork()`, so run the worker in Docker (or WSL), not natively on Windows.
