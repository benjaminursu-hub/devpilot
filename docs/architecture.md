# DevPilot backend — architecture

## Components

```text
 ┌──────────────┐  JSON /api/v1   ┌───────────────────────────┐   REST    ┌────────────┐
 │ React        │ ──────────────▶ │ FastAPI backend           │ ────────▶ │ GitHub API │
 │ frontend     │ ◀────────────── │  routes → services        │ ◀──────── │            │
 │ (Foundable)  │                 │  GitHub client (token)    │  log ZIP  └────────────┘
 └──────────────┘                 └──────┬──────────────┬─────┘
                                         │ SQLAlchemy   │ enqueue (M2)
                                  ┌──────▼─────┐  ┌─────▼─────┐   ┌──────────────┐
                                  │ PostgreSQL │◀─│  Redis    │──▶│ Worker (RQ)  │──▶ AI provider
                                  └────────────┘  └───────────┘   └──────────────┘
                                        ▲ results written by the worker ─┘
```

* **API (FastAPI)** — validates input, calls services, returns contract-shaped JSON. The only
  process the browser talks to. Holds the GitHub token; never returns it.
* **Services** (`app/services/`) — the actual behaviour (register repo, sync runs, build logs).
  Framework-free, so the worker can reuse them in milestone 2.
* **GitHub client** (`app/github/client.py`) — the only code that talks to GitHub. Maps GitHub
  failures to app errors (404, 410, 502, 503, 504).
* **Log processing** (`app/log_processing.py`, `app/redaction.py`) — unzip, clean, redact,
  bound. Treats the archive as untrusted input.
* **PostgreSQL** — `repositories`, `workflow_runs`, `analyses`. Schema owned by Alembic.
* **Redis + worker** (`app/task_queue.py`, `app/worker.py`) — analysis is slow (an AI call),
  so `POST /analyze` stores a `queued` row, enqueues its id on Redis (RQ) and returns `202`.
  The worker claims it (`running`), fetches logs, calls the provider with retries/backoff,
  validates the reply, and writes `completed` or `failed`. The frontend polls
  `GET /analyses/{id}`. Abandoned analyses (no worker, killed job) are reported as `failed`.
* **AI layer** (`app/ai/`) — `prompt.py` (system prompt, untrusted-log fencing),
  `providers.py` (Anthropic, OpenAI/compatible, offline heuristic; uniform transient vs
  permanent errors), `output.py` (JSON extraction, validation, evidence grounding).
* **Error middleware** (`app/middleware.py`) — unhandled exceptions become a logged,
  CORS-readable `500 {"detail": ...}` (see the 2026-11-07 diagnosis doc).

## Request flows (milestone 1)

1. `POST /repositories` → check DB for duplicate → `GET /repos/{owner}/{name}` on GitHub
   (proves the repo exists *and* the token can see it, and gives the canonical name) → insert.
2. `GET /repositories/{id}/runs` → `GET /repos/{o}/{r}/actions/runs` → upsert into
   `workflow_runs` → return newest first.
3. `GET /runs/{run_id}/logs` → look the run up in `workflow_runs` (to know its repository) →
   refresh status from GitHub if it was still running → download the log ZIP (GitHub redirects
   to a pre-signed URL; we follow it *without* the token) → extract job logs → strip
   timestamps/colour codes → redact secrets → keep the last `LOG_MAX_CHARS` characters.

## File structure

```text
devpilot/
├── backend/
│   ├── app/
│   │   ├── main.py              app factory, CORS, error handlers ({"detail": "..."})
│   │   ├── config.py            settings from environment variables
│   │   ├── db.py                engine/session + get_db dependency
│   │   ├── models.py            SQLAlchemy models (all three tables)
│   │   ├── schemas.py           request/response models = the API contract
│   │   ├── errors.py            ApiError hierarchy → HTTP status codes
│   │   ├── log_processing.py    ZIP → clean, bounded text
│   │   ├── redaction.py         secret redaction
│   │   ├── github/client.py     GitHub REST client
│   │   ├── services/            repositories.py, runs.py, logs.py, analyses.py
│   │   ├── ai/                  prompt.py, providers.py, output.py
│   │   ├── task_queue.py        Redis/RQ enqueue
│   │   ├── worker.py            analysis job + worker entry point
│   │   ├── middleware.py        CORS-safe 500 handling
│   │   └── api/                 routes.py, deps.py
│   ├── alembic/                 migrations (versions/0001_initial_schema.py)
│   ├── tests/                   pytest suite (SQLite + fake GitHub, no network)
│   ├── scripts/export_openapi.py
│   ├── Dockerfile, docker-entrypoint.sh, pyproject.toml, alembic.ini
├── worker/                      README — worker code is backend/app/worker.py
├── docs/                        this file, api-contract-notes.md, openapi.json (generated)
├── .github/workflows/backend-ci.yml
├── compose.yaml
├── .env.example
└── README.md
```

## Milestones

| # | Scope | Status |
|---|-------|--------|
| 1 | Health, register/list repos, list runs, fetch logs; DB + migrations; tests; Compose; CI | **Done** (race + error-visibility fixes 2026-11-07) |
| 2 | `POST /runs/{id}/analyze`, `GET /analyses/{id}`, Redis queue, worker, AI providers | **Done** |
| 3 | User authentication + repository-level authorization (required before public deployment) | Not started |
