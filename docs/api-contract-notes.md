# API contract notes (for the frontend)

The shapes in the shared contract are implemented exactly. This file documents the details
the contract left open, plus two **additive** extensions. Nothing here changes an existing
field or response shape.

## Proposed additions (backwards compatible)

1. `GET /api/v1/repositories/{repository_id}/runs` accepts optional query parameters:
   * `conclusion` — one of `failure`, `success`, `cancelled`, `timed_out`, `action_required`,
     `skipped`, `neutral`. Use `?conclusion=failure` for the "pick a failed run" screen.
   * `limit` — 1–100, default 20.
   Without parameters the endpoint behaves as the contract describes.
2. Every error uses `{"detail": "<string>"}` — **including 422 validation errors**, where
   FastAPI would normally return a list. The frontend can always show `detail` as text.

## Status codes

| Endpoint | Success | Errors |
|---|---|---|
| `POST /repositories` | **201** | 404 not on GitHub / token can't see it · 409 already registered (case-insensitive) · 422 invalid owner/name |
| `GET /repositories` | 200 | — |
| `GET /repositories/{id}/runs` | 200 | 404 repository not registered · 422 bad query |
| `GET /runs/{run_id}/logs` | 200 | 404 run unknown · 409 run not completed yet · 410 logs expired on GitHub |
| `POST /runs/{run_id}/analyze` | **202** | 404 run unknown · 409 run not completed, or it concluded `success`/`skipped`/`neutral` · 503 Redis unavailable |
| `GET /analyses/{analysis_id}` | 200 | 404 analysis not found |
| any GitHub-backed endpoint | | 502 GitHub rejected/failed · 503 token missing or rate limited · 504 GitHub timeout |
| any endpoint | | 500 unexpected server error — still `{"detail": ...}` and still CORS-readable |

## Behaviour details

* **Runs are fetched live** from GitHub on every `GET .../runs` call and stored. The list is
  newest first.
* **Logs need the run to have been listed first.** `run_id` alone doesn't say which repository
  it belongs to, so DevPilot looks it up in its own table. In the normal UI flow (repo → runs →
  pick a run) this is always true.
* **`created_at`** is when the run was created on GitHub, always UTC with a `Z` suffix.
* **`branch`** and **`conclusion`** may be `null` (e.g. a run still in progress has no
  conclusion).
* **Log format:** one section per job, headed `===== <job name> =====`. GitHub's per-line
  timestamps and terminal colour codes are removed. Recognizable secrets are replaced with
  `[REDACTED]`.
* **`truncated: true`** means the start of the log was dropped; the first line is then
  `[... N earlier characters omitted ...]`. The end is kept because failures are reported last.
  Limit: `LOG_MAX_CHARS` (default 200 000).
* `repositories.id` is DevPilot's own id; `runs[].id` is **GitHub's** run id.

## Analyses

* `POST /runs/{run_id}/analyze` returns `202` with `{analysis_id, run_id, status: "queued"}`
  exactly as in the contract. If an analysis of the same run is already `queued` or `running`,
  that analysis is returned instead of starting a second (paid) AI call.
* Poll `GET /analyses/{analysis_id}` (every 2–3 s is plenty). `result` is `null` until
  `status` is `completed`; `error` is a readable message only when `status` is `failed`.
* An analysis that waits for a worker longer than `ANALYSIS_QUEUE_TIMEOUT_SECONDS` (default
  10 min), or runs longer than the job timeout, is reported as `failed` with an explanation,
  so the UI never spins forever. Requesting the analysis again starts a fresh one.
* `confidence` is `low` | `medium` | `high` — the model's own judgement, not a probability.
  It is forced to `low` when none of the model's evidence quotes appear in the log.
* `evidence` contains only lines that really occur in the (redacted) log, so the UI can
  highlight them in the log view.
* Each list has at most 5 items; items are at most 500 characters.
* With `AI_PROVIDER=heuristic` (the default without an API key) no AI model is used: the
  summary starts with "Offline heuristic analysis (no AI model configured)" and confidence is
  always `low`.

## Frontend note (not changed by the backend team)

`frontend/src/api.ts` types `WorkflowRun.branch` as `string`, but GitHub can return no branch
(the API then sends `null`, as documented above). Suggest `branch: string | null`.
